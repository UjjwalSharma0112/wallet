"""Reset the wallet application database to its seeded baseline.

Invoked by `bash setup.sh` on install and on every `bun start`. The reset is
deliberate and total: every collection in the application database is dropped, so ad
hoc validation data cannot survive a restart. Baseline contents are owned by
`seed_data`.
"""

import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wallet_backend.settings")

import django  # noqa: E402

django.setup()

import bcrypt  # noqa: E402
from bson import ObjectId  # noqa: E402
from mongoengine.connection import get_db  # noqa: E402

from apps.auth.models import UserAccount  # noqa: E402
from apps.shared.mongo import collection, to_dict  # noqa: E402
from apps.transfers.constants import COMPLETED_STATE  # noqa: E402
from apps.transfers.models import Transfer  # noqa: E402
from apps.transfers.models import ensure_transfer_indexes  # noqa: E402
from apps.transfers.services import execute_transfer  # noqa: E402
from apps.wallet.models import LedgerEntry, Wallet  # noqa: E402
from apps.wallet.repository import derived_balance  # noqa: E402
from apps.wallet.services import create_wallet  # noqa: E402
from scripts import seed_data  # noqa: E402

SEPARATOR = "========================================"

SEEDED_MODELS = (UserAccount, Wallet, LedgerEntry, Transfer)


def connect():
    database = get_db()
    database.command("ping")

    return database


def reset(database):
    existing = sorted(database.list_collection_names())

    for name in existing:
        database.drop_collection(name)

    return existing


def baseline_report(database):
    return [(name, database[name].count_documents({})) for name in seed_data.BASELINE_COLLECTIONS]


def insert_accounts():
    print("\nSeeding sign-in accounts...")
    password_hash = bcrypt.hashpw(seed_data.DEMO_PASSWORD.encode(), bcrypt.gensalt(seed_data.PASSWORD_ROUNDS)).decode()
    accounts = [UserAccount(name=row["name"], email=row["email"], password_hash=password_hash) for row in seed_data.ACCOUNT_ROWS]
    UserAccount.objects.insert(accounts)

    print(f"  Created {len(accounts)} accounts")

    return accounts


def insert_wallets(accounts):
    print("\nProvisioning wallets...")
    wallets = [create_wallet(account.id, row["openingBalanceMinor"]) for account, row in zip(accounts, seed_data.ACCOUNT_ROWS)]

    print(f"  Created {len(wallets)} wallets")

    return wallets


def backdate_opening_entries(wallets):
    """Give the ledger a plausible history instead of one identical seed timestamp."""
    seeded_at = datetime.now(timezone.utc)

    for wallet, row in zip(wallets, seed_data.ACCOUNT_ROWS):
        moment = seeded_at - timedelta(days=row["openingDaysAgo"])
        collection(LedgerEntry).update_many({"walletId": wallet["_id"]}, {"$set": {"createdAt": moment, "updatedAt": moment}})

    print(f"  Recorded {LedgerEntry.objects.count()} opening ledger entries")


def validate_wallets(accounts):
    """Every wallet must agree with its own ledger and own all of its entries.

    Balances are read fresh rather than reused from provisioning, because seeded
    transfers have since moved money.
    """
    for account in accounts:
        email = account.email
        wallet = Wallet.objects(__raw__={"userId": account.id}).as_pymongo().first()

        if derived_balance(wallet["_id"]) != wallet["availableBalanceMinor"]:
            raise ValueError(f"Seed ledger does not reconcile with the stored balance for {email}.")

        for entry in LedgerEntry.objects(__raw__={"walletId": wallet["_id"]}).as_pymongo():
            if entry["userId"] != account.id:
                raise ValueError(f"Seed ledger entry is attached to the wrong owner for {email}.")


def insert_transfers(accounts):
    """Run the real transfer service so the seeded history is one a request could produce."""
    seeded_at = datetime.now(timezone.utc)
    # The service takes the same account document a request carries, so the documents
    # built for insertion are handed over in that shape.
    by_email = {account.email: to_dict(account) for account in accounts}
    transfers = []

    for row in seed_data.TRANSFER_ROWS:
        # The seed sends no idempotency key, so each seeded transfer is its own operation.
        result, _ = execute_transfer(by_email[row["senderEmail"]], row["recipientEmail"], row["amountMinor"], None)
        transfer_id = ObjectId(result["transfer"]["id"])
        moment = seeded_at - timedelta(days=row["daysAgo"])

        for model, query in (
            (Transfer, {"_id": transfer_id}),
            (LedgerEntry, {"transactionId": transfer_id}),
        ):
            collection(model).update_many(query, {"$set": {"createdAt": moment, "updatedAt": moment}})

        collection(Transfer).update_one({"_id": transfer_id}, {"$set": {"completedAt": moment}})
        transfers.append(transfer_id)

    print(f"  Sent {len(transfers)} transfers")

    return transfers


def validate_transfers(transfers):
    """A seeded transfer must be complete, recorded on both wallets, and value-preserving."""
    for transfer_id in transfers:
        transfer = Transfer.objects(__raw__={"_id": transfer_id}).as_pymongo().first()

        if transfer["state"] != COMPLETED_STATE:
            raise ValueError(f"Seed transfer {transfer_id} did not complete.")

        legs = LedgerEntry.objects(__raw__={"transactionId": transfer_id}).as_pymongo()

        if len(legs) != 2:
            raise ValueError(f"Seed transfer {transfer_id} did not record both ledger entries.")

        moved = sum(leg["amountMinor"] for leg in legs if leg["direction"] == "DEBIT")
        received = sum(leg["amountMinor"] for leg in legs if leg["direction"] == "CREDIT")

        if moved != received or moved != transfer["amountMinor"]:
            raise ValueError(f"Seed transfer {transfer_id} did not conserve value.")

        for leg in legs:
            expected_user = transfer["senderUserId"] if leg["direction"] == "DEBIT" else transfer["recipientUserId"]

            if leg["userId"] != expected_user:
                raise ValueError(f"Seed transfer {transfer_id} recorded a leg for the wrong owner.")


def validate_system_total():
    """Transfers move money between wallets, so the sum of all balances must hold still."""
    wallets = Wallet.objects.as_pymongo()
    total = sum(wallet["availableBalanceMinor"] for wallet in wallets)
    opening = sum(row["openingBalanceMinor"] for row in seed_data.ACCOUNT_ROWS)

    if total != opening:
        raise ValueError("Seed transfers changed the total money in the system.")


def ensure_indexes():
    for model in SEEDED_MODELS:
        model.ensure_indexes()

    # The idempotency index is partial and MongoEngine cannot declare one, so the transfers
    # model creates that one itself. Without it a repeated key would be honoured twice.
    ensure_transfer_indexes()


def seed():
    print(SEPARATOR)
    print("Database Seeding")
    print(f"{SEPARATOR}\n")

    database = connect()
    print(f"Connected to MongoDB database '{database.name}'")

    dropped = reset(database)
    print(f"Cleared {len(dropped)} collection(s)" + (f": {', '.join(dropped)}" if dropped else ""))

    accounts = insert_accounts()
    wallets = insert_wallets(accounts)
    backdate_opening_entries(wallets)
    transfers = insert_transfers(accounts)
    validate_transfers(transfers)
    validate_wallets(accounts)
    validate_system_total()
    ensure_indexes()

    print("\nCollection counts:")

    for name, count in baseline_report(database):
        print(f"  {name}: {count}")

    print("\nDemo accounts:")

    for row in seed_data.ACCOUNT_ROWS:
        print(f"  Email: {row['email']} | Password: {seed_data.DEMO_PASSWORD}")

    print(f"\n{SEPARATOR}")
    print("Seeding completed successfully!")
    print(f"{SEPARATOR}\n")


def main():
    try:
        seed()
    except Exception as error:
        print(f"\nSeeding failed: {error}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
