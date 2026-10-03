from bson import ObjectId
from pymongo import ReturnDocument

from apps.shared.mongo import collection, to_dict
from apps.shared.validation import is_object_id

from .constants import (
    APPROVED_STATE,
    APPROVER_ROLE,
    APPROVING_STATE,
    DECLINED_STATE,
    EXPIRED_STATE,
    PENDING_STATE,
    REQUESTER_ROLE,
)
from .models import MoneyRequest

HISTORY_SORT = ["-created_at", "-id"]


def as_object_id(value):
    """An id as the database holds it.

    The raw updates below write documents the driver never inspects a field definition of,
    so an id given as a string would be stored as a string and stop matching the ids already
    in the collection. Converting here means a caller may pass whichever form it has.
    """
    return value if isinstance(value, ObjectId) else ObjectId(value)


def create_request(values):
    return to_dict(MoneyRequest(**values).save())


def find_request(request_id):
    """The request with that id, if there is one.

    The id is checked before it reaches the driver, because a path segment that is not an id
    at all is a request that does not exist and has to be answered as one rather than raised.
    An id that has already been read out of the database is not checked again, because it is
    known to be one.
    """
    if isinstance(request_id, str) and not is_object_id(request_id):
        return None

    return MoneyRequest.objects(__raw__={"_id": as_object_id(request_id)}).as_pymongo().first()


def conditions_for_account(account_id, role, state):
    """The requests one account is a party to, narrowed by the filters a caller asked for.

    A request is visible to both of the accounts it names and to nobody else. With no side
    asked for, both are included; asking for one narrows the list to that side rather than
    choosing between them, so a caller cannot see somebody else's request by naming a role.
    """
    owner = as_object_id(account_id)

    if role == REQUESTER_ROLE:
        mine = {"requesterUserId": owner}
    elif role == APPROVER_ROLE:
        mine = {"approverUserId": owner}
    else:
        mine = {"$or": [{"requesterUserId": owner}, {"approverUserId": owner}]}

    return mine if not state else {**mine, "state": state}


def list_for_account(account_id, role, state, limit, offset):
    conditions = conditions_for_account(account_id, role, state)

    return list(MoneyRequest.objects(__raw__=conditions).order_by(*HISTORY_SORT).skip(offset).limit(limit).as_pymongo())


def count_for_account(account_id, role, state):
    return MoneyRequest.objects(__raw__=conditions_for_account(account_id, role, state)).count()


def expire_for_account(account_id, moment):
    """Expire every request of this account that is past its time, in a single write.

    Scoped to this account on both sides of the request, so reading one person's requests can
    never expire anybody else's. The condition is on the request's own stored `expiresAt`, so
    the value that expires a request is the same value that decides whether it can still be
    claimed.
    """
    owner = as_object_id(account_id)
    conditions = {
        "$or": [{"requesterUserId": owner}, {"approverUserId": owner}],
        "state": PENDING_STATE,
        "expiresAt": {"$lte": moment},
    }

    return collection(MoneyRequest).update_many(conditions, expire_changes(moment))


def expire_request(request_id, moment):
    """Expire one request, and only while it is still pending and past its time."""
    conditions = {"_id": as_object_id(request_id), "state": PENDING_STATE, "expiresAt": {"$lte": moment}}

    return collection(MoneyRequest).update_one(conditions, expire_changes(moment))


def expire_changes(moment):
    return {"$set": {"state": EXPIRED_STATE, "resolvedAt": moment, "updatedAt": moment}}


def claim(request_id, moment, claimed_state):
    """Take one pending, unexpired request out of circulation for a single action.

    This conditional update is the whole of the feature's concurrency control. Whichever of
    approve, decline or expire matches first owns the request, because every one of them
    matches on `state` being `PENDING` and on the request's own stored `expiresAt`, and
    because nobody reads the document before writing it. The loser of a race is answered from
    the state the winner left behind, which is why this returns a document or nothing at all.
    """
    conditions = {"_id": as_object_id(request_id), "state": PENDING_STATE, "expiresAt": {"$gt": moment}}

    if claimed_state == APPROVING_STATE:
        # Claiming is not answering. The request stays claimed while the engine works, and
        # this stamp is what tells a claim nobody is working on from one that is running.
        changes = {"state": APPROVING_STATE, "approvalStartedAt": moment, "updatedAt": moment}
    else:
        changes = {"state": claimed_state, "resolvedAt": moment, "updatedAt": moment}

    return collection(MoneyRequest).find_one_and_update(
        conditions, {"$set": changes}, return_document=ReturnDocument.AFTER
    )


def settle_approved(request_id, transfer_id, moment):
    """Record the transfer that paid a request, and only while it may still be paid.

    The condition allows the two states an unanswered request can be in and refuses every
    terminal one, so a request that has already been answered is never rewritten. Both are
    allowed because the caller's own evidence is the transfer: a request whose key is held by
    a completed transfer has been paid, whatever the document itself happens to say.
    """
    conditions = {"_id": as_object_id(request_id), "state": {"$in": [APPROVING_STATE, PENDING_STATE]}}
    changes = {
        "$set": {
            "state": APPROVED_STATE,
            "transferId": as_object_id(transfer_id),
            "resolvedAt": moment,
            "updatedAt": moment,
        },
        "$unset": {"approvalStartedAt": ""},
    }

    return collection(MoneyRequest).find_one_and_update(conditions, changes, return_document=ReturnDocument.AFTER)


def release_claim(request_id, moment):
    """Hand a claim back so the request can be answered again, and only while it is held.

    Used when the transfer engine refused before any money moved. The condition is on the
    claim itself, so this can never reopen a request that has been answered, and the request
    goes back to exactly the state it was in with the stamp that marked the claim removed.
    """
    conditions = {"_id": as_object_id(request_id), "state": APPROVING_STATE}
    changes = {"$set": {"state": PENDING_STATE, "updatedAt": moment}, "$unset": {"approvalStartedAt": ""}}

    return collection(MoneyRequest).find_one_and_update(conditions, changes, return_document=ReturnDocument.AFTER)
