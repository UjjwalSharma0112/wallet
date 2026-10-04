from datetime import timedelta

from apps.wallet import services as wallet_services

from . import repository
from .constants import BUCKET_SIZE_DAY, BUCKET_TIME_ZONE, DATE_FORMAT


def period_days(from_date, to_date):
    """Every calendar day of the period, labeled the way a bucket is labeled.

    The label is built here rather than taken from the database so that a day the database
    had nothing to say about is labeled identically to one it did.
    """
    day_count = (to_date - from_date).days + 1
    dates = [from_date + timedelta(days=offset) for offset in range(day_count)]

    return [date.strftime(DATE_FORMAT) for date in dates]


def bucket_row(label, measured):
    """One day of the series, whether or not anything happened on it.

    A day with no movements is reported as a day with zeros rather than left out, so the
    series always covers exactly the range that was asked for. Leaving gaps out would make
    the caller infer them, and a caller drawing bars cannot tell an absent bar from a bar
    of no height.
    """
    measured = measured or {}

    return {
        "bucket": label,
        "sentMinor": measured.get("sentMinor", 0),
        "receivedMinor": measured.get("receivedMinor", 0),
        "transactionCount": measured.get("transactionCount", 0),
    }


def average_amount(totals):
    """The mean size of a movement in the period, in whole paise.

    Integer division, so a period whose movements do not divide evenly reports the paise
    figure just below the exact mean rather than a fraction of a paise — money is a whole
    number of paise everywhere else in this response, and a mean that was not would be the
    only float in it. A period with no movements averages nothing, and is reported as zero.
    """
    count = totals["transactionCount"]

    if not count:
        return 0

    return totals["amountSumMinor"] // count


def account_analytics(account_id, from_date, to_date):
    """What one account's own activity adds up to over a period.

    The wallet is resolved from the account the request authenticated as, so there is no
    argument through which a caller could name somebody else's money. Nothing is written:
    every figure here is a sum of entries that were recorded when the money moved, which is
    what makes the call safe to repeat and safe to make while other transfers are running.
    """
    wallet = wallet_services.find_wallet(account_id)
    measured = repository.aggregate_period(
        wallet["_id"], repository.period_start(from_date), repository.period_end(to_date)
    )
    totals = measured["totals"]
    buckets = measured["buckets"]

    return {
        "currency": wallet["currency"],
        "from": from_date.strftime(DATE_FORMAT),
        "to": to_date.strftime(DATE_FORMAT),
        "bucketSize": BUCKET_SIZE_DAY,
        "timeZone": BUCKET_TIME_ZONE,
        "sentMinor": totals["sentMinor"],
        "receivedMinor": totals["receivedMinor"],
        "netMovementMinor": totals["receivedMinor"] - totals["sentMinor"],
        "transactionCount": totals["transactionCount"],
        "averageAmountMinor": average_amount(totals),
        "volume": [bucket_row(label, buckets.get(label)) for label in period_days(from_date, to_date)],
    }