from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument

from apps.shared.mongo import collection, to_dict
from apps.shared.validation import is_object_id

from .constants import (
    COMPLETED_STATE,
    FAILED_STATE,
    PENDING_STATE,
)
from .models import Refund

HISTORY_SORT = ["-created_at", "-id"]


def as_object_id(value):
    """An id as the database holds it, for raw writes the driver will not coerce."""
    return value if isinstance(value, ObjectId) else ObjectId(value)


def create_refund(values):
    return to_dict(Refund(**values).save())


def find_refund(refund_id):
    if isinstance(refund_id, str) and not is_object_id(refund_id):
        return None

    return Refund.objects(__raw__={"_id": as_object_id(refund_id)}).as_pymongo().first()


def find_by_key(initiator_user_id, idempotency_key):
    """The refund an initiator has already spent this idempotency key on, if any."""
    if not idempotency_key:
        return None

    conditions = {"initiatorUserId": ObjectId(initiator_user_id), "idempotencyKey": idempotency_key}

    return Refund.objects(__raw__=conditions).as_pymongo().first()


def list_for_initiator(initiator_user_id, limit, offset):
    conditions = {"initiatorUserId": ObjectId(initiator_user_id)}

    return list(Refund.objects(__raw__=conditions).order_by(*HISTORY_SORT).skip(offset).limit(limit).as_pymongo())


def count_for_initiator(initiator_user_id):
    return Refund.objects(__raw__={"initiatorUserId": ObjectId(initiator_user_id)}).count()


def settle(refund_id, transfer_id, moment):
    """Record the transfer that paid a refund, and only while it may still be settled.

    The condition is the pending state itself, so a settled or failed refund is never
    rewritten, and the refund's own row is the only evidence the other path may have left
    behind.
    """
    conditions = {"_id": as_object_id(refund_id), "state": PENDING_STATE}
    changes = {
        "state": COMPLETED_STATE,
        "transferId": as_object_id(transfer_id),
        "completedAt": moment,
        "updatedAt": moment,
    }

    return collection(Refund).find_one_and_update(conditions, {"$set": changes}, return_document=ReturnDocument.AFTER)


def fail(refund_id, failure_code, moment):
    """Fail a refund that never paid, and hand both of its keys back, in one write.

    The engine key is spent on a transfer, so the caller attempts that transfer at most
    once per refund; the client key is also released, because a refund that paid nothing is
    not the operation the caller named and a genuine retry deserves a fresh attempt. Both
    happen only while the refund is still pending, so a paid refund can never be unwound by
    this write.
    """
    conditions = {"_id": as_object_id(refund_id), "state": PENDING_STATE}
    changes = {
        "state": FAILED_STATE,
        "failureCode": failure_code,
        "updatedAt": moment,
    }

    return collection(Refund).find_one_and_update(
        conditions,
        {"$set": changes, "$unset": {"idempotencyKey": "", "idempotencyFingerprint": ""}},
        return_document=ReturnDocument.AFTER,
    )
