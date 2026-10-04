"""Moving money between two wallets.

MongoDB here is a standalone server, so a transfer cannot be one multi-document
transaction. Every write below touches exactly one document and is therefore atomic on
its own, and the sequence is arranged so that any interruption leaves a state that can
be *completed forward* rather than reversed.

Three ideas carry that guarantee:

1. The transfer document is written before any money moves, so an interrupted attempt
   always leaves a record of what it intended.
2. Each wallet records the balance change it just made in the same atomic write that
   made it. A wallet that has taken money away can therefore always say so, with the
   exact balance it produced, even if the ledger entry for it was never written.
3. The sender's idempotency key is held on that same document, so a claim on the key and
   the record of the transfer cannot come apart, and the database refuses a second claim
   on it.

Because none of those facts can be recorded outside the document that holds it, there is no
point at which the system can no longer tell what happened.
"""

import hashlib

from bson import ObjectId
from mongoengine.errors import NotUniqueError

from apps.auth.repository import find_active_by_email, find_names_by_ids
from apps.shared.errors import AppError, ValidationError
from apps.wallet import repository as wallet_repository
from apps.wallet.constants import (
    MOVEMENT_ALREADY_APPLIED,
    MOVEMENT_APPLIED,
    MOVEMENT_CREDIT,
    MOVEMENT_DEBIT,
    REFUND_IN_KIND,
    REFUND_OUT_KIND,
    TRANSFER_IN_KIND,
    TRANSFER_OUT_KIND,
)
from apps.wallet.services import find_wallet, public_wallet

from . import repository
from .constants import (
    COMPLETED_STATE,
    FAILED_STATE,
    FINGERPRINT_VERSION,
    IDEMPOTENCY_KEY_CONTENDED_CODE,
    IDEMPOTENCY_KEY_CONTENDED_MESSAGE,
    IDEMPOTENCY_KEY_REUSED_CODE,
    IDEMPOTENCY_KEY_REUSED_MESSAGE,
    INSUFFICIENT_FUNDS_CODE,
    INSUFFICIENT_FUNDS_MESSAGE,
    MAX_KEY_CLAIM_ATTEMPTS,
    MAX_RECOVERY_PER_ATTEMPT,
    MOVEMENT_MISSING_MESSAGE,
    PENDING_STATE,
    RECIPIENT_NOT_FOUND_MESSAGE,
    RECIPIENT_ROLE,
    RECIPIENT_UNAVAILABLE_CODE,
    SELF_TRANSFER_MESSAGE,
    SENDER_ROLE,
    TRANSFER_ALREADY_FAILED_CODE,
    TRANSFER_ALREADY_FAILED_MESSAGE,
)


def resolve_recipient(recipient_email, sender_account):
    """Find the account that will receive the money, before anything is written.

    A recipient that cannot be paid must be rejected while nothing has happened yet, so
    a bad lookup never leaves an attempt behind.
    """
    if recipient_email == sender_account["email"]:
        raise ValidationError([], {"recipientEmail": [SELF_TRANSFER_MESSAGE]})

    account = find_active_by_email(recipient_email)

    if account is None:
        raise AppError(404, "RECIPIENT_NOT_FOUND", RECIPIENT_NOT_FOUND_MESSAGE)

    wallet = wallet_repository.find_wallet_by_user_id(account["_id"])

    if wallet is None:
        raise AppError(404, "RECIPIENT_NOT_FOUND", RECIPIENT_NOT_FOUND_MESSAGE)

    return account, wallet


def recorded_movement(wallet_id, transfer, role, amount_minor):
    """Move the money for one side of a transfer and return how that wallet recorded it.

    The wallet's own record is authoritative: it carries the balance this movement
    produced, which is what the ledger entry needs and what a later retry cannot
    recompute once other movements have followed.
    """
    if role == MOVEMENT_DEBIT:
        wallet, status = wallet_repository.apply_debit(wallet_id, transfer["_id"], amount_minor)

        if status not in (MOVEMENT_APPLIED, MOVEMENT_ALREADY_APPLIED):
            repository.mark_failed(transfer["_id"], INSUFFICIENT_FUNDS_CODE)

            raise AppError(409, "INSUFFICIENT_FUNDS", INSUFFICIENT_FUNDS_MESSAGE)
    else:
        wallet, status = wallet_repository.apply_credit(wallet_id, transfer["_id"], amount_minor)

    if status == MOVEMENT_APPLIED:
        # The movement just pushed is the last element of the post-image.
        return wallet["pendingMovements"][-1]

    movement = wallet_repository.find_movement(wallet_id, transfer["_id"])

    if movement is None:
        raise AppError(500, "MOVEMENT_MISSING", MOVEMENT_MISSING_MESSAGE)

    return movement


def record_leg(transfer, role, movement):
    """Append the ledger entry for one side of a transfer.

    Re-running this is safe: the database rejects a second entry for the same
    transaction on the same wallet, so recovery can retry it as often as it needs to.
    """
    user_id = transfer["senderUserId"] if role == MOVEMENT_DEBIT else transfer["recipientUserId"]
    wallet_id = transfer["senderWalletId"] if role == MOVEMENT_DEBIT else transfer["recipientWalletId"]

    # A refund transfer moves money back along a transfer that already existed, so its
    # ledger legs say REFUND rather than TRANSFER. Everything else about the write is
    # the engine's own.
    if transfer.get("refundOf"):
        kind = REFUND_OUT_KIND if role == MOVEMENT_DEBIT else REFUND_IN_KIND
    else:
        kind = TRANSFER_OUT_KIND if role == MOVEMENT_DEBIT else TRANSFER_IN_KIND

    try:
        wallet_repository.create_entry(
            {
                "wallet_id": wallet_id,
                "user_id": user_id,
                "transaction_id": transfer["_id"],
                "kind": kind,
                "direction": role,
                "amount_minor": movement["amountMinor"],
                "balance_after_minor": movement["balanceAfterMinor"],
            }
        )
    except NotUniqueError:
        pass


def complete_transfer(transfer):
    """Apply a transfer, continuing from wherever a previous attempt stopped.

    Each step is skipped when the wallet has already recorded it, so calling this again
    on the same transfer is harmless. The transfer is only marked completed once both
    ledger entries exist, so a completed transfer never claims more than the ledger
    shows.

    State is re-read rather than trusted from the caller, so a transfer another attempt has
    already settled is never carried any further. Once a transfer settles its movements are
    released, so its own state is what stops it being applied a second time.
    """
    transfer = repository.find_transfer(transfer["_id"])

    if transfer["state"] != PENDING_STATE:
        return transfer

    transfer_id = transfer["_id"]
    amount_minor = transfer["amountMinor"]

    # The sender is debited and recorded first so the two wallets never both hold a
    # balance that disagrees with its ledger for longer than one write.
    record_leg(
        transfer,
        MOVEMENT_DEBIT,
        recorded_movement(transfer["senderWalletId"], transfer, MOVEMENT_DEBIT, amount_minor),
    )
    record_leg(
        transfer,
        MOVEMENT_CREDIT,
        recorded_movement(transfer["recipientWalletId"], transfer, MOVEMENT_CREDIT, amount_minor),
    )

    repository.mark_completed(transfer_id)
    wallet_repository.release_movement(transfer["senderWalletId"], transfer_id)
    wallet_repository.release_movement(transfer["recipientWalletId"], transfer_id)

    return repository.find_transfer(transfer_id)


def recover_in_flight():
    """Finish transfers an interrupted request left behind, before starting a new one.

    Runs on the transfer path rather than on a schedule, so there is no background
    process to operate. Only transfers old enough that no request can still be working on
    one are touched, so recovery never competes with the request that created a transfer.
    A retry of an interrupted request reaches its transfer here rather than by driving it
    itself, which keeps the sweep the one way a transfer is ever finished after the fact.
    """
    for transfer in repository.list_in_flight(MAX_RECOVERY_PER_ATTEMPT):
        try:
            complete_transfer(transfer)
        except AppError:
            continue
        except Exception as error:  # a leftover transfer must never block a new one
            print("Transfer recovery failed:", repr(error))


def public_transfer(transfer, viewer_account_id):
    """Describe a transfer as the viewer sees it: what they sent, or what they received."""
    is_sender = transfer["senderUserId"] == viewer_account_id

    return {
        "id": str(transfer["_id"]),
        "transactionId": str(transfer["_id"]),
        "role": SENDER_ROLE if is_sender else RECIPIENT_ROLE,
        "senderUserId": str(transfer["senderUserId"]),
        "recipientUserId": str(transfer["recipientUserId"]),
        "amountMinor": transfer["amountMinor"],
        "state": transfer["state"],
        "failureCode": transfer.get("failureCode"),
        "note": transfer.get("note"),
        "createdAt": transfer["createdAt"],
        "completedAt": transfer.get("completedAt"),
    }


def request_fingerprint(recipient_account_id, amount_minor, note):
    """A digest of what an idempotency key was spent on.

    A key names one request, not one result, so the request it was sent with has to be
    comparable later: a retry that sends the same key for a different transfer must not be
    answered with the first one. The recipient is identified by the account it resolved to
    rather than by the address typed, so two spellings of one address name one operation,
    and the fields before the note are fixed in shape, so no value in them can imitate a
    separator. Only a digest is stored, and it is never returned to a client.
    """
    canonical = "|".join([FINGERPRINT_VERSION, str(recipient_account_id), str(amount_minor), note or ""])

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def transfer_response(sender_account, transfer):
    """The transfer, with the sender's own wallet as it stands now.

    The balance is read rather than taken from the transfer, because other transfers may
    have settled since. It is the balance the ledger holds, whether this request moved it
    or an earlier attempt of the same operation did.
    """
    return {
        "transfer": public_transfer(transfer, sender_account["_id"]),
        "wallet": public_wallet(find_wallet(sender_account["_id"])),
    }


def replay_response(sender_account, claimed, fingerprint):
    """Answer a repeated key from the transfer it already produced.

    The stored fingerprint is what says whether this is the same request. A key spent on a
    different one is refused rather than answered with somebody else's result, and the
    refusal says nothing about the request that claimed the key.

    A transfer that is still in flight is reported as it is and deliberately not moved on:
    the recovery sweep is what finishes a transfer a request left behind, and it never
    touches one young enough for its own request to still be working on it. Reporting is the
    only thing a replay does, so a transfer is never carried any further by a second
    attempt at the operation that created it.
    """
    if claimed.get("idempotencyFingerprint") != fingerprint:
        raise AppError(409, IDEMPOTENCY_KEY_REUSED_CODE, IDEMPOTENCY_KEY_REUSED_MESSAGE)

    if claimed["state"] == FAILED_STATE:
        raise AppError(409, TRANSFER_ALREADY_FAILED_CODE, TRANSFER_ALREADY_FAILED_MESSAGE)

    response = transfer_response(sender_account, claimed)
    response["idempotentReplay"] = True

    return response, False


def claim_or_replay(sender_account, values, idempotency_key, fingerprint):
    """Spend the sender's key on this transfer, or answer from the one already holding it.

    The claim and the record are the same insert, so the database, not this code, decides
    whether a key has been spent before. Reading the key first spares the common retry an
    insert it would only have to undo, but it is never what makes the key safe.

    Being refused an insert means a competing request won that attempt. That request either
    still holds the key, in which case its transfer is the answer to this one and the next
    pass hands it back, or it failed, which releases the key and leaves it free to be spent
    again — in which case the next pass spends it. Both are answered by trying again, and
    `MAX_KEY_CLAIM_ATTEMPTS` bounds how many times this will race for it, because each refusal
    is evidence that a competitor was there.

    Returns the transfer to work with and whether this call is the one that created it. A
    transfer this call did not create has already been settled by whoever did.
    """
    for _ in range(MAX_KEY_CLAIM_ATTEMPTS):
        if idempotency_key:
            claimed = repository.find_by_key(sender_account["_id"], idempotency_key)

            if claimed is not None:
                return claimed, False

        try:
            return repository.create_transfer(values), True
        except NotUniqueError:
            # The only unique index a transfer carries besides its own `_id` is the
            # idempotency index, so this exact key is held by a request that got there
            # first. Whether it is still holding it is the next pass's question.
            continue

    # Raced the bound and still nothing holds the key, which means every competitor that
    # refused this key has since released it. A released key is free, so the attempt is made
    # once more without racing for it. This insert is not raced either, so it is the last one.
    try:
        return repository.create_transfer(values), True
    except NotUniqueError:
        # Refused again, so a competitor claimed the key after the last read. The database is
        # the only thing that knows which transfer that is, and it is the same answer every
        # earlier pass would have given had the read landed a moment sooner.
        claimed = repository.find_by_key(sender_account["_id"], idempotency_key)

        if claimed is not None:
            return claimed, False

        # The key was released again between the refusal and the read. Nothing can be said to
        # hold it, and the attempt budget is spent, so this is reported as contention rather
        # than crashed on or looped over: it is a refusal the caller may repeat, and by then
        # the key is either held by somebody or free. Letting the refusal out instead would
        # answer a legitimate race with a server error, which is the one thing this must not
        # do — and a batch item's key is raced by every attempt at that item.
        raise AppError(409, IDEMPOTENCY_KEY_CONTENDED_CODE, IDEMPOTENCY_KEY_CONTENDED_MESSAGE)


def execute_transfer(sender_account, recipient_email, amount_minor, note, idempotency_key=None, refund_of=None):
    """Send money from the authenticated account's wallet to another account.

    An idempotency key names this operation for its sender. It is written onto the transfer
    in the transfer's own insert, so the claim and the record are one atomic write and the
    database, not this code, decides whether a key has been spent before. A request that
    sends a key already in use is answered from the transfer that key produced; without a
    key, every request is its own operation, as it has always been.

    `refund_of` marks the transfer as a refund of another one: the marker lives on the new
    transfer alone, which is how the original transfer's document is never touched by its
    own reversal.
    """
    recover_in_flight()

    sender_wallet = find_wallet(sender_account["_id"])
    recipient_account, recipient_wallet = resolve_recipient(recipient_email, sender_account)
    fingerprint = request_fingerprint(recipient_account["_id"], amount_minor, note) if idempotency_key else None

    values = {
        "sender_user_id": sender_account["_id"],
        "sender_wallet_id": sender_wallet["_id"],
        "recipient_user_id": recipient_account["_id"],
        "recipient_wallet_id": recipient_wallet["_id"],
        "amount_minor": amount_minor,
        "state": PENDING_STATE,
        "note": note or None,
    }

    if refund_of is not None:
        values["refund_of"] = ObjectId(refund_of)

    if idempotency_key:
        values["idempotency_key"] = idempotency_key
        values["idempotency_fingerprint"] = fingerprint

    transfer, created = claim_or_replay(sender_account, values, idempotency_key, fingerprint)

    if not created:
        return replay_response(sender_account, transfer, fingerprint)

    return transfer_response(sender_account, complete_transfer(transfer)), True


def decorate_entries(entries, viewer_account_id):
    """Add the counterparty to ledger entries that came from a transfer.

    The ledger stays purely financial: the other party is resolved here, from the
    transfer records, rather than copied onto the entries when they are written.
    """
    if not entries:
        return entries

    transfers = repository.find_transfers_by_ids([entry["transactionId"] for entry in entries])

    if not transfers:
        return entries

    counterparties = resolve_counterparties(transfers.values(), ObjectId(viewer_account_id))

    for entry in entries:
        transfer = transfers.get(ObjectId(entry["transactionId"]))
        counterparty = counterparties.get(transfer["_id"]) if transfer else None

        if counterparty:
            entry["counterparty"] = counterparty

    return entries


def resolve_counterparties(transfers, viewer_account_id):
    """Name the other party in each transfer, with the viewer's role in it."""
    account_ids = set()

    for transfer in transfers:
        account_ids.add(
            transfer["recipientUserId"] if transfer["senderUserId"] == viewer_account_id else transfer["senderUserId"]
        )

    names = find_names_by_ids(account_ids)
    counterparties = {}

    for transfer in transfers:
        is_sender = transfer["senderUserId"] == viewer_account_id
        other_id = transfer["recipientUserId"] if is_sender else transfer["senderUserId"]

        counterparties[transfer["_id"]] = {
            "userId": str(other_id),
            "name": names.get(other_id, "Unknown account"),
            "role": SENDER_ROLE if is_sender else RECIPIENT_ROLE,
            "note": transfer.get("note"),
        }

    return counterparties
