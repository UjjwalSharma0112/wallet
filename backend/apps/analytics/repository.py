from datetime import datetime, time, timedelta, timezone

from bson import ObjectId

from apps.shared.mongo import collection
from apps.wallet.models import LedgerEntry

from .constants import ACTIVITY_KINDS, BUCKET_FORMAT, BUCKET_TIME_ZONE, RECEIVED_KINDS, SENT_KINDS

MIDNIGHT = time.min

# What a group that matched nothing reports, and the shape every group is reduced to.
NO_MOVEMENTS = {"sentMinor": 0, "receivedMinor": 0, "transactionCount": 0, "amountSumMinor": 0}


def period_start(from_date):
    """The first instant of the period, in UTC.

    A caller supplies a calendar date rather than a moment, so the range opens at that
    date's UTC midnight: the date means the whole day, not the instant it was typed.
    """
    return datetime.combine(from_date, MIDNIGHT, tzinfo=timezone.utc)


def period_end(to_date):
    """The instant just past the period's last day, in UTC.

    The endpoints are inclusive, so the last day is included by starting the range at the
    next day's midnight rather than by adding 23:59:59.999 — which would still leave any
    movement the database happened to store at a sub-millisecond resolution outside.
    """
    return datetime.combine(to_date + timedelta(days=1), MIDNIGHT, tzinfo=timezone.utc)


def _bucket_expression():
    """The pipeline expression that names the UTC day a movement belongs to."""
    return {"$dateToString": {"format": BUCKET_FORMAT, "date": "$createdAt", "timezone": BUCKET_TIME_ZONE}}


def _direction_sum(kinds):
    """Sum the amounts of the entries whose kind is one of `kinds`, and nothing else."""
    return {"$sum": {"$cond": [{"$in": ["$kind", kinds]}, "$amountMinor", 0]}}


def _measurements():
    """The figures a group reads off the entries it matched.

    Each total asks only about its own kinds. They cannot be answered by one shared
    conditional, because an entry that is not received is still something: if a single
    condition answered both, the fallback that hands a sent entry to the received total
    would count it twice and the two totals would agree on every wallet whose activity is
    one-sided. The sum of the amounts is kept alongside the count rather than left to be
    re-derived, so the average describes the same entries the totals do.
    """
    return {
        "sentMinor": _direction_sum(SENT_KINDS),
        "receivedMinor": _direction_sum(RECEIVED_KINDS),
        "transactionCount": {"$sum": 1},
        "amountSumMinor": {"$sum": "$amountMinor"},
    }


def aggregate_period(wallet_id, start, end):
    """Sum and bucket one wallet's movements over a half-open range of instants.

    Every figure comes from the ledger, because the ledger is the only record of money that
    actually moved: a transfer that was attempted and refused leaves a `Transfer` row and no
    entry, so a total read from transfers would count an operation that paid nobody. `kind`
    is narrowed to the kinds a counterparty's movement produces, which is what excludes the
    opening credit — the one credit in the ledger that no counterparty sent.

    The totals and the buckets are read in one `$facet`, so a single pass over the matched
    entries answers both and the two cannot disagree about which entries they describe. Days
    with no entries produce no group at all; the service fills them in, because it is the
    service that holds the range the caller asked for.
    """
    pipeline = [
        {
            "$match": {
                "walletId": ObjectId(wallet_id),
                "createdAt": {"$gte": start, "$lt": end},
                "kind": {"$in": ACTIVITY_KINDS},
            }
        },
        {
            "$facet": {
                "totals": [{"$group": {"_id": None, **_measurements()}}],
                "buckets": [{"$group": {"_id": _bucket_expression(), **_measurements()}}],
            }
        },
    ]

    pages = list(collection(LedgerEntry).aggregate(pipeline))
    facet = pages[0] if pages else {}

    totals = (facet.get("totals") or [NO_MOVEMENTS])[0]
    buckets = {row["_id"]: row for row in facet.get("buckets") or []}

    return {
        "totals": {key: totals.get(key, 0) for key in NO_MOVEMENTS},
        "buckets": buckets,
    }