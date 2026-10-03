from apps.auth.models import MAX_EMAIL_LENGTH
from apps.shared.validation import Errors, read_email, read_number, read_string
from apps.transfers.constants import MAX_NOTE_LENGTH, MAX_TRANSFER_MINOR
from apps.transfers.schema import read_minor_amount

from .constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, REQUEST_ROLES, REQUEST_STATES


def validate_create(body):
    """Read the request for money a client is asking to make.

    The body is the same shape `POST /transfers` takes and is read by the same helpers,
    because a request and a payment are the same two things said at different times: an
    address, an amount, and an optional note. The amount cap and the note length are the
    transfers app's own, so one figure bounds both.
    """
    errors = Errors()
    approver_email = read_email(errors, body, "recipientEmail", MAX_EMAIL_LENGTH)
    amount_minor = read_minor_amount(errors, body, "amountMinor")
    note = read_string(errors, body, "note", default=None, trim=True, maximum=MAX_NOTE_LENGTH)
    errors.raise_if_any()

    return approver_email, amount_minor, note


def validate_list(query_params):
    """Read the page of requests a caller asked to see.

    An unknown filter value is reported against the filter rather than ignored, so a typo
    cannot silently return a wider list than the one that was asked for.
    """
    errors = Errors()
    role = read_choice(errors, query_params, "role", REQUEST_ROLES)
    state = read_choice(errors, query_params, "state", REQUEST_STATES)
    limit = read_number(errors, query_params, "limit", default=DEFAULT_PAGE_SIZE, minimum=1, maximum=MAX_PAGE_SIZE)
    offset = read_number(errors, query_params, "offset", default=0, minimum=0)
    errors.raise_if_any()

    return role, state, limit, offset


def read_choice(errors, query_params, field, choices):
    """Read an optional filter that has to name one of a fixed set of values."""
    value = query_params.get(field)

    if value is None or value == "":
        return None

    if value not in choices:
        errors.add(field, f"Invalid input: expected one of {', '.join(choices)}")

        return None

    return value
