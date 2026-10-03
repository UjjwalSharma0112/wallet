from mongoengine import DateTimeField, IntField, ObjectIdField, StringField

from apps.shared.documents import TimestampedDocument

from .constants import FAILED_STATE, MAX_NOTE_LENGTH, PENDING_STATE, TRANSFER_FAILURE_CODES, TRANSFER_STATES


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
