CURRENCY = "INR"

ENTRY_KINDS = ["OPENING", "TRANSFER_IN", "TRANSFER_OUT", "REFUND_IN", "REFUND_OUT"]
ENTRY_DIRECTIONS = ["CREDIT", "DEBIT"]
OPENING_KIND = "OPENING"
TRANSFER_IN_KIND = "TRANSFER_IN"
TRANSFER_OUT_KIND = "TRANSFER_OUT"
REFUND_IN_KIND = "REFUND_IN"
REFUND_OUT_KIND = "REFUND_OUT"

# Roles recorded on an in-flight balance movement. They mirror the ledger direction
# the movement will eventually be written as.
MOVEMENT_DEBIT = "DEBIT"
MOVEMENT_CREDIT = "CREDIT"

# Outcomes of trying to apply a movement to a wallet.
MOVEMENT_APPLIED = "APPLIED"
MOVEMENT_ALREADY_APPLIED = "ALREADY_APPLIED"
MOVEMENT_REFUSED = "REFUSED"

WALLET_EXISTS_MESSAGE = "This account already has a wallet."
WALLET_NOT_FOUND_MESSAGE = "This account does not have a wallet yet."

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
