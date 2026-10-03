from datetime import datetime, timedelta, timezone

from bson import ObjectId

from apps.shared.mongo import collection, to_dict

from .constants import COMPLETED_STATE, FAILED_STATE, PENDING_STATE, RECOVERY_AGE_SECONDS
from .models import Transfer

IN_FLIGHT_SORT = ["created_at", "id"]


def create_transfer(values):
    return to_dict(Transfer(**values).save())


def find_by_key(sender_user_id, idempotency_key):
    """The transfer a sender has already spent an idempotency key on, if there is one.

    A key released by a failed transfer is no longer held by anything, so this reports
    nothing and the key is free to be spent again. Reading this is only ever an
    optimisation: the uniqueness of the key is settled by the index, not by this query.
    """
    if not idempotency_key:
        return None

    conditions = {"senderUserId": ObjectId(sender_user_id), "idempotencyKey": idempotency_key}

    return Transfer.objects(__raw__=conditions).as_pymongo().first()


def find_transfer(transfer_id):
    return Transfer.objects(__raw__={"_id": ObjectId(transfer_id)}).as_pymongo().first()


def find_transfers_by_ids(transaction_ids):
    """Look up several transfers at once, so a history page costs one extra query."""
    if not transaction_ids:
        return {}

    rows = collection(Transfer).find({"_id": {"$in": [ObjectId(value) for value in transaction_ids]}})

    return {row["_id"]: row for row in rows}


def list_in_flight(limit):
    """Transfers left pending long enough ago that no request is still working on them."""
    stale_before = datetime.now(timezone.utc) - timedelta(seconds=RECOVERY_AGE_SECONDS)
    conditions = {"state": PENDING_STATE, "createdAt": {"$lte": stale_before}}

    return list(Transfer.objects(__raw__=conditions).order_by(*IN_FLIGHT_SORT).limit(limit).as_pymongo())


def mark_completed(transfer_id):
    """Complete a transfer only while it is still in flight, so it cannot be completed twice."""
    return Transfer.objects(__raw__={"_id": ObjectId(transfer_id), "state": PENDING_STATE}).update(
        set__state=COMPLETED_STATE, set__completed_at=datetime.now(timezone.utc)
    )


def mark_failed(transfer_id, failure_code):
    """Fail a transfer that never moved money, and hand its idempotency key back.

    A transfer that failed has not done what the key named, so the key is released in the
    same update that fails it. Both happen while the transfer is still in flight, so this
    can never release the key of a transfer that has already moved money — the only way a
    transfer reaches a terminal state from here is a refusal before its first write. A
    transfer left in `PENDING` keeps its key until recovery settles it, which is the one way
    a key can stay held.
    """
    return Transfer.objects(__raw__={"_id": ObjectId(transfer_id), "state": PENDING_STATE}).update(
        set__state=FAILED_STATE,
        set__failure_code=failure_code,
        set__idempotency_key_released_at=datetime.now(timezone.utc),
        unset__idempotency_key=1,
        unset__idempotency_fingerprint=1,
    )
