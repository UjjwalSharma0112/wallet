PENDING_STATE = "PENDING"
COMPLETED_STATE = "COMPLETED"
FAILED_STATE = "FAILED"
TRANSFER_STATES = [PENDING_STATE, COMPLETED_STATE, FAILED_STATE]

INSUFFICIENT_FUNDS_CODE = "INSUFFICIENT_FUNDS"
RECIPIENT_UNAVAILABLE_CODE = "RECIPIENT_UNAVAILABLE"
TRANSFER_FAILURE_CODES = [INSUFFICIENT_FUNDS_CODE, RECIPIENT_UNAVAILABLE_CODE]

# A single transfer is capped well above any balance the product demonstrates. The cap
# bounds input rather than funds: affordability is decided by the conditional debit.
MAX_TRANSFER_MINOR = 10_000_000
MAX_NOTE_LENGTH = 140

# Transfer attempts an interrupted request may have left behind are completed before a
# new one starts. The bound keeps a large backlog from stalling a request.
MAX_RECOVERY_PER_ATTEMPT = 25

# A transfer younger than this is assumed to belong to a request that is still running.
# Recovery only touches older ones, so it never competes with the request that created
# the transfer it is completing. Without this, recovery could finish a transfer while its
# own request was midway through, and the two would each apply the same movement.
RECOVERY_AGE_SECONDS = 60

RECIPIENT_ROLE = "RECIPIENT"
SENDER_ROLE = "SENDER"

RECIPIENT_NOT_FOUND_MESSAGE = "No active account with that email can receive money."
SELF_TRANSFER_MESSAGE = "Enter an email address that belongs to someone else."
INSUFFICIENT_FUNDS_MESSAGE = "This wallet does not have enough available balance for that amount."
MOVEMENT_MISSING_MESSAGE = "The recorded balance movement for this transfer could not be found."
