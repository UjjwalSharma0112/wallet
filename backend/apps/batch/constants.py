"""The vocabulary of a batch: its states, its items', and what may stop it."""

from apps.transfers.constants import BATCH_KEY_PREFIX

# A batch is written with nothing paid and is run once per deliberate action.
PENDING_STATE = "PENDING"
# The owner has claimed it and its items are being handed to the transfer engine one at a
# time. It is not terminal: an item the engine refuses is recorded and the batch carries on,
# so this state ends in one of the three below rather than in itself.
PROCESSING_STATE = "PROCESSING"
# Every item moved its money.
COMPLETED_STATE = "COMPLETED"
# Some items moved their money and some did not. The ones that did stay done: a batch is not
# undone because one line of it could not be paid.
PARTIALLY_COMPLETED_STATE = "PARTIALLY_COMPLETED"
# No item moved any money.
FAILED_STATE = "FAILED"
BATCH_STATES = [PENDING_STATE, PROCESSING_STATE, COMPLETED_STATE, PARTIALLY_COMPLETED_STATE, FAILED_STATE]

# The states a batch may be claimed from. `PROCESSING` is the only one absent: a batch
# already claimed is either being worked on or was abandoned, and the two are told apart by
# the grace period rather than by the claim. `COMPLETED` is included because running a batch
# that paid everything is answered from the transfers that paid it and moves nothing, which is
# the same answer a caller gets by reading it and is worth being able to ask for.
PROCESSABLE_STATES = [PENDING_STATE, COMPLETED_STATE, PARTIALLY_COMPLETED_STATE, FAILED_STATE]
TERMINAL_STATES = [COMPLETED_STATE, PARTIALLY_COMPLETED_STATE, FAILED_STATE]

# An item is written with nothing paid. It never holds `PROCESSING`: an item's outcome is
# whatever the engine reported, and an item whose process was interrupted is still `PENDING`,
# which is exactly the state that lets a later run try it again.
ITEM_PENDING_STATE = "PENDING"
ITEM_COMPLETED_STATE = "COMPLETED"
ITEM_FAILED_STATE = "FAILED"
ITEM_STATES = [ITEM_PENDING_STATE, ITEM_COMPLETED_STATE, ITEM_FAILED_STATE]

# The engine key a batch item spends, derived from the batch and the item's own position,
# the same shape an approval uses for a request and a refund for a transfer. Because the key
# is derived rather than sent, however many times a batch is processed only one of those
# times can move the money for any one item.
BATCH_KEY_PREFIX = BATCH_KEY_PREFIX

# A batch is a list a person reads and a person corrects, so it is bounded. The bound is on
# the number of lines rather than on the money, which the conditional debit already decides.
MAX_BATCH_ITEMS = 25

# A batch claimed longer than this is assumed to belong to a processing run that is gone.
# The bound is the one transfers use for their own recovery, and for the same reason: a run
# claims the batch and writes each item's outcome immediately, so a claim this old with items
# still pending belongs to a run that was interrupted. Taking it over cannot move money
# twice, because every item's key is derived and still decides what may be spent.
PROCESSING_CLAIM_GRACE_SECONDS = 60

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

# Not being able to read a batch is answered without saying whether it exists.
BATCH_NOT_FOUND_CODE = "BATCH_NOT_FOUND"
BATCH_NOT_FOUND_MESSAGE = "That batch was not found."

BATCH_ACTION_NOT_ALLOWED_CODE = "BATCH_ACTION_NOT_ALLOWED"
BATCH_ACTION_NOT_ALLOWED_MESSAGE = "Only the account that created a batch can read or process it."

BATCH_IN_PROGRESS_CODE = "BATCH_IN_PROGRESS"
BATCH_IN_PROGRESS_MESSAGE = "This batch is being processed right now. Try again in a moment."

BATCH_EMPTY_CODE = "BATCH_EMPTY"
BATCH_EMPTY_MESSAGE = "Add at least one payment to the batch before saving it."

BATCH_TOO_MANY_ITEMS_MESSAGE = f"A batch can hold at most {MAX_BATCH_ITEMS} payments."

# The one status the engine refuses an operation on that is about the operation rather than
# about whether it can be carried out at all. `INSUFFICIENT_FUNDS` is such a refusal: the
# payment is a real one that could not be made now, which is an outcome for that line and not
# a reason to abandon the batch. A recipient that cannot be paid is refused with `404` and one
# addressed to the sender with `400`, because neither is an outcome — the engine answered
# before it looked at any money, and both say nothing about whether this batch is worth
# running.
ITEM_OUTCOME_STATUS = 409

# Why an item could not be paid is the engine's own wording, passed through unchanged, so a
# batch item is refused for exactly the reason a single transfer is and there is nothing here
# to fall out of step with it. The fallback is only for a refusal that arrived with no wording
# of its own, which would otherwise leave the row with nothing to say about itself.
ITEM_FAILURE_FALLBACK_MESSAGE = "This payment could not be completed."