from datetime import datetime, timedelta, timezone

from bson import ObjectId
from pymongo import ReturnDocument

from apps.shared.mongo import collection, to_dict
from apps.shared.validation import is_object_id

from .constants import (
    COMPLETED_STATE,
    FAILED_STATE,
    ITEM_COMPLETED_STATE,
    PARTIALLY_COMPLETED_STATE,
    PENDING_STATE,
    PROCESSABLE_STATES,
    PROCESSING_CLAIM_GRACE_SECONDS,
    PROCESSING_STATE,
)
from .models import Batch

HISTORY_SORT = ["-created_at", "-id"]


def as_object_id(value):
    """An id as the database holds it.

    The raw updates below write documents the driver never inspects a field definition of, so
    an id given as a string would be stored as a string and stop matching the ids already in
    the collection. Converting here means a caller may pass whichever form it has.
    """
    return value if isinstance(value, ObjectId) else ObjectId(value)


def as_instant(moment):
    """A moment read out of the database, comparable with the clock.

    A datetime read through the raw driver carries no offset, because BSON stores UTC
    milliseconds and nothing more, while one read through the document layer carries UTC
    explicitly. Both name the same instant, so the missing offset is filled in rather than
    taken off the value that has one.
    """
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def create_batch(values):
    return to_dict(Batch(**values).save())


def find_batch(batch_id):
    """The batch with that id, if there is one.

    The id is checked before it reaches the driver, because a path segment that is not an id
    at all is a batch that does not exist and has to be answered as one rather than raised.
    An id already read out of the database is not checked again, because it is known to be
    one.
    """
    if isinstance(batch_id, str) and not is_object_id(batch_id):
        return None

    return Batch.objects(__raw__={"_id": as_object_id(batch_id)}).as_pymongo().first()


def conditions_for_owner(owner_id, state):
    """The batches one account created, optionally narrowed to a state.

    A batch belongs to the account that created it and to nobody else, so the owner is the
    only condition there is. Asking for a state narrows the page rather than choosing between
    accounts, so a caller cannot widen it by naming something else.
    """
    mine = {"ownerUserId": as_object_id(owner_id)}

    return mine if not state else {**mine, "state": state}


def list_for_owner(owner_id, state, limit, offset):
    conditions = conditions_for_owner(owner_id, state)

    return list(Batch.objects(__raw__=conditions).order_by(*HISTORY_SORT).skip(offset).limit(limit).as_pymongo())


def count_for_owner(owner_id, state):
    return Batch.objects(__raw__=conditions_for_owner(owner_id, state)).count()


def claim(batch_id, moment):
    """Take one processable batch out of circulation for a single processing run.

    This conditional update is the whole of the feature's concurrency control at the batch
    level. Whichever run matches first owns the batch, because the condition is on the state
    being one this feature may process from, and because nobody reads the document before
    writing it. `PROCESSING` is deliberately not among those states: a batch already claimed
    is either being worked on or was abandoned, and the two are told apart by the grace
    period in `abandoned` rather than by this update.
    """
    conditions = {"_id": as_object_id(batch_id), "state": {"$in": PROCESSABLE_STATES}}
    changes = {"$set": {"state": PROCESSING_STATE, "processingStartedAt": moment, "updatedAt": moment}}

    return collection(Batch).find_one_and_update(conditions, changes, return_document=ReturnDocument.AFTER)


def reassert_claim(batch_id, moment):
    """Keep a claimed batch claimed, for a run that found it abandoned and took it over.

    The state is part of the condition, so this cannot revive a batch that has since been
    finished by somebody else: it matches nothing then, and the caller reads the state the
    batch actually reached.
    """
    conditions = {"_id": as_object_id(batch_id), "state": PROCESSING_STATE}
    changes = {"$set": {"processingStartedAt": moment, "updatedAt": moment}}

    return collection(Batch).find_one_and_update(conditions, changes, return_document=ReturnDocument.AFTER)


def settle_item(batch_id, position, item_changes, moment):
    """Record how one item turned out, unless the item has already been paid.

    Only a `COMPLETED` item is refused a write, because only a completed item is settled for
    good: its transfer exists, and nothing may say otherwise about it. An item that failed is
    deliberately still writable, because a failed transfer gives its idempotency key back and
    so the same key is free to try that payment again — the outcome recorded now may be the
    one a later run gets, and it may be the opposite.

    Two runs racing over the same item therefore may both write it, and that is safe rather
    than merely tolerated: they spend the same derived key, so the engine settles both at the
    same transfer and there is only one answer to record. The write names the item by its
    position rather than rewriting the list, so two items being settled at the same time
    cannot overwrite each other's outcome.

    An item that is being recorded as paid has whatever it failed with taken away in the same
    write. A failed item stays writable precisely so a later run can pay it, and a payment that
    went through must not still be carrying the reason it did not go through last time — an
    item that says it was paid and, in the same breath, why it was not, describes two outcomes
    at once, and the summary is derived from the items, so it is the items that have to be
    right.
    """
    conditions = {
        "_id": as_object_id(batch_id),
        "items": {"$elemMatch": {"position": position, "state": {"$ne": ITEM_COMPLETED_STATE}}},
    }
    changes = {f"items.$.{field}": value for field, value in item_changes.items()}
    changes["updatedAt"] = moment

    write = {"$set": changes}

    if item_changes.get("transferId") is not None:
        write["$unset"] = {"items.$.failureCode": 1, "items.$.failureMessage": 1}

    return collection(Batch).find_one_and_update(conditions, write, return_document=ReturnDocument.AFTER)


def finalise(batch_id, moment):
    """Close a claimed batch out, and only while it is still the run that claimed it.

    The state is derived from the items on the document being written, in the same write that
    records it, so a batch can never claim an outcome its own items disagree with. The
    condition keeps a run that was overtaken from finishing a batch that has since been dealt
    with.
    """
    conditions = {"_id": as_object_id(batch_id), "state": PROCESSING_STATE}
    changes = [
        {"$set": {"state": derived_state(), "completedAt": moment, "updatedAt": moment}},
        {"$unset": "processingStartedAt"},
    ]

    return collection(Batch).find_one_and_update(conditions, changes, return_document=ReturnDocument.AFTER)


def release(batch_id, moment):
    """Hand a claim back without running anything else, and only while it is still held.

    Used when a batch could not be run at all, which is a refusal before the item that caused
    it — though not necessarily before the items before it, since those were paid. The state
    is therefore derived from the items rather than reset, so a run that paid part of a batch
    leaves it looking like the partly paid batch it now is, and a run that paid nothing leaves
    it pending. The condition is on the claim itself, so a batch that has since been dealt
    with is never reopened.
    """
    conditions = {"_id": as_object_id(batch_id), "state": PROCESSING_STATE}
    changes = [
        {"$set": {"state": derived_state(), "updatedAt": moment}},
        {"$unset": "processingStartedAt"},
    ]

    return collection(Batch).find_one_and_update(conditions, changes, return_document=ReturnDocument.AFTER)


def derived_state():
    """The state a batch's own items say it is in, read off the document being written.

    An aggregation expression, so it is evaluated against the items in the same write that
    records the answer rather than being stored as the answer, and against the array as it is
    being written rather than a copy of it read earlier. Both updates that set a state have to
    be pipeline updates for that reason.

    Four cases rather than three, because an item can still be pending: a run that was
    interrupted, or one that was released before reaching its last item, leaves items nobody
    has paid yet, and calling those paid would be the one answer that is certainly wrong.
    `COMPLETED` is the default because it is right for a batch whose every item is paid, which
    is the case a completed run always leaves behind.
    """
    items = "$items"
    failed = {"$size": {"$filter": {"input": items, "as": "item", "cond": {"$eq": ["$$item.state", FAILED_STATE]}}}}
    paid = {"$size": {"$filter": {"input": items, "as": "item", "cond": {"$eq": ["$$item.state", COMPLETED_STATE]}}}}

    return {
        "$switch": {
            "branches": [
                {"case": {"$eq": [failed, {"$size": items}]}, "then": FAILED_STATE},
                {"case": {"$gt": [failed, 0]}, "then": PARTIALLY_COMPLETED_STATE},
                {"case": {"$lt": [paid, {"$size": items}]}, "then": PENDING_STATE},
            ],
            "default": COMPLETED_STATE,
        }
    }


def abandoned(batch):
    """Whether a claimed batch is old enough that the run which claimed it is gone.

    Bounded exactly as transfers bound their recovery, and for the same reason: a run claims
    the batch and writes each item's outcome as it goes, so a claim this old with items still
    pending belongs to a run that was interrupted. Taking such a batch over cannot move money
    twice, because every item's key is derived and still decides what may be spent.
    """
    started = batch.get("processingStartedAt")

    if started is None:
        return False

    deadline = datetime.now(timezone.utc) - timedelta(seconds=PROCESSING_CLAIM_GRACE_SECONDS)

    return as_instant(started) <= deadline