"""Reset the wallet application database to its seeded baseline.

Invoked by `bash setup.sh` on install and on every `bun start`. The reset is
deliberate and total: every collection in the application database is dropped, so ad
hoc validation data cannot survive a restart. Baseline contents are owned by
`seed_data`.
"""

import os
import sys
import traceback
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wallet_backend.settings")

import django  # noqa: E402

django.setup()

from mongoengine.connection import get_db  # noqa: E402

from scripts import seed_data  # noqa: E402

SEPARATOR = "========================================"


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
    rows = []

    for name in seed_data.BASELINE_COLLECTIONS:
        rows.append((name, database[name].count_documents({})))

    return rows


def seed():
    print(SEPARATOR)
    print("Database Seeding")
    print(f"{SEPARATOR}\n")

    database = connect()
    print(f"Connected to MongoDB database '{database.name}'")

    dropped = reset(database)
    print(f"Cleared {len(dropped)} collection(s)" + (f": {', '.join(dropped)}" if dropped else ""))

    rows = baseline_report(database)
    print(f"Baseline collections: {len(rows)}")

    for name, count in rows:
        print(f"  {name}: {count} document(s)")

    print(f"\n{SEPARATOR}")
    print("Seeding completed successfully!")
    print(SEPARATOR)

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
