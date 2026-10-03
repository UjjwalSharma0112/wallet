"""Seed baseline definitions for the wallet application database.

`seed.py` clears every collection in the application database before it reports the
baseline, so a full start always produces a known state. Each domain feature
registers the collections it owns in `BASELINE_COLLECTIONS` so the reset stays
complete once real collections exist.
"""

DATABASE_NAME = "wallet_db"

BASELINE_COLLECTIONS = ()
