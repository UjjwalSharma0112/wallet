"""Seed baseline definitions for the wallet application database.

`seed.py` clears every collection in the application database before it reports the
baseline, so a full start always produces a known state. Each domain feature
registers the collections it owns in `BASELINE_COLLECTIONS` so the reset stays
complete once real collections exist.

Accounts are funded only by an opening credit recorded in the ledger. R1 defines no
runtime credit path, so these opening balances are what the product demonstrates.
"""

DATABASE_NAME = "wallet_db"

PASSWORD_ROUNDS = 12
DEMO_PASSWORD = "Wallet@2026"

ACCOUNT_ROWS = [
    {
        "name": "Aarav Mehta",
        "email": "aarav.mehta@wallet.demo",
        "openingBalanceMinor": 245000,
        "openingDaysAgo": 96,
    },
    {
        "name": "Fatima Noor",
        "email": "fatima.noor@wallet.demo",
        "openingBalanceMinor": 12850,
        "openingDaysAgo": 41,
    },
    {
        "name": "Grace Obi",
        "email": "grace.obi@wallet.demo",
        "openingBalanceMinor": 0,
        "openingDaysAgo": 12,
    },
    {
        "name": "Diego Santos",
        "email": "diego.santos@wallet.demo",
        "openingBalanceMinor": 965000,
        "openingDaysAgo": 8,
    },
]

BASELINE_COLLECTIONS = ("useraccounts", "wallets", "ledgerentries")
