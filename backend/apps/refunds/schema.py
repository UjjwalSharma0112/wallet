from apps.shared.validation import Errors, is_object_id, read_number, read_string
from apps.transfers.constants import MAX_NOTE_LENGTH
from apps.transfers.schema import read_minor_amount

from .constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE


def validate_create(body):
    """Read the refund a payee is asking to make.

    The body names the original transfer, an amount, and an optional note — the three
    things a compensating entry needs. The amount is read by the transfers app's own
    helper, so one figure bounds both a transfer and its refund.
    """
    errors = Errors()
    original_transfer_id = read_string(errors, body, "originalTransferId", trim=True)
    amount_minor = read_minor_amount(errors, body, "amountMinor")
    note = read_string(errors, body, "note", default=None, trim=True, maximum=MAX_NOTE_LENGTH)

    if original_transfer_id is not None and not is_object_id(original_transfer_id):
        errors.add("originalTransferId", "Enter a valid transfer id.")

    errors.raise_if_any()

    return original_transfer_id, amount_minor, note


def validate_list(query_params):
    errors = Errors()
    limit = read_number(errors, query_params, "limit", default=DEFAULT_PAGE_SIZE, minimum=1, maximum=MAX_PAGE_SIZE)
    offset = read_number(errors, query_params, "offset", default=0, minimum=0)
    errors.raise_if_any()

    return limit, offset
