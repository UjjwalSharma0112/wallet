"""The vocabulary of a money request: its states, its sides, and what it may be asked."""

from apps.transfers.constants import MONEY_REQUEST_KEY_PREFIX

# A request is written pending and is answered exactly once.
PENDING_STATE = "PENDING"
# The approver has claimed it and the transfer engine is running. It is not terminal: the
# engine refuses before any money moves, and a refusal hands the request back so it can be
# answered once there is money to answer it with.
APPROVING_STATE = "APPROVING"
APPROVED_STATE = "APPROVED"
DECLINED_STATE = "DECLINED"
EXPIRED_STATE = "EXPIRED"
REQUEST_STATES = [PENDING_STATE, APPROVING_STATE, APPROVED_STATE, DECLINED_STATE, EXPIRED_STATE]

# The states a request never leaves, and the states that may still be claimed.
TERMINAL_STATES = [APPROVED_STATE, DECLINED_STATE, EXPIRED_STATE]

# Which side of the request the signed-in account is on. The one asking is the requester;
# the one being asked is the approver, and is the only account that may answer.
REQUESTER_ROLE = "REQUESTER"
APPROVER_ROLE = "APPROVER"
REQUEST_ROLES = [REQUESTER_ROLE, APPROVER_ROLE]

# The idempotency key an approval spends, derived from the request rather than from the
# caller. The prefix is the one the transfers app reserves for itself, which is why it is
# declared there rather than here.
APPROVAL_KEY_PREFIX = MONEY_REQUEST_KEY_PREFIX

# A request can be answered for a week. The window is the server's to set, so no client can
# hold a request open or close it early.
REQUEST_TTL_DAYS = 7

# A claim older than this is assumed to belong to an approval that is no longer running.
# The bound is the same one transfers use for their own recovery, and for the same reason:
# an approval claims the request and writes its transfer immediately, so a claim this old
# with nothing behind it belongs to a request that is gone.
APPROVAL_CLAIM_GRACE_SECONDS = 60

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

REQUEST_NOT_FOUND_CODE = "REQUEST_NOT_FOUND"
REQUEST_NOT_FOUND_MESSAGE = "That money request was not found."

# A request that belongs to two accounts says nothing about which of them may answer it,
# and a caller who may not answer it is told so without being told whether it exists.
REQUEST_ACTION_NOT_ALLOWED_CODE = "REQUEST_ACTION_NOT_ALLOWED"
REQUEST_ACTION_NOT_ALLOWED_MESSAGE = "Only the account being asked can answer this request."

REQUEST_ALREADY_APPROVED_CODE = "REQUEST_ALREADY_APPROVED"
REQUEST_ALREADY_APPROVED_MESSAGE = "This request has already been approved."

REQUEST_ALREADY_DECLINED_CODE = "REQUEST_ALREADY_DECLINED"
REQUEST_ALREADY_DECLINED_MESSAGE = "This request has already been declined."

REQUEST_EXPIRED_CODE = "REQUEST_EXPIRED"
REQUEST_EXPIRED_MESSAGE = "This request has expired and can no longer be answered."

# An approval that has claimed the request and whose transfer has not settled yet. Saying so
# is better than refusing for a reason the caller could do nothing about, and it is the only
# answer that is true while another approval is still working.
REQUEST_APPROVAL_IN_PROGRESS_CODE = "REQUEST_APPROVAL_IN_PROGRESS"
REQUEST_APPROVAL_IN_PROGRESS_MESSAGE = "This request is being approved right now. Try again in a moment."
