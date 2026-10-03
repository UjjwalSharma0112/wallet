from mongoengine import DateTimeField, IntField, ObjectIdField, StringField

from apps.shared.documents import TimestampedDocument
from apps.transfers.constants import MAX_NOTE_LENGTH

from .constants import PENDING_STATE, REQUEST_STATES


class MoneyRequest(TimestampedDocument):
    """One account asking another to send money.

    The document is written before anything can be answered to it, so an interrupted attempt
    always leaves evidence of what it was doing. `transfer_id` is set only once the transfer
    engine has reported that the money moved, and in the same write that sets the terminal
    state, which is what keeps an answered request from claiming a payment that never
    happened. A request that has reached `APPROVED`, `DECLINED` or `EXPIRED` is never
    rewritten.
    """

    meta = {
        "collection": "moneyrequests",
        "indexes": [
            {"fields": ["requester_user_id", "-created_at", "-id"], "name": "requester_history_idx"},
            {"fields": ["approver_user_id", "-created_at", "-id"], "name": "approver_history_idx"},
        ],
    }

    requester_user_id = ObjectIdField(db_field="requesterUserId", required=True)
    approver_user_id = ObjectIdField(db_field="approverUserId", required=True)
    amount_minor = IntField(db_field="amountMinor", required=True, min_value=1)
    note = StringField(max_length=MAX_NOTE_LENGTH)
    state = StringField(required=True, choices=REQUEST_STATES, default=PENDING_STATE)
    # When the request stops being answerable, whatever it was never answered by.
    expires_at = DateTimeField(db_field="expiresAt", required=True)
    # When the approver claimed it. Present only while the state is `APPROVING`, and the
    # only thing that tells a claim nobody is working on from one that is still running.
    approval_started_at = DateTimeField(db_field="approvalStartedAt")
    # The transfer that paid this request, set with `APPROVED` and never before, so the two
    # cannot come apart. Its `_id` is also the transaction id of the ledger entries it
    # wrote, which is how one identifier ties a request to its money.
    transfer_id = ObjectIdField(db_field="transferId")
    # When the request was answered — approved, declined or expired. `state` says which.
    resolved_at = DateTimeField(db_field="resolvedAt")
