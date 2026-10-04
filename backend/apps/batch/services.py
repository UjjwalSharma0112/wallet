"""Paying a list of people in one recorded action.

A batch moves no money of its own. It takes one item at a time, hands that item's recipient,
amount and note to the transfer engine under a key derived from the batch and the item's own
position, and records the transfer the engine produced. Every balance change, ledger entry,
recovery sweep and idempotency decision therefore belongs to the one financial path R2 and R3
already established, and the only thing this feature knows about money is which transfer paid
which item.

MongoDB here is a standalone server, so the claim on the batch and the transfers it produces
cannot be one transaction. They are arranged so that no interruption can leave a batch
claiming a payment that did not happen, or a payment with nothing to show for it:

1. The batch is claimed before the engine is called, so two processing runs arriving together
   cannot both proceed, and so nothing has been paid yet while the claim is taken.
2. Each item is settled by one conditional update against the batch's own document, keyed on
   that item's state, so a run records an item's outcome once and two items being settled at
   the same time cannot overwrite each other.
3. The key each item spends is derived from the batch and its position, so however many times
   a batch is processed only one of those times can move the money for any one item. An item
   that has already moved money is answered from the transfer that moved it; an item the
   engine refused failed and gave its key back, so the very same key is free to try again.
4. The claim is not a terminal state. A run that cannot start at all hands it back, and one
   that is interrupted leaves it claimed for a later run to take over once it is old enough
   that nothing is working on it.
5. The batch's own state is derived from its items, in the same write that records it, so it
   can never claim an outcome its items disagree with.

A batch is not all-or-nothing, and nothing here pretends otherwise. Each item is settled on
its own, so one line that cannot be paid leaves every other line paid and standing.
"""

from bson import ObjectId

from apps.shared.documents import now_utc
from apps.shared.errors import AppError
from apps.transfers import services as transfer_services
from apps.wallet.services import find_wallet, public_wallet

from . import repository
from .constants import (
    BATCH_ACTION_NOT_ALLOWED_CODE,
    BATCH_ACTION_NOT_ALLOWED_MESSAGE,
    BATCH_IN_PROGRESS_CODE,
    BATCH_IN_PROGRESS_MESSAGE,
    BATCH_KEY_PREFIX,
    BATCH_NOT_FOUND_CODE,
    BATCH_NOT_FOUND_MESSAGE,
    ITEM_COMPLETED_STATE,
    ITEM_FAILED_STATE,
    ITEM_FAILURE_FALLBACK_MESSAGE,
    ITEM_OUTCOME_STATUS,
    ITEM_PENDING_STATE,
    PENDING_STATE,
    PROCESSING_STATE,
)

# What a run that could not process the batch is told. A batch already claimed is the only
# such case left: it is either being worked on or was abandoned, and a claim old enough that
# nothing is working on it is taken over rather than refused, so this answer is only ever
# about somebody else having it now.
REFUSALS = {
    PROCESSING_STATE: (BATCH_IN_PROGRESS_CODE, BATCH_IN_PROGRESS_MESSAGE),
}


def batch_key(batch_id, position):
    """The idempotency key one item spends.

    Derived from the batch and the item's own position, never from the caller, so every
    attempt at processing this batch spends the same key for the same item and therefore names
    the same operation. R3's unique index then allows exactly one of those attempts to create
    a transfer, however many arrive at once, and an item's recipient, amount and note are all
    fixed when the batch is created, so every attempt also produces the same fingerprint and
    none of them can be refused as a different request.

    The key is not only what stops an item being paid twice. It is also what lets a failed
    item be tried again: a failed transfer gives its key back, so the same key names a fresh
    attempt rather than a spent one, and a run that refused an item for want of funds can be
    run again once there is money to pay it with. The prefix is refused from an incoming
    `Idempotency-Key`, so no client can spend this name on anything else.
    """
    return f"{BATCH_KEY_PREFIX}{batch_id}:{position}"


def create_batch(owner_account, items):
    """Record a list of payments to make, without paying any of them.

    Nothing is resolved and no wallet is read, because nothing is being paid yet: whether a
    recipient can be paid is decided when the item runs, and an account may have been closed,
    or its address changed, between saving a batch and processing it. What is fixed here is
    each item's position, and that is what its key will be derived from.
    """
    batch = repository.create_batch(
        {
            "owner_user_id": owner_account["_id"],
            "state": PENDING_STATE,
            "items": [
                {
                    "position": item["position"],
                    "recipientEmail": item["recipient_email"],
                    "amountMinor": item["amount_minor"],
                    "note": item["note"],
                    "state": ITEM_PENDING_STATE,
                }
                for item in items
            ],
        }
    )

    return public_batch(batch)


def process_batch(owner_account, batch_id):
    """Run every item of one of this account's batches through the transfer engine.

    The claim comes first and is one conditional update, so of two processing runs arriving
    together exactly one proceeds. Items are then settled one at a time, each under its own
    derived key, and a refusal from the engine is recorded against that item and the run
    carries on: a batch that cannot pay one person is still a batch that can pay the rest, and
    stopping would leave money unmoved for no reason. Only a refusal that is not about any one
    payment ends the run, and a claim this run took is handed back rather than left behind.
    """
    batch = load_batch_for(owner_account, batch_id)
    claimed = repository.claim(batch["_id"], now_utc())

    if claimed is None:
        # Either somebody is working on this batch now, or a run that did claim it was
        # interrupted. A claim old enough that nothing is working on it may be taken over;
        # the derived keys are what make taking it over safe, because an item whose money has
        # already moved is answered from the transfer that moved it rather than paid again.
        claimed = take_over(batch) if repository.abandoned(batch) else None

        if claimed is None:
            refuse(repository.find_batch(batch["_id"]))

    run_batch(owner_account, claimed)

    return batch_response(owner_account, repository.finalise(claimed["_id"], now_utc()) or repository.find_batch(claimed["_id"]))


def run_batch(owner_account, claimed):
    """Hand each item of a claimed batch to the engine, and settle each one as it is answered.

    A refusal that is not about one payment — a recipient that cannot be paid, or one that is
    the sender — ends the run and hands the claim back. Items already paid are left paid and
    the batch keeps saying so, because the money really did move. The refusal is raised rather
    than recorded against the item because it is not that item's outcome: the engine answered
    before it looked at any money, so the payment was never attempted, and recording it would
    mark the item settled for ever over a problem the owner can do nothing about and that a
    later run might not meet at all.
    """
    for item in list(claimed["items"]):
        if item["state"] == ITEM_COMPLETED_STATE:
            # Already paid, by an earlier run or by one that raced this run for it. The key
            # this item would spend is held by the transfer that paid it, so running it again
            # could only answer from that transfer; skipping it says the same thing without
            # asking the engine. An item that failed is *not* skipped: its key was given back
            # when it failed, so the very same key names a fresh attempt.
            continue

        try:
            payload, _ = transfer_services.execute_transfer(
                owner_account,
                item["recipientEmail"],
                item["amountMinor"],
                item.get("note"),
                batch_key(claimed["_id"], item["position"]),
            )
        except AppError as error:
            if error.status_code != ITEM_OUTCOME_STATUS:
                repository.release(claimed["_id"], now_utc())

                raise

            settle_item(claimed["_id"], item, failed=error)

            continue

        settle_item(claimed["_id"], item, transfer_id=payload["transfer"]["id"])

    return claimed


def settle_item(batch_id, item, transfer_id=None, failed=None):
    """Record one item's outcome on the batch's own document, once.

    The write names the item by its position and matches on that item still being pending, so
    a run that has been overtaken records nothing rather than overwriting what the run that
    beat it settled. Either way the answer is the same, because both runs spent the same key
    and so arrived at the same transfer.
    """
    if transfer_id is not None:
        changes = {"state": ITEM_COMPLETED_STATE, "transferId": ObjectId(transfer_id)}
    else:
        changes = {"state": ITEM_FAILED_STATE, "failureCode": failed.code, "failureMessage": message_for(failed)}

    return repository.settle_item(batch_id, item["position"], changes, now_utc())


def message_for(error):
    """Why this payment could not be made, in the words the engine refused it with.

    The reason is the engine's and is passed through unchanged, so a batch item is refused for
    exactly the reason a single transfer is and there is nothing here to fall out of step with
    it. There is deliberately no table of batch-specific wording: a second copy of a message
    can only ever disagree with the one it copied, and a disagreement here would be shown to a
    person as though the engine had said it.
    """
    return error.message or ITEM_FAILURE_FALLBACK_MESSAGE


def take_over(batch):
    """Take a batch whose run was interrupted, or say why it could not be taken.

    The reassert matches only a batch still claimed for processing, so it cannot revive one
    that has since been dealt with — a batch finished by the run this one overtook is
    answered from the state it actually reached rather than being run a second time.
    """
    claimed = repository.reassert_claim(batch["_id"], now_utc())

    if claimed is None:
        refuse(repository.find_batch(batch["_id"]))

    return claimed


def refuse(batch):
    """Answer a run that could not process this batch, from the state it has reached."""
    code, message = REFUSALS[batch["state"]]

    raise AppError(409, code, message)


def list_batches(account_id, state, limit, offset):
    """The account's own batches, newest first.

    A batch is only ever read with the account that created it, so a page is one query and no
    filter can widen it past its owner.
    """
    return {
        "batches": [public_batch(batch) for batch in repository.list_for_owner(account_id, state, limit, offset)],
        "total": repository.count_for_owner(account_id, state),
        "state": state,
        "limit": limit,
        "offset": offset,
    }


def load_batch_for(account, batch_id):
    """The batch, once it is known to exist and to be this account's to act on.

    A batch belonging to another account is refused here rather than answered, so no later
    step has to decide whether the caller was allowed to be holding it. The two refusals carry
    the same wording, so neither of them says whether a batch with that id exists.
    """
    batch = repository.find_batch(batch_id)

    if batch is None:
        raise AppError(404, BATCH_NOT_FOUND_CODE, BATCH_NOT_FOUND_MESSAGE)

    if batch["ownerUserId"] != account["_id"]:
        raise AppError(403, BATCH_ACTION_NOT_ALLOWED_CODE, BATCH_ACTION_NOT_ALLOWED_MESSAGE)

    return batch


def summarise(items):
    """What a list of items adds up to, read off the items themselves.

    Every figure here is counted from the items rather than kept beside them. A counter stored
    on the batch would be a second answer to the same question and the two could disagree —
    which is why the batch holds none, and why the state a batch ends in is derived from these
    same items by the database rather than written from here.
    """
    completed = [item for item in items if item["state"] == ITEM_COMPLETED_STATE]
    failed = [item for item in items if item["state"] == ITEM_FAILED_STATE]

    return {
        "totalItems": len(items),
        "completedItems": len(completed),
        "failedItems": len(failed),
        "pendingItems": len(items) - len(completed) - len(failed),
        "totalAmountMinor": sum(item["amountMinor"] for item in items),
        "completedAmountMinor": sum(item["amountMinor"] for item in completed),
        "failedAmountMinor": sum(item["amountMinor"] for item in failed),
    }


def public_batch(batch):
    """A batch as its owner sees it: its state, what it adds up to, and every line of it."""
    return {
        "id": str(batch["_id"]),
        "state": batch["state"],
        "summary": summarise(batch["items"]),
        "items": [public_item(item) for item in batch["items"]],
        "createdAt": batch["createdAt"],
        "updatedAt": batch["updatedAt"],
        "completedAt": batch.get("completedAt"),
    }


def public_item(item):
    return {
        "position": item["position"],
        "recipientEmail": item["recipientEmail"],
        "amountMinor": item["amountMinor"],
        "note": item.get("note"),
        "state": item["state"],
        "failureCode": item.get("failureCode"),
        "failureMessage": item.get("failureMessage"),
        "transferId": str(item["transferId"]) if item.get("transferId") else None,
    }


def batch_response(account, batch):
    """The batch as it now stands, with the owner's own wallet beside it.

    The balance is read rather than taken from any transfer, because other movements may have
    settled since — including the ones this very batch made. It is the balance the ledger
    holds, which is what makes the figure on screen the figure that settled.
    """
    return {"batch": public_batch(batch), "wallet": public_wallet(find_wallet(account["_id"]))}