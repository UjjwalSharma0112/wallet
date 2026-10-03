"""The vocabulary of a refund: its states and why one may stop."""

from apps.transfers.constants import REFUND_KEY_PREFIX

# A refund is written pending, is settled against the transfer the engine produced, and
# is otherwise never rewritten.
PENDING_STATE = "PENDING"
COMPLETED_STATE = "COMPLETED"
FAILED_STATE = "FAILED"
REFUND_STATES = [PENDING_STATE, COMPLETED_STATE, FAILED_STATE]

# The engine key a refund spends is derived from the refund itself, the same shape an
# approval uses for a request, so a retry of one costs the engine one look-up rather than
# a second movement.
REFUND_ENGINE_KEY_PREFIX = REFUND_KEY_PREFIX

# A pending refund stops being workable for the same reason an approval claim stops: the
# call that created it was interrupted rather than answered, so the state it left behind
# belongs to a request that is gone, and a fresh one with the same key may take its place.
REFUND_CLAIM_GRACE_SECONDS = 60

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

ORIGINAL_TRANSFER_NOT_FOUND_CODE = "ORIGINAL_TRANSFER_NOT_FOUND"
ORIGINAL_TRANSFER_NOT_FOUND_MESSAGE = "That transfer was not found."

# A refund belongs to the account that received the money. Naming anyone else refs a
# refund the caller cannot give, and is refused without saying whether it exists.
REFUND_ACTION_NOT_ALLOWED_CODE = "REFUND_ACTION_NOT_ALLOWED"
REFUND_ACTION_NOT_ALLOWED_MESSAGE = "Only the account that received the original transfer can refund it."

# Only a transfer that completed describes money that can be given back.
TRANSFER_NOT_REFUNDABLE_CODE = "TRANSFER_NOT_REFUNDABLE"
TRANSFER_NOT_REFUNDABLE_MESSAGE = "Only a completed transfer can be refunded."

# A refund transfer gives money back along a transfer; it cannot itself be the original of
# another refund, because a refund of a refund would owe money neither side is holding.
REFUND_OF_REFUND_CODE = "REFUND_OF_REFUND"
REFUND_OF_REFUND_MESSAGE = "A refund transfer cannot itself be refunded."

REFUND_LIMIT_EXCEEDED_CODE = "REFUND_LIMIT_EXCEEDED"
REFUND_LIMIT_EXCEEDED_MESSAGE = "That refund would leave more refunded than the original transfer allowed."

REFUND_IN_PROGRESS_CODE = "REFUND_IN_PROGRESS"
REFUND_IN_PROGRESS_MESSAGE = "That refund is being completed right now. Try again in a moment."
