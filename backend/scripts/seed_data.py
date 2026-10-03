"""Seed baseline definitions for the wallet application database.

`seed.py` clears every collection in the application database before it reports the
baseline, so a full start always produces a known state. Each domain feature
registers the collections it owns in `BASELINE_COLLECTIONS` so the reset stays
complete once real collections exist.

Accounts are funded only by an opening credit recorded in the ledger. Transfers move
money between accounts but never create it, so the opening balances remain the only
source of funds in the baseline.
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

# Transfers are seeded by running the real transfer service, so the baseline history is
# produced by the same code path a request takes and cannot disagree with it. Grace Obi
# is left out on purpose: her wallet has no balance and no entries, which is the empty
# state the product needs to demonstrate.
TRANSFER_ROWS = [
    {
        "senderEmail": "aarav.mehta@wallet.demo",
        "recipientEmail": "fatima.noor@wallet.demo",
        "amountMinor": 50000,
        "daysAgo": 6,
    },
    {
        "senderEmail": "diego.santos@wallet.demo",
        "recipientEmail": "aarav.mehta@wallet.demo",
        "amountMinor": 150000,
        "daysAgo": 4,
    },
    {
        "senderEmail": "fatima.noor@wallet.demo",
        "recipientEmail": "diego.santos@wallet.demo",
        "amountMinor": 7550,
        "daysAgo": 2,
    },
]

# Money requests are seeded through the real services rather than written straight into the
# collection. An approved row is approved for real, so it pays through the transfer engine
# and appears in the validated history like any other payment; a declined row is declined for
# real; an expired row is left for the lazy expiry sweep to find, which is the same path that
# expires one in the application. Every state a request can be shown in is seeded on both
# sides of the request, so the baseline demonstrates the whole feature. `APPROVING` is the one
# state that is never seeded: it only ever exists while an approval is running.
#
# Grace Obi is left out, so that signing in as her still shows the product's empty state.
# The balances below are chosen so that every seeded approval can actually be paid.
REQUEST_ROWS = [
    {
        "state": "PENDING",
        "requesterEmail": "fatima.noor@wallet.demo",
        "approverEmail": "aarav.mehta@wallet.demo",
        "amountMinor": 120000,
        "note": "Rent share for April",
        "daysAgo": 1,
    },
    {
        "state": "PENDING",
        "requesterEmail": "aarav.mehta@wallet.demo",
        "approverEmail": "diego.santos@wallet.demo",
        "amountMinor": 75000,
        "note": "Concert tickets",
        "daysAgo": 2,
    },
    {
        "state": "APPROVED",
        "requesterEmail": "aarav.mehta@wallet.demo",
        "approverEmail": "fatima.noor@wallet.demo",
        "amountMinor": 30000,
        "note": "Dinner last Friday",
        "daysAgo": 5,
    },
    {
        "state": "APPROVED",
        "requesterEmail": "diego.santos@wallet.demo",
        "approverEmail": "aarav.mehta@wallet.demo",
        "amountMinor": 150000,
        "note": "Invoice 2214",
        "daysAgo": 3,
    },
    {
        "state": "DECLINED",
        "requesterEmail": "diego.santos@wallet.demo",
        "approverEmail": "fatima.noor@wallet.demo",
        "amountMinor": 400000,
        "note": "Loan repayment",
        "daysAgo": 4,
    },
    {
        "state": "EXPIRED",
        "requesterEmail": "aarav.mehta@wallet.demo",
        "approverEmail": "diego.santos@wallet.demo",
        "amountMinor": 60000,
        "note": "Second-hand books",
        "daysAgo": 9,
    },
]

BASELINE_COLLECTIONS = ("useraccounts", "wallets", "ledgerentries", "transfers", "moneyrequests")
