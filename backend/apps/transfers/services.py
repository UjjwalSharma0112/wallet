"""Moving money between two wallets.

MongoDB here is a standalone server, so a transfer cannot be one multi-document
transaction. Every write below touches exactly one document and is therefore atomic on
its own, and the sequence is arranged so that any interruption leaves a state that can
be *completed forward* rather than reversed.

Two ideas carry that guarantee:

1. The transfer document is written before any money moves, so an interrupted attempt
   always leaves a record of what it intended.
2. Each wallet records the balance change it just made in the same atomic write that
   made it. A wallet that has taken money away can therefore always say so, with the
   exact balance it produced, even if the ledger entry for it was never written.

Because neither fact can be recorded outside the document that holds it, there is no
point at which the system can no longer tell what happened.
"""

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
    TRANSFER_IN_KIND,
    TRANSFER_OUT_KIND,
)
from apps.wallet.services import find_wallet, public_wallet

from . import repository
from .constants import (
    COMPLETED_STATE,
    FAILED_STATE,
    INSUFFICIENT_FUNDS_CODE,
    INSUFFICIENT_FUNDS_MESSAGE,
    MAX_RECOVERY_PER_ATTEMPT,
    MOVEMENT_MISSING_MESSAGE,
    PENDING_STATE,
    RECIPIENT_NOT_FOUND_MESSAGE,
    RECIPIENT_ROLE,
    RECIPIENT_UNAVAILABLE_CODE,
    SELF_TRANSFER_MESSAGE,
    SENDER_ROLE,
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

    try:
        wallet_repository.create_entry(
            {
                "wallet_id": wallet_id,
                "user_id": user_id,
                "transaction_id": transfer["_id"],
                "kind": TRANSFER_OUT_KIND if role == MOVEMENT_DEBIT else TRANSFER_IN_KIND,
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


def execute_transfer(sender_account, recipient_email, amount_minor, note):
    """Send money from the authenticated account's wallet to another account."""
    recover_in_flight()

    sender_wallet = find_wallet(sender_account["_id"])
    recipient_account, recipient_wallet = resolve_recipient(recipient_email, sender_account)

    transfer = repository.create_transfer(
        {
            "sender_user_id": sender_account["_id"],
            "sender_wallet_id": sender_wallet["_id"],
            "recipient_user_id": recipient_account["_id"],
            "recipient_wallet_id": recipient_wallet["_id"],
            "amount_minor": amount_minor,
            "state": PENDING_STATE,
            "note": note or None,
        }
    )

    completed = complete_transfer(transfer)

    return {
        "transfer": public_transfer(completed, sender_account["_id"]),
        "wallet": public_wallet(find_wallet(sender_account["_id"])),
    }


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
