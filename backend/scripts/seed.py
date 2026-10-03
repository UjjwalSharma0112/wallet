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
from mongoengine.connection import get_db  # noqa: E402

from apps.auth.models import UserAccount  # noqa: E402
from apps.shared.mongo import collection  # noqa: E402
from apps.wallet.models import LedgerEntry, Wallet  # noqa: E402
from apps.wallet.repository import derived_balance  # noqa: E402
from apps.wallet.services import create_wallet  # noqa: E402
from scripts import seed_data  # noqa: E402

SEPARATOR = "========================================"

SEEDED_MODELS = (UserAccount, Wallet, LedgerEntry)


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


def validate_wallets(accounts, wallets):
    for account, row, wallet in zip(accounts, seed_data.ACCOUNT_ROWS, wallets):
        email = row["email"]
        stored = wallet["availableBalanceMinor"]

        if stored != row["openingBalanceMinor"]:
            raise ValueError(f"Seed wallet balance does not match the opening balance for {email}.")

        if derived_balance(wallet["_id"]) != stored:
            raise ValueError(f"Seed ledger does not reconcile with the stored balance for {email}.")

        for entry in LedgerEntry.objects(__raw__={"walletId": wallet["_id"]}).as_pymongo():
            if entry["userId"] != account.id:
                raise ValueError(f"Seed ledger entry is attached to the wrong owner for {email}.")


def ensure_indexes():
    for model in SEEDED_MODELS:
        model.ensure_indexes()


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
    validate_wallets(accounts, wallets)
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
