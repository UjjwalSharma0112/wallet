import re
from datetime import datetime, timedelta, timezone

from apps.shared.validation import Errors, expected_message, js_type_name

from .constants import (
    DATE_FORMAT,
    DATE_PATTERN,
    DEFAULT_PERIOD_DAYS,
    INVALID_DATE_MESSAGE,
    MAX_PERIOD_DAYS,
    PERIOD_REVERSED_MESSAGE,
    PERIOD_TOO_LONG_MESSAGE,
)


def read_calendar_date(errors, query_params, field, default):
    """Read one `YYYY-MM-DD` date from the query, returning `default` when it is absent.

    The shape is matched before the date is parsed, because the parser is lenient about
    padding and would read `2026-3-10` as the tenth of March. Being lenient here would give
    one period two spellings, which is a distinction a caller has no reason to make and this
    feature has no reason to accept. An empty value is a mistake rather than an omission, so
    `?from=` is refused instead of being read as absent.

    Dates are read in UTC because that is the timezone the buckets are cut on; reading them
    in the caller's zone instead would make one query answer two different questions
    depending on who asked it.
    """
    raw = query_params.get(field)

    if raw is None:
        return default

    if not isinstance(raw, str):
        errors.add(field, expected_message("string", js_type_name(raw)))

        return None

    if not DATE_PATTERN.match(raw):
        errors.add(field, INVALID_DATE_MESSAGE)

        return None

    try:
        return datetime.strptime(raw, DATE_FORMAT).replace(tzinfo=timezone.utc).date()
    except ValueError:
        errors.add(field, INVALID_DATE_MESSAGE)

        return None


def anchor_period(today, named_from, named_to):
    """Give a period its two endpoints from whatever the caller named.

    A period is a window of `DEFAULT_PERIOD_DAYS` days unless the caller names both of its
    ends. Naming one end anchors that window on the day named — `?from=<day>` reads the days
    from it and `?to=<day>` the days up to it — so either one works for a date in the past.
    Naming neither anchors the window on today, which is what makes the bare query useful
    without making it ambiguous.
    """
    span = timedelta(days=DEFAULT_PERIOD_DAYS - 1)

    if named_from is not None and named_to is not None:
        return named_from, named_to

    if named_from is not None:
        return named_from, named_from + span

    if named_to is not None:
        return named_to - span, named_to

    return today - span, today


def validate_period(query_params):
    """Read the period a caller is asking about, or refuse it."""
    errors = Errors()
    named_from = read_calendar_date(errors, query_params, "from", default=None)
    named_to = read_calendar_date(errors, query_params, "to", default=None)
    from_date = to_date = None

    # An endpoint that would not parse has already said everything there is to say about the
    # period, and comparing it against a missing one would raise here instead.
    if not errors.any():
        from_date, to_date = anchor_period(datetime.now(timezone.utc).date(), named_from, named_to)

        if from_date > to_date:
            errors.add_form(PERIOD_REVERSED_MESSAGE)
        elif (to_date - from_date).days + 1 > MAX_PERIOD_DAYS:
            errors.add_form(PERIOD_TOO_LONG_MESSAGE)

    errors.raise_if_any()

    return from_date, to_date