from bson import ObjectId

from apps.shared.mongo import collection, to_dict

from .models import LedgerEntry, Wallet

HISTORY_SORT = ["-created_at", "-id"]


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
