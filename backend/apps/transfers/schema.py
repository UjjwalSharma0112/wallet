from apps.auth.models import MAX_EMAIL_LENGTH
from apps.shared.errors import AppError
from apps.shared.validation import Errors, read_email, read_number, read_string

from .constants import (
    IDEMPOTENCY_KEY_INVALID_CODE,
    IDEMPOTENCY_KEY_INVALID_MESSAGE,
    IDEMPOTENCY_KEY_PATTERN,
    IDEMPOTENCY_KEY_RESERVED_CODE,
    IDEMPOTENCY_KEY_RESERVED_MESSAGE,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_TRANSFER_MINOR,
    MONEY_REQUEST_KEY_PREFIX,
    MIN_IDEMPOTENCY_KEY_LENGTH,
    REFUND_KEY_PREFIX,
)

RESERVED_KEY_PREFIXES = (MONEY_REQUEST_KEY_PREFIX, REFUND_KEY_PREFIX)

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"


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


def read_idempotency_key(headers):
    """Read the sender's key for this operation, or None when they did not send one.

    A key is optional: the route has always honoured a request without one, and that is
    still what happens, so idempotency applies to the clients that ask for it. A key that
    *is* sent has to be something the server can store and compare, so its format is checked
    here — before the recipient is resolved and before anything is written, so a malformed
    key never reaches the database.

    The keys that are well formed but still refused are those in the namespaces the server
    spends on its own behalf, because an approval and a refund both derive their keys from
    the record they settle, and a client must not be able to claim those names. This check
    is only ever reached from an incoming request: a key the server derived is handed to the
    engine directly and never read back through here.
    """
    raw = headers.get(IDEMPOTENCY_KEY_HEADER)

    if raw is None:
        return None

    key = raw.strip()
    usable = MIN_IDEMPOTENCY_KEY_LENGTH <= len(key) <= MAX_IDEMPOTENCY_KEY_LENGTH

    if not usable or not IDEMPOTENCY_KEY_PATTERN.match(key):
        raise AppError(400, IDEMPOTENCY_KEY_INVALID_CODE, IDEMPOTENCY_KEY_INVALID_MESSAGE)

    if any(key.startswith(prefix) for prefix in RESERVED_KEY_PREFIXES):
        raise AppError(400, IDEMPOTENCY_KEY_RESERVED_CODE, IDEMPOTENCY_KEY_RESERVED_MESSAGE)

    return key
