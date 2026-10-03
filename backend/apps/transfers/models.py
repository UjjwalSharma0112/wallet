from mongoengine import DateTimeField, IntField, ObjectIdField, StringField
from pymongo import ASCENDING

from apps.shared.documents import TimestampedDocument, ensure_index

from .constants import (
    FAILED_STATE,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_NOTE_LENGTH,
    PENDING_STATE,
    SENDER_IDEMPOTENCY_INDEX,
    TRANSFER_FAILURE_CODES,
    TRANSFER_STATES,
)


class Transfer(TimestampedDocument):
    """Record of one attempted movement of money between two accounts.

    The document is written before any money moves, so an attempt that is interrupted
    always leaves evidence of what it intended. `_id` is the transaction id shared by the
    ledger entries it produces, which makes this document the single record of the
    operation. A transfer that reaches `COMPLETED` or `FAILED` is never rewritten.
    """

    meta = {
        "collection": "transfers",
        "indexes": [
            {"fields": ["sender_user_id", "-created_at", "-id"], "name": "sender_history_idx"},
            {"fields": ["recipient_user_id", "-created_at", "-id"], "name": "recipient_history_idx"},
            # Bounds the sweep that completes transfers an interrupted request left behind.
            {"fields": ["state", "created_at"], "name": "in_flight_idx"},
        ],
    }

    sender_user_id = ObjectIdField(db_field="senderUserId", required=True)
    sender_wallet_id = ObjectIdField(db_field="senderWalletId", required=True)
    recipient_user_id = ObjectIdField(db_field="recipientUserId", required=True)
    recipient_wallet_id = ObjectIdField(db_field="recipientWalletId", required=True)
    amount_minor = IntField(db_field="amountMinor", required=True, min_value=1)
    state = StringField(required=True, choices=TRANSFER_STATES, default=PENDING_STATE)
    failure_code = StringField(db_field="failureCode", choices=TRANSFER_FAILURE_CODES)
    note = StringField(max_length=MAX_NOTE_LENGTH)
    completed_at = DateTimeField(db_field="completedAt")
    # The sender's key for this operation, when one was sent. It is held here rather than in
    # a document of its own so that claiming the key and recording the transfer are the same
    # write, and so that a key's owner is simply the owner of the transfer that holds it.
    # A transfer made without a key leaves both fields absent.
    idempotency_key = StringField(db_field="idempotencyKey", max_length=MAX_IDEMPOTENCY_KEY_LENGTH)
    # What that key was spent on, compared before any result is replayed. Cleared together
    # with the key if the transfer fails, so a released key leaves nothing behind.
    idempotency_fingerprint = StringField(db_field="idempotencyFingerprint")
    # When a failed transfer gave its key back. Recorded with the failure so that a key that
    # is free again is explained rather than a mystery.
    idempotency_key_released_at = DateTimeField(db_field="idempotencyKeyReleasedAt")


def ensure_transfer_indexes():
    """Create every index transfers need, including the one MongoEngine cannot declare.

    `sender_idempotency_unique_idx` is deliberately absent from the model's index list:
    MongoEngine would create it without the partial expression, which would then limit a
    sender to one keyless transfer for the life of the database. Only keys that were actually
    sent take part in it.
    """
    Transfer.ensure_indexes()

    ensure_index(
        Transfer,
        SENDER_IDEMPOTENCY_INDEX,
        [("senderUserId", ASCENDING), ("idempotencyKey", ASCENDING)],
        unique=True,
        partialFilterExpression={"idempotencyKey": {"$type": "string"}},
    )
