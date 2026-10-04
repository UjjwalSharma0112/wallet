"""What an account's own activity can be learned from, and the vocabulary it is learned in."""

import re

from apps.wallet.constants import (
    REFUND_IN_KIND,
    REFUND_OUT_KIND,
    TRANSFER_IN_KIND,
    TRANSFER_OUT_KIND,
)

# The ledger's five kinds partition exactly three ways, and this module names two of them.
#
# `OPENING` is a credit no counterparty sent: it funds a wallet rather than being paid into
# it. It is part of the balance and is excluded from both totals, which is what makes
# net movement the change in the wallet's own ledger over the period. The other four kinds
# are money that moved between two accounts, so each is either money this account sent or
# money it received, and a refund is one of those rather than a category of its own. The UI
# still tells the two apart by label; the totals do not, because splitting them would mean
# two definitions of what left the wallet.
SENT_KINDS = [TRANSFER_OUT_KIND, REFUND_OUT_KIND]
RECEIVED_KINDS = [TRANSFER_IN_KIND, REFUND_IN_KIND]
ACTIVITY_KINDS = SENT_KINDS + RECEIVED_KINDS

# One bucket per calendar day, on UTC day boundaries.
#
# The requirement to settle was whether a bucket boundary is UTC or the reader's local time.
# UTC is chosen because the boundary is then the same fact for every reader and every
# browser: the server decides which day a movement belongs to, and the reader cannot change
# the answer by being in another timezone or by a laptop whose clock is wrong. The cost is
# that a bucket is labeled with its UTC date, so a reader far from UTC sees a day edge that
# is not their midnight. That is stated in the response rather than hidden, and it is the
# only rule that keeps the figures reproducible.
BUCKET_SIZE_DAY = "DAY"
BUCKET_TIME_ZONE = "UTC"

# The wire format for a calendar date, and the format a bucket label is written in.
DATE_FORMAT = "%Y-%m-%d"
BUCKET_FORMAT = DATE_FORMAT

# What counts as that format. The parser is lenient about padding and would accept
# `2026-3-10`, so the shape is checked first and one period has exactly one spelling.
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# A period is a range of days, both endpoints included, so its length is counted in days.
DEFAULT_PERIOD_DAYS = 30
# The response carries one bucket per day, so the period length is what bounds it.
MAX_PERIOD_DAYS = 366

INVALID_DATE_MESSAGE = "Invalid date: expected a calendar date as YYYY-MM-DD."
PERIOD_REVERSED_MESSAGE = "Invalid period: from must be on or before to, and both endpoints are inclusive."
PERIOD_TOO_LONG_MESSAGE = f"Too long: expected a period to cover at most {MAX_PERIOD_DAYS} days."