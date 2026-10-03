import re

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

# A sender's idempotency key names one operation. The server never interprets it: it only
# checks that it can be stored and compared, and that a sender cannot spend it twice.
MAX_IDEMPOTENCY_KEY_LENGTH = 64
MIN_IDEMPOTENCY_KEY_LENGTH = 8
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")

# The version prefix of the canonical request a fingerprint is taken over, so a later change
# to what counts as the same request cannot silently reclassify a key already in use.
FINGERPRINT_VERSION = "v1"

# A namespace the server spends on its own behalf and a client may not. An approval derives
# its key from the request it is approving, so that retrying an approval is one transfer
# rather than several. A client that could spend the same key would be able to collide with
# it deliberately, so the prefix is declared here, where keys are understood, and refused
# from a request that carries it. The key itself never passes through this check: it is
# handed to the engine by the feature that derived it.
MONEY_REQUEST_KEY_PREFIX = "money-request:"

# A refund's key for its engine call is derived from the refund, the same way an
# approval's is derived from the request it is approving: one refund, one movement,
# however many times the caller retries. Also reserved, also refused from clients.
REFUND_KEY_PREFIX = "refund:"

# One sender, one key, one transfer. The index is partial so it only covers keys that were
# actually sent, and it is created by the model because MongoEngine cannot express it.
SENDER_IDEMPOTENCY_INDEX = "sender_idempotency_unique_idx"

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

# A key that is sent has to be storable and comparable, so its format is checked before
# anything is written. A key that is not sent is not an error: the route has always honoured
# a request without one.
IDEMPOTENCY_KEY_INVALID_CODE = "IDEMPOTENCY_KEY_INVALID"
IDEMPOTENCY_KEY_INVALID_MESSAGE = (
    "The Idempotency-Key header must be 8 to 64 characters of letters, numbers, dots, "
    "underscores, colons or dashes."
)

# Refused without saying what the key was spent on: the sender knows what they sent, and
# the original request is not theirs to be told back.
IDEMPOTENCY_KEY_REUSED_CODE = "IDEMPOTENCY_KEY_REUSED"
IDEMPOTENCY_KEY_REUSED_MESSAGE = (
    "That idempotency key has already been used for a different transfer. Use a new key for a new transfer."
)

# A failed transfer gives its key back, so this is only reached if a key is somehow still
# attached to one. It is stated rather than answered with a result that never happened.
TRANSFER_ALREADY_FAILED_CODE = "TRANSFER_ALREADY_FAILED"
TRANSFER_ALREADY_FAILED_MESSAGE = "That transfer already failed. Send it again with a new idempotency key."

# A well-formed key in the namespace the server reserves for itself. It is refused for its
# own sake rather than as a format problem, so the sender is told why rather than what a key
# has to look like.
IDEMPOTENCY_KEY_RESERVED_CODE = "IDEMPOTENCY_KEY_RESERVED"
IDEMPOTENCY_KEY_RESERVED_MESSAGE = (
    "Idempotency keys starting with 'money-request:' or 'refund:' are reserved by the wallet and cannot be used."
)
