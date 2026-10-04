"""Seed baseline definitions for the wallet application database.

`seed.py` clears every collection in the application database before it reports the
baseline, so a full start always produces a known state. Each domain feature
registers the collections it owns in `BASELINE_COLLECTIONS` so the reset stays
complete once real collections exist.

Accounts are funded only by an opening credit recorded in the ledger. Transfers move
money between accounts but never create it, so the opening balances remain the only
source of funds in the baseline.
"""

from apps.auth.constants import USER_ROLE

DATABASE_NAME = "wallet_db"

PASSWORD_ROUNDS = 12
DEMO_PASSWORD = "Wallet@2026"

# Every account is an ordinary holder. Roles exist and are enforced, but no requirement gives
# any role a capability that an ordinary holder lacks, and nothing in this product is an
# administrative operation, so every seeded account is a USER. The role is written out rather
# than left to the model's default, so the baseline says what it means on the record.
ACCOUNT_ROWS = [
    {
        "name": "Aarav Mehta",
        "email": "aarav.mehta@wallet.demo",
        "openingBalanceMinor": 245000,
        "openingDaysAgo": 96,
        "role": USER_ROLE,
    },
    {
        "name": "Fatima Noor",
        "email": "fatima.noor@wallet.demo",
        "openingBalanceMinor": 12850,
        "openingDaysAgo": 41,
        "role": USER_ROLE,
    },
    {
        "name": "Grace Obi",
        "email": "grace.obi@wallet.demo",
        "openingBalanceMinor": 0,
        "openingDaysAgo": 12,
        "role": USER_ROLE,
    },
    {
        "name": "Diego Santos",
        "email": "diego.santos@wallet.demo",
        "openingBalanceMinor": 965000,
        "openingDaysAgo": 8,
        "role": USER_ROLE,
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

# Refunds are seeded through the real refund service too, so the compensating entries are
# the same ones a request produces and the baseline can prove it. The scenarios are the
# ones a payee meeting a real payment writes: one full give-back, one partial, and two
# small corrections against the same transfer. Every row pays from the original
# recipient's wallet, which is exactly whose balance it debits — the amounts below were
# chosen so the seeded wallets cover them.
REFUND_ROWS = [
    {
        "originalSenderEmail": "diego.santos@wallet.demo",
        "originalRecipientEmail": "aarav.mehta@wallet.demo",
        "originalAmountMinor": 150000,
        "refundAmountMinor": 150000,
        "note": "Overcharged invoice",
        "daysAgo": 3,
    },
    {
        "originalSenderEmail": "aarav.mehta@wallet.demo",
        "originalRecipientEmail": "fatima.noor@wallet.demo",
        "originalAmountMinor": 50000,
        "refundAmountMinor": 20000,
        "note": "Items missing from the order",
        "daysAgo": 4,
    },
    {
        "originalSenderEmail": "fatima.noor@wallet.demo",
        "originalRecipientEmail": "diego.santos@wallet.demo",
        "originalAmountMinor": 7550,
        "refundAmountMinor": 1000,
        "note": "One item returned",
        "daysAgo": 2,
    },
    {
        "originalSenderEmail": "fatima.noor@wallet.demo",
        "originalRecipientEmail": "diego.santos@wallet.demo",
        "originalAmountMinor": 7550,
        "refundAmountMinor": 2000,
        "note": "Second item returned",
        "daysAgo": 1,
    },
]

BASELINE_COLLECTIONS = ("useraccounts", "wallets", "ledgerentries", "transfers", "moneyrequests", "refunds", "batches")

# A batch that is waiting to be run, owned by Aarav and addressing the other three accounts.
# Nothing here is paid: the seed deliberately stops one step short of processing, so that
# running a batch is something the person using the application does rather than something the
# baseline did for them, and so that no balance in the baseline above has to change to
# accommodate it. Grace owns no batch, which is what gives the batch list a real empty state.
BATCH_ROWS = [
    {
        "ownerEmail": "aarav.mehta@wallet.demo",
        "daysAgo": 2,
        "items": [
            {"recipientEmail": "fatima.noor@wallet.demo", "amountMinor": 2500, "note": "Shared cab"},
            {"recipientEmail": "diego.santos@wallet.demo", "amountMinor": 4000, "note": "Tickets"},
            {"recipientEmail": "grace.obi@wallet.demo", "amountMinor": 1500, "note": "Books"},
        ],
    },
]