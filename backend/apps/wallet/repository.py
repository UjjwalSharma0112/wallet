from bson import ObjectId
from pymongo import ReturnDocument

from apps.shared.mongo import collection, to_dict

from .constants import (
    MOVEMENT_ALREADY_APPLIED,
    MOVEMENT_APPLIED,
    MOVEMENT_CREDIT,
    MOVEMENT_DEBIT,
    MOVEMENT_REFUSED,
)
from .models import LedgerEntry, Wallet

HISTORY_SORT = ["-created_at", "-id"]

PENDING_MOVEMENTS_FIELD = "pendingMovements"
MOVEMENT_MATCH_FIELD = f"{PENDING_MOVEMENTS_FIELD}.transferId"


def find_wallet_by_user_id(user_id):
    return Wallet.objects(__raw__={"userId": ObjectId(user_id)}).as_pymongo().first()


def create_wallet(values):
    return to_dict(Wallet(**values).save())


def list_entries(wallet_id, limit, offset):
    conditions = {"walletId": ObjectId(wallet_id)}

    return list(LedgerEntry.objects(__raw__=conditions).order_by(*HISTORY_SORT).skip(offset).limit(limit).as_pymongo())


def count_entries(wallet_id):
    return LedgerEntry.objects(__raw__={"walletId": ObjectId(wallet_id)}).count()


def latest_entry(wallet_id):
    return LedgerEntry.objects(__raw__={"walletId": ObjectId(wallet_id)}).order_by(*HISTORY_SORT).as_pymongo().first()


def create_entry(values):
    return to_dict(LedgerEntry(**values).save())


def movement_stage(transfer_id, role, delta):
    """Build the update that moves a balance and records the movement in one write.

    A plain `$inc` cannot place the resulting balance inside the element it pushes, but
    every `$set` in a pipeline stage reads the document as it was before that stage, so
    `availableBalanceMinor + delta` is both the new balance and the value to record.
    """
    recorded = {
        "transferId": ObjectId(transfer_id),
        "role": role,
        "amountMinor": abs(delta),
        "balanceAfterMinor": {"$add": ["$availableBalanceMinor", delta]},
    }

    return [
        {
            "$set": {
                "availableBalanceMinor": {"$add": ["$availableBalanceMinor", delta]},
                PENDING_MOVEMENTS_FIELD: {
                    "$concatArrays": [{"$ifNull": [f"${PENDING_MOVEMENTS_FIELD}", []]}, [recorded]]
                },
            }
        }
    ]


def apply_movement(wallet_id, transfer_id, role, delta, require_balance):
    """Move a wallet balance and record what it did, atomically.

    The stored `balanceAfterMinor` is the balance this movement produced, so the ledger
    entry for it can be written later from the wallet's own record rather than from a
    balance that may since have moved on. Matching on a transfer the wallet has not
    already moved makes the write idempotent: a retry matches nothing and returns `None`.
    """
    conditions = {"_id": ObjectId(wallet_id), MOVEMENT_MATCH_FIELD: {"$ne": ObjectId(transfer_id)}}

    if require_balance:
        conditions["availableBalanceMinor"] = {"$gte": abs(delta)}

    return collection(Wallet).find_one_and_update(
        conditions, movement_stage(transfer_id, role, delta), return_document=ReturnDocument.AFTER
    )


def find_movement(wallet_id, transfer_id):
    """Return the movement this wallet already recorded for a transfer, if any."""
    transfer_id = ObjectId(transfer_id)
    wallet = collection(Wallet).find_one(
        {"_id": ObjectId(wallet_id), MOVEMENT_MATCH_FIELD: transfer_id},
        {PENDING_MOVEMENTS_FIELD: {"$elemMatch": {"transferId": transfer_id}}},
    )

    if wallet is None:
        return None

    recorded = wallet.get(PENDING_MOVEMENTS_FIELD) or []

    return recorded[0] if recorded else None


def release_movement(wallet_id, transfer_id):
    """Forget a movement once the ledger records it. Safe to repeat."""
    collection(Wallet).update_one(
        {"_id": ObjectId(wallet_id), MOVEMENT_MATCH_FIELD: ObjectId(transfer_id)},
        {"$pull": {PENDING_MOVEMENTS_FIELD: {"transferId": ObjectId(transfer_id)}}},
    )


def apply_debit(wallet_id, transfer_id, amount_minor):
    """Take money out of a wallet, but only if the wallet can cover it.

    Reports whether the debit was applied, was already applied for this transfer, or was
    refused for want of funds. The refusal is decided by the server against the balance
    it matched, so concurrent debits cannot both pass a check the other invalidated.
    """
    wallet = apply_movement(wallet_id, transfer_id, MOVEMENT_DEBIT, -amount_minor, require_balance=True)

    if wallet is not None:
        return wallet, MOVEMENT_APPLIED

    if find_movement(wallet_id, transfer_id) is not None:
        return None, MOVEMENT_ALREADY_APPLIED

    return None, MOVEMENT_REFUSED


def apply_credit(wallet_id, transfer_id, amount_minor):
    """Pay money into a wallet. A credit can never overdraw, so only idempotency matters."""
    wallet = apply_movement(wallet_id, transfer_id, MOVEMENT_CREDIT, amount_minor, require_balance=False)

    if wallet is not None:
        return wallet, MOVEMENT_APPLIED

    return None, MOVEMENT_ALREADY_APPLIED


def derived_balance(wallet_id):
    """Sum the ledger into the balance it implies.

    The stored `availableBalanceMinor` is a materialized projection of this sum, so
    comparing the two is how an inconsistency is detected.
    """
    pipeline = [
        {"$match": {"walletId": ObjectId(wallet_id)}},
        {
            "$group": {
                "_id": None,
                "total": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$direction", "CREDIT"]},
                            "$amountMinor",
                            {"$multiply": [-1, "$amountMinor"]},
                        ]
                    }
                },
            }
        },
    ]
    rows = list(collection(LedgerEntry).aggregate(pipeline))

    return rows[0]["total"] if rows else 0


def totals_by_direction(wallet_id):
    """Report credited and debited minor units so the ledger can be checked for balance."""
    pipeline = [
        {"$match": {"walletId": ObjectId(wallet_id)}},
        {
            "$group": {
                "_id": "$direction",
                "total": {"$sum": "$amountMinor"},
            }
        },
    ]

    return {row["_id"]: row["total"] for row in collection(LedgerEntry).aggregate(pipeline)}
