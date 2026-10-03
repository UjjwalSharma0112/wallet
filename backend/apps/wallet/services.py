from bson import ObjectId
from mongoengine.errors import NotUniqueError

from apps.shared.errors import AppError

from . import repository
from .constants import CURRENCY, OPENING_KIND, WALLET_EXISTS_MESSAGE, WALLET_NOT_FOUND_MESSAGE


def find_wallet(account_id):
    wallet = repository.find_wallet_by_user_id(account_id)

    if wallet is None:
        raise AppError(404, "WALLET_NOT_FOUND", WALLET_NOT_FOUND_MESSAGE)

    return wallet


def public_wallet(wallet):
    return {
        "id": str(wallet["_id"]),
        "userId": str(wallet["userId"]),
        "currency": wallet["currency"],
        "availableBalanceMinor": wallet["availableBalanceMinor"],
        "createdAt": wallet["createdAt"],
        "updatedAt": wallet["updatedAt"],
    }


def public_entry(entry):
    return {
        "id": str(entry["_id"]),
        "transactionId": str(entry["transactionId"]),
        "kind": entry["kind"],
        "direction": entry["direction"],
        "amountMinor": entry["amountMinor"],
        "balanceAfterMinor": entry["balanceAfterMinor"],
        "createdAt": entry["createdAt"],
    }


def create_wallet(account_id, opening_balance_minor):
    """Provision the single wallet an account owns, funded by an opening credit.

    Ownership is fixed to the account here and repeated on the ledger entry, so an
    entry can never be attached to the wrong owner. The opening credit is appended
    only after the wallet exists; the ledger is the balance's source of truth, so a
    failure between the two writes is detectable by comparing the stored balance
    against `repository.derived_balance`.
    """
    if repository.find_wallet_by_user_id(account_id) is not None:
        raise AppError(409, "WALLET_EXISTS", WALLET_EXISTS_MESSAGE)

    try:
        wallet = repository.create_wallet(
            {
                "user_id": account_id,
                "currency": CURRENCY,
                "available_balance_minor": opening_balance_minor,
            }
        )
    except NotUniqueError:
        raise AppError(409, "WALLET_EXISTS", WALLET_EXISTS_MESSAGE)

    if opening_balance_minor > 0:
        repository.create_entry(
            {
                "wallet_id": wallet["_id"],
                "user_id": account_id,
                "transaction_id": ObjectId(),
                "kind": OPENING_KIND,
                "direction": "CREDIT",
                "amount_minor": opening_balance_minor,
                "balance_after_minor": opening_balance_minor,
            }
        )

    return wallet


def get_wallet(account_id):
    return public_wallet(find_wallet(account_id))


def list_transactions(account_id, limit, offset):
    wallet = find_wallet(account_id)
    entries = repository.list_entries(wallet["_id"], limit, offset)

    return {
        "entries": [public_entry(entry) for entry in entries],
        "total": repository.count_entries(wallet["_id"]),
        "limit": limit,
        "offset": offset,
    }
