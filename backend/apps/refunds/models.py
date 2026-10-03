from mongoengine import DateTimeField, IntField, ObjectIdField, StringField
from pymongo import ASCENDING

from apps.shared.documents import TimestampedDocument, ensure_index
from apps.transfers.constants import MAX_NOTE_LENGTH

from .constants import COMPLETED_STATE, FAILED_STATE, PENDING_STATE, REFUND_STATES


class Refund(TimestampedDocument):
    """The promise to give some of a completed transfer back, and its outcome.

    The row is written before the amount is claimed, so an interrupted attempt always
    leaves evidence of what it intended. `transfer_id` names the refund's own Transfer —
    the payment that gives the money back — and is set only once the engine has reported
    that money moved, which is what keeps a refund from claiming a payment that never
    happened. A refund that has reached `COMPLETED` or `FAILED` is never rewritten.
    """

    meta = {
        "collection": "refunds",
        "indexes": [
            {"fields": ["original_transfer_id", "-created_at", "-id"], "name": "original_transfer_idx"},
            {"fields": ["initiator_user_id", "-created_at", "-id"], "name": "initiator_history_idx"},
        ],
    }

    original_transfer_id = ObjectIdField(db_field="originalTransferId", required=True)
    initiator_user_id = ObjectIdField(db_field="initiatorUserId", required=True)
    amount_minor = IntField(db_field="amountMinor", required=True, min_value=1)
    note = StringField(max_length=MAX_NOTE_LENGTH)
    state = StringField(required=True, choices=REFUND_STATES, default=PENDING_STATE)
    # The transfer that paid the refund, set with `COMPLETED` and never before, so the two
    # cannot come apart. Its `_id` is the transaction id of the ledger entries the refund
    # wrote.
    transfer_id = ObjectIdField(db_field="transferId")
    failure_code = StringField(db_field="failureCode")
    completed_at = DateTimeField(db_field="completedAt")
    # The caller's key for this operation, held here rather than on a document of its own,
    # so the claim on the key and the record of the refund cannot come apart.
    idempotency_key = StringField(db_field="idempotencyKey", max_length=64)
    # What that key was spent on, compared before any result is replayed.
    idempotency_fingerprint = StringField(db_field="idempotencyFingerprint")


REFUND_INITIATOR_IDEMPOTENCY_INDEX = "initiator_idempotency_unique_idx"


def ensure_refund_indexes():
    """Create the indexes a refund needs, including the one MongoEngine cannot declare."""
    Refund.ensure_indexes()

    ensure_index(
        Refund,
        REFUND_INITIATOR_IDEMPOTENCY_INDEX,
        [("initiatorUserId", ASCENDING), ("idempotencyKey", ASCENDING)],
        unique=True,
        partialFilterExpression={"idempotencyKey": {"$type": "string"}},
    )
