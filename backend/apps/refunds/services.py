"""Giving a completed transfer back, in whole or in part.

A refund moves no money of its own. It claims a share of an original transfer's refundable
amount, hands that share to the transfer engine pointed the other way, and records the
transfer the engine produced. Every balance change, ledger entry and idempotency decision
therefore belongs to the one financial path R2 and R3 already established, and the only
thing this feature knows about money is which refund transfer returned which part of which
payment back.

MongoDB here is a standalone server, so the claim on the original transfer and the new
transfer it pays cannot be one transaction. They are arranged so that no interruption can
leave a refund claiming a payment that did not happen, or a payment with nothing to show
for it:

1. The refund is written pending, so an interrupted attempt always leaves a record of what
   it intended.
2. The amount is claimed by the database, on the original transfer, before the engine is
   called, so no total of refunds can ever pass the original, however many arrive at once.
3. The key the engine spends is derived from the refund, so however many times the caller
   retries, only one of them can create a transfer.
4. The transfer is recorded on the refund in a single write, after the engine has reported
   the money moved, and only while the refund is still pending.
"""

import hashlib
from datetime import timedelta, timezone

from mongoengine.errors import NotUniqueError

from bson import ObjectId

from apps.auth.repository import find_active_by_id, find_names_by_ids
from apps.shared.documents import now_utc
from apps.shared.errors import AppError
from apps.transfers import repository as transfer_repository
from apps.transfers import services as transfer_services
from apps.transfers.constants import (
    COMPLETED_STATE,
    FINGERPRINT_VERSION,
    IDEMPOTENCY_KEY_REUSED_CODE,
    IDEMPOTENCY_KEY_REUSED_MESSAGE,
)

from . import repository
from .constants import (
    FAILED_STATE,
    ORIGINAL_TRANSFER_NOT_FOUND_CODE,
    ORIGINAL_TRANSFER_NOT_FOUND_MESSAGE,
    PENDING_STATE,
    REFUND_ACTION_NOT_ALLOWED_CODE,
    REFUND_ACTION_NOT_ALLOWED_MESSAGE,
    REFUND_CLAIM_GRACE_SECONDS,
    REFUND_ENGINE_KEY_PREFIX,
    REFUND_IN_PROGRESS_CODE,
    REFUND_IN_PROGRESS_MESSAGE,
    REFUND_LIMIT_EXCEEDED_CODE,
    REFUND_LIMIT_EXCEEDED_MESSAGE,
    REFUND_OF_REFUND_CODE,
    REFUND_OF_REFUND_MESSAGE,
    TRANSFER_NOT_REFUNDABLE_CODE,
    TRANSFER_NOT_REFUNDABLE_MESSAGE,
)
from .models import Refund


def refund_key(refund_id):
    """The engine key a refund spends, derived so a retry of one refund spends one key."""
    return f"{REFUND_ENGINE_KEY_PREFIX}{refund_id}"


def refund_fingerprint(original_transfer_id, amount_minor, note):
    """A digest of what a client key was spent on, so a different refund cannot replay it."""
    canonical = "|".join([FINGERPRINT_VERSION, str(original_transfer_id), str(amount_minor), note or ""])

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_refund(account, original_transfer_id, amount_minor, note, idempotency_key):
    """Return part of a completed transfer to its original sender.

    Checks, in order: the original exists, the caller received it, it completed, and it was
    not itself a refund. The cap is the atomic claim on the original transfer below: it
    is the only writer of the original's refundable counter, so however many refunds
    arrive for the same transfer, the total can only ever stay within the original. A
    claim that finds no room there fails the refund, records that it found no room, and
    moves nothing.
    """
    transfer = load_original_for(account, original_transfer_id)
    fingerprint = refund_fingerprint(transfer["_id"], amount_minor, note) if idempotency_key else None

    if idempotency_key:
        existing = repository.find_by_key(account["_id"], idempotency_key)

        if existing is not None:
            return answer_existing(existing, account, fingerprint), False

    values = {
        "original_transfer_id": transfer["_id"],
        "initiator_user_id": account["_id"],
        "amount_minor": amount_minor,
        "note": note or None,
        "state": PENDING_STATE,
    }

    if idempotency_key:
        values["idempotency_key"] = idempotency_key
        values["idempotency_fingerprint"] = fingerprint

    try:
        refund = repository.create_refund(values)
    except NotUniqueError:
        # The only unique index besides `_id` is the initiator's key, so this is the same
        # call arriving twice: answer from the refund the first one wrote.
        existing = repository.find_by_key(account["_id"], idempotency_key)

        return answer_existing(existing, account, fingerprint), False

    claimed = transfer_repository.claim_refundable_amount(transfer["_id"], amount_minor)

    if claimed is None:
        repository.fail(refund["_id"], REFUND_LIMIT_EXCEEDED_CODE, now_utc())

        raise AppError(409, REFUND_LIMIT_EXCEEDED_CODE, REFUND_LIMIT_EXCEEDED_MESSAGE)

    try:
        payload, created = transfer_services.execute_transfer(
            account, payee_email(transfer), amount_minor, note, refund_key(refund["_id"]), refund_of=transfer["_id"]
        )
    except AppError as error:
        # The engine refuses before any money moves, so both wallets are exactly as they
        # were; the claim is handed back and the refund is closed out as failed.
        failed = repository.fail(refund["_id"], error.code, now_utc())

        if failed is not None:
            transfer_repository.release_refundable_amount(transfer["_id"], amount_minor)

        raise

    settled = repository.settle(refund["_id"], payload["transfer"]["id"], now_utc())

    return refund_response(settled or repository.find_refund(refund["_id"]), payload, created, account), created


def answer_existing(existing, account, fingerprint):
    """What a repeated key answers from the refund it already named.

    A key names one refund, not one result. A different one is refused rather than answered
    with somebody else's money moved. A settled one is answered with its transfer, the way
    R3 answers a spent transfer key. One that never settled belongs to a call that is still
    running, or one that was interrupted: reported as in flight while it is young, and
    carried to its conclusion — without moving money twice, the derived key forbids it —
    once it is old enough that nothing is working on it.
    """
    if fingerprint is not None and existing.get("idempotencyFingerprint") != fingerprint:
        raise AppError(409, IDEMPOTENCY_KEY_REUSED_CODE, IDEMPOTENCY_KEY_REUSED_MESSAGE)

    if existing["state"] == FAILED_STATE:
        raise AppError(409, existing["failureCode"] or "REFUND_FAILED", "That refund already failed. Send it again with a new idempotency key.")

    if existing["state"] == COMPLETED_STATE:
        transfer = transfer_repository.find_transfer(existing["transferId"])
        payload = transfer_services.transfer_response(account, transfer)

        return {"refund": public_refund(existing, account["_id"]), **payload, "idempotentReplay": True}

    refund_transfer = transfer_repository.find_by_key(account["_id"], refund_key(existing["_id"]))

    if refund_transfer is not None:
        if refund_transfer["state"] == COMPLETED_STATE:
            settled = repository.settle(existing["_id"], refund_transfer["_id"], now_utc())
            payload = transfer_services.transfer_response(account, refund_transfer)

            return {"refund": public_refund(settled or existing, account["_id"]), **payload, "idempotentReplay": True}

        if refund_transfer["state"] == FAILED_STATE:
            released = repository.fail(existing["_id"], refund_transfer["failureCode"], now_utc())

            if released is not None:
                transfer_repository.release_refundable_amount(existing["originalTransferId"], existing["amountMinor"])

            raise AppError(409, refund_transfer["failureCode"], "That refund did not go through. Send it again with a new idempotency key.")

        raise AppError(409, REFUND_IN_PROGRESS_CODE, REFUND_IN_PROGRESS_MESSAGE)

    if not abandoned(existing):
        raise AppError(409, REFUND_IN_PROGRESS_CODE, REFUND_IN_PROGRESS_MESSAGE)

    try:
        payload, created = transfer_services.execute_transfer(
            account,
            payee_email(load_original(account, existing["originalTransferId"])),
            existing["amountMinor"],
            existing.get("note"),
            refund_key(existing["_id"]),
            refund_of=existing["originalTransferId"],
        )
    except AppError as error:
        released = repository.fail(existing["_id"], error.code, now_utc())

        if released is not None:
            transfer_repository.release_refundable_amount(existing["originalTransferId"], existing["amountMinor"])

        raise

    settled = repository.settle(existing["_id"], payload["transfer"]["id"], now_utc())

    return {"refund": public_refund(settled or repository.find_refund(existing["_id"]), account["_id"]), **payload, "idempotentReplay": not created}


def list_refunds(account_id, limit, offset):
    """The caller's refunds, newest first, with anything already paid recorded.

    A refund that has settled without its row finding out is recorded on the way out,
    which copies a result the engine produced rather than creating one, and costs one
    query for the whole page rather than one per refund.
    """
    refunds = settle_refunds(account_id, repository.list_for_initiator(account_id, limit, offset))
    origins = transfers_by_id([refund["originalTransferId"] for refund in refunds])
    names = names_for(origins)

    return {
        "refunds": [public_refund(refund, account_id, names, origins) for refund in refunds],
        "total": repository.count_for_initiator(account_id),
        "limit": limit,
        "offset": offset,
    }


def settle_refunds(initiator_user_id, refunds):
    """Record refunds whose transfer paid without the refund row finding out."""
    pending = [refund for refund in refunds if refund["state"] == PENDING_STATE]

    if not pending:
        return refunds

    transfers = transfer_repository.find_by_keys(
        {refund["initiatorUserId"] for refund in pending}, [refund_key(refund["_id"]) for refund in pending]
    )

    for position, refund in enumerate(refunds):
        if refund["state"] != PENDING_STATE:
            continue

        transfer = transfers.get(refund_key(refund["_id"]))

        if transfer is None:
            continue

        if transfer["state"] == COMPLETED_STATE:
            settled = repository.settle(refund["_id"], transfer["_id"], now_utc())

            if settled is not None:
                refunds[position] = settled
        elif transfer["failureCode"]:
            released = repository.fail(refund["_id"], transfer["failureCode"], now_utc())

            if released is not None:
                transfer_repository.release_refundable_amount(refund["originalTransferId"], refund["amountMinor"])
                refunds[position] = repository.find_refund(refund["_id"])

    return refunds


def load_original_for(account, original_transfer_id):
    """The transfer a refund is asked of, once it is known to exist, to be completed, not
    itself a refund, and to belong to the caller as its recipient."""
    transfer = transfer_repository.find_transfer(original_transfer_id)

    if transfer is None:
        raise AppError(404, ORIGINAL_TRANSFER_NOT_FOUND_CODE, ORIGINAL_TRANSFER_NOT_FOUND_MESSAGE)

    if transfer["recipientUserId"] != account["_id"]:
        raise AppError(403, REFUND_ACTION_NOT_ALLOWED_CODE, REFUND_ACTION_NOT_ALLOWED_MESSAGE)

    if transfer["state"] != COMPLETED_STATE:
        raise AppError(409, TRANSFER_NOT_REFUNDABLE_CODE, TRANSFER_NOT_REFUNDABLE_MESSAGE)

    if transfer.get("refundOf"):
        raise AppError(409, REFUND_OF_REFUND_CODE, REFUND_OF_REFUND_MESSAGE)

    return transfer


def load_original(account, original_transfer_id):
    return transfer_repository.find_transfer(original_transfer_id)


def abandoned(refund):
    """Whether a pending refund is old enough that its call has gone, bounded like transfers."""
    return as_instant(refund["updatedAt"]) <= now_utc() - timedelta(seconds=REFUND_CLAIM_GRACE_SECONDS)


def as_instant(moment):
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def payee_email(transfer):
    """The address the engine should pay: the original sender, found now rather than kept."""
    account = find_active_by_id(str(transfer["senderUserId"]))

    if account is None:
        raise AppError(404, ORIGINAL_TRANSFER_NOT_FOUND_CODE, ORIGINAL_TRANSFER_NOT_FOUND_MESSAGE)

    return account["email"]


def transfers_by_id(transfer_ids):
    return transfer_repository.find_transfers_by_ids(transfer_ids)


def names_for(transfers):
    ids = {transfer["senderUserId"] for transfer in transfers.values()}

    return find_names_by_ids(ids) if ids else {}


def public_refund(refund, viewer_account_id, names=None, origins=None):
    """A refund as the caller sees it: who got the money back, through which transfer."""
    originals = origins if origins is not None else transfers_by_id([refund["originalTransferId"]])
    original = originals.get(refund["originalTransferId"])
    counterparty_id = original["senderUserId"] if original else None
    names = names if names is not None else (find_names_by_ids({counterparty_id}) if counterparty_id else {})

    return {
        "id": str(refund["_id"]),
        "state": refund["state"],
        "originalTransferId": str(refund["originalTransferId"]),
        "amountMinor": refund["amountMinor"],
        "note": refund.get("note"),
        "createdAt": refund["createdAt"],
        "updatedAt": refund["updatedAt"],
        "completedAt": refund.get("completedAt"),
        "failureCode": refund.get("failureCode"),
        "transferId": str(refund["transferId"]) if refund.get("transferId") else None,
        "counterparty": (
            {"userId": str(counterparty_id), "name": names.get(counterparty_id, "Unknown account"), "role": "SENDER"}
            if counterparty_id
            else None
        ),
    }


def refund_response(refund, payload, created, account):
    return {"refund": public_refund(refund, account["_id"]), **payload, "idempotentReplay": not created}


def decorate_entries(entries, viewer_account_id):
    """Add refund context to transfer legs, so a history row can offer what can be undone.

    An ordinary transfer row is told how much of it can still be given back, and a refund
    transfer row is tied back to the transfer it pays against. Ordinary transfers of other
    people's money are not refundable by the viewer, and getting that wrong is what the
    field being null rather than absent says out loud.
    """
    if not entries:
        return entries

    transfers = transfer_repository.find_transfers_by_ids([entry["transactionId"] for entry in entries])

    if not transfers:
        return entries

    refund_rows = list(
        Refund.objects(__raw__={"transferId": {"$in": [transfer["_id"] for transfer in transfers.values() if transfer.get("refundOf")]}}).as_pymongo()
    )
    refund_by_transfer = {row["transferId"]: row for row in refund_rows}

    originals = transfer_repository.find_transfers_by_ids(
        {transfer["refundOf"] for transfer in transfers.values() if transfer.get("refundOf")}
    )

    for entry in entries:
        transfer = transfers.get(ObjectId(entry["transactionId"]))

        if transfer is None:
            continue

        if transfer.get("refundOf"):
            original = originals.get(transfer["refundOf"])
            entry["refundOf"] = str(transfer["refundOf"])
            entry["refundId"] = str(refund_by_transfer[transfer["_id"]]["_id"]) if transfer["_id"] in refund_by_transfer else None
            entry["originalAmountMinor"] = original["amountMinor"] if original else None
        else:
            refunded = transfer.get("refundedAmountMinor") or 0
            entry["refundedAmountMinor"] = refunded
            entry["remainingRefundableMinor"] = (
                max(transfer["amountMinor"] - refunded, 0)
                if transfer["state"] == COMPLETED_STATE and transfer["recipientUserId"] == ObjectId(viewer_account_id)
                else None
            )

    return entries
