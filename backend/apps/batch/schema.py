from apps.auth.models import MAX_EMAIL_LENGTH
from apps.shared.errors import AppError
from apps.shared.validation import Errors, js_type_name, read_email, read_number, read_string
from apps.transfers.constants import MAX_NOTE_LENGTH
from apps.transfers.schema import read_minor_amount

from .constants import (
    BATCH_EMPTY_CODE,
    BATCH_EMPTY_MESSAGE,
    BATCH_STATES,
    BATCH_TOO_MANY_ITEMS_MESSAGE,
    DEFAULT_PAGE_SIZE,
    MAX_BATCH_ITEMS,
    MAX_PAGE_SIZE,
)


def validate_create(body):
    """Read the list of payments a client is asking to make.

    Every item is read by the same helpers `POST /transfers` uses, because a payment inside a
    batch is a payment: an address, an amount, and an optional note. What differs is only
    where a problem is reported — an item's problem belongs to that item, so it is reported
    under `items.<n>.<field>` and every other item is still read.

    Errors are raised for the whole list rather than for the first item alone, so a caller
    fixing a batch is shown everything that is wrong with it in one pass instead of one field
    per attempt. The empty list and an over-long list are refused before any item is read,
    because there is nothing to say about an item in a list that may not exist.
    """
    items = body.get("items")

    if items is None:
        raise AppError(400, BATCH_EMPTY_CODE, BATCH_EMPTY_MESSAGE)

    if not isinstance(items, list):
        raise AppError(
            400,
            "VALIDATION_ERROR",
            "A batch must be a list of payments.",
            {"fieldErrors": {"items": [f"Invalid input: expected array, received {js_type_name(items)}"]}},
        )

    if not items:
        raise AppError(400, BATCH_EMPTY_CODE, BATCH_EMPTY_MESSAGE)

    if len(items) > MAX_BATCH_ITEMS:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            BATCH_TOO_MANY_ITEMS_MESSAGE,
            {"fieldErrors": {"items": [BATCH_TOO_MANY_ITEMS_MESSAGE]}},
        )

    errors = Errors()
    read = [read_item(errors, position, item) for position, item in enumerate(items)]

    errors.raise_if_any()

    return read


def read_item(errors, position, item):
    """Read one payment, reporting anything wrong with it under its own position.

    Each item collects its own complaints before they are published, because the shared
    readers report against the field name they were given and two items with the same wrong
    field would otherwise share one list of messages and each be shown the other's. The
    returned list always has one entry per item, so the positions a caller is told about line
    up with the positions the batch is stored under.
    """
    path = lambda name: f"items.{position}.{name}"

    if not isinstance(item, dict):
        errors.add(path("recipientEmail"), f"Invalid input: expected object, received {js_type_name(item)}")

        return None

    own = Errors()
    recipient_email = read_email(own, item, "recipientEmail", MAX_EMAIL_LENGTH)
    amount_minor = read_minor_amount(own, item, "amountMinor")
    note = read_string(own, item, "note", default=None, trim=True, maximum=MAX_NOTE_LENGTH)

    for field, messages in own.field_errors.items():
        errors.field_errors.setdefault(path(field), []).extend(messages)

    return {"position": position, "recipient_email": recipient_email, "amount_minor": amount_minor, "note": note}


def validate_list(query_params):
    """Read the page of batches a caller asked to see.

    An unknown filter value is reported against the filter rather than ignored, so a typo
    cannot silently return a wider list than the one that was asked for.
    """
    errors = Errors()
    state = read_choice(errors, query_params, "state")
    limit = read_number(errors, query_params, "limit", default=DEFAULT_PAGE_SIZE, minimum=1, maximum=MAX_PAGE_SIZE)
    offset = read_number(errors, query_params, "offset", default=0, minimum=0)
    errors.raise_if_any()

    return state, limit, offset


def read_choice(errors, query_params, field, choices=BATCH_STATES):
    """Read an optional filter that has to name one of a fixed set of values."""
    value = query_params.get(field)

    if value is None or value == "":
        return None

    if value not in choices:
        errors.add(field, f"Invalid input: expected one of {', '.join(choices)}")

        return None

    return value