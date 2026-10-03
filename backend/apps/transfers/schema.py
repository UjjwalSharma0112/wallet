from apps.auth.models import MAX_EMAIL_LENGTH
from apps.shared.validation import Errors, read_email, read_number, read_string

from .constants import MAX_NOTE_LENGTH, MAX_TRANSFER_MINOR


def read_minor_amount(errors, body, field):
    """Read a money amount given in minor units.

    Booleans are rejected explicitly: `read_number` coerces `True` to 1, which would
    otherwise let `{"amountMinor": true}` transfer one paise. No default is given, so a
    missing amount is reported rather than silently treated as absent.
    """
    if isinstance(body.get(field), bool):
        errors.add(field, "Invalid input: expected int, received boolean")

        return None

    return read_number(errors, body, field, minimum=1, maximum=MAX_TRANSFER_MINOR)


def validate_transfer(body):
    errors = Errors()
    recipient_email = read_email(errors, body, "recipientEmail", MAX_EMAIL_LENGTH)
    amount_minor = read_minor_amount(errors, body, "amountMinor")
    note = read_string(errors, body, "note", default=None, trim=True, maximum=MAX_NOTE_LENGTH)
    errors.raise_if_any()

    return recipient_email, amount_minor, note
