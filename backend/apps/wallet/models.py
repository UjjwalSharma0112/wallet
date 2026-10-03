from mongoengine import IntField, ObjectIdField, StringField

from apps.shared.documents import TimestampedDocument

from .constants import CURRENCY, ENTRY_DIRECTIONS, ENTRY_KINDS


class Wallet(TimestampedDocument):
    meta = {
        "collection": "wallets",
        "indexes": [{"fields": ["user_id"], "unique": True, "name": "user_id_unique_idx"}],
    }

    user_id = ObjectIdField(db_field="userId", required=True)
    currency = StringField(required=True, choices=[CURRENCY], default=CURRENCY)
    available_balance_minor = IntField(db_field="availableBalanceMinor", required=True, min_value=0, default=0)


class LedgerEntry(TimestampedDocument):
    """Append-only record of a single balance movement on one wallet.

    A financial operation is represented by one entry per participating wallet that
    shares a `transaction_id`. Entries are never updated or deleted, so a later
    correction is expressed as a compensating entry rather than a rewrite.
    """

    meta = {
        "collection": "ledgerentries",
        "indexes": [
            {"fields": ["wallet_id", "-created_at", "-id"], "name": "wallet_history_idx"},
            {"fields": ["user_id"], "name": "user_id_idx"},
            {"fields": ["transaction_id"], "name": "transaction_id_idx"},
        ],
    }

    wallet_id = ObjectIdField(db_field="walletId", required=True)
    user_id = ObjectIdField(db_field="userId", required=True)
    transaction_id = ObjectIdField(db_field="transactionId", required=True)
    kind = StringField(required=True, choices=ENTRY_KINDS)
    direction = StringField(required=True, choices=ENTRY_DIRECTIONS)
    amount_minor = IntField(db_field="amountMinor", required=True, min_value=1)
    balance_after_minor = IntField(db_field="balanceAfterMinor", required=True, min_value=0)
