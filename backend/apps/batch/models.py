from mongoengine import DateTimeField, DictField, IntField, ListField, ObjectIdField, StringField

from apps.shared.documents import TimestampedDocument
from apps.transfers.constants import MAX_NOTE_LENGTH

from .constants import BATCH_STATES, ITEM_STATES, PENDING_STATE

# One payment in a batch, written before it is run and never rewritten except to record how
# it turned out. `position` is the item's place in the batch as given, and it is what the
# engine key is derived from, so it is fixed at creation and is part of what the key means.
ITEM_FIELDS = {
    "position": IntField(required=True, min_value=0),
    "recipient_email": StringField(db_field="recipientEmail", required=True),
    "amount_minor": IntField(db_field="amountMinor", required=True, min_value=1),
    "note": StringField(max_length=MAX_NOTE_LENGTH),
    "state": StringField(required=True, choices=ITEM_STATES, default=PENDING_STATE),
    # Why this item could not be paid, as the engine's own code, so a batch is refused for
    # exactly the reason a single transfer is.
    "failure_code": StringField(db_field="failureCode"),
    "failure_message": StringField(db_field="failureMessage"),
    # The transfer that paid this item, set only once the engine has reported the money
    # moved. It is never set for an item that did not move money, so an item cannot claim a
    # payment that did not happen.
    "transfer_id": ObjectIdField(db_field="transferId"),
}


class Batch(TimestampedDocument):
    """One account's list of payments, and how each of them turned out.

    The items live inside this document rather than beside it. That is deliberate: an item's
    outcome is written by one conditional update against this one document, so an item can
    never be recorded as paid while the batch disagrees, and there is no second place for the
    state of a payment to live. A batch is bounded by `MAX_BATCH_ITEMS`, so the list cannot
    grow towards the document size limit.

    The batch holds no counters of its own. How many payments went through, how much was
    sent, and which state the batch is in are all read off the items, because a counter kept
    beside them would be a second answer to the same question and could disagree with them.
    """

    meta = {
        "collection": "batches",
        "indexes": [{"fields": ["owner_user_id", "-created_at", "-id"], "name": "owner_history_idx"}],
    }

    owner_user_id = ObjectIdField(db_field="ownerUserId", required=True)
    state = StringField(required=True, choices=BATCH_STATES, default=PENDING_STATE)
    items = ListField(DictField())
    # When a processing run claimed the batch. Present only while the state is `PROCESSING`,
    # and the only thing that tells a claim nobody is working on from one that is running.
    processing_started_at = DateTimeField(db_field="processingStartedAt")
    # When the last processing run finished. `state` says how it turned out.
    completed_at = DateTimeField(db_field="completedAt")