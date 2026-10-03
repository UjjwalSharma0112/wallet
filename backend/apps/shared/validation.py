import re

from .errors import ValidationError

MISSING = object()
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
OBJECT_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{24}$")


def js_type_name(value):
    if value is None:
        return "undefined"

    if isinstance(value, bool):
        return "boolean"

    if isinstance(value, (int, float)):
        return "number"

    if isinstance(value, str):
        return "string"

    if isinstance(value, list):
        return "array"

    if isinstance(value, dict):
        return "object"

    return "unknown"


def expected_message(kind, received):
    return f"Invalid input: expected {kind}, received {received}"


def too_small_message(subject, limit, unit):
    return f"Too small: expected {subject} to have >={limit} {unit}"


def too_big_message(subject, limit, unit):
    return f"Too big: expected {subject} to have <={limit} {unit}"


class Errors:
    def __init__(self):
        self.field_errors = {}
        self.form_errors = []

    def add(self, field, message):
        self.field_errors.setdefault(field, []).append(message)

    def add_form(self, message):
        self.form_errors.append(message)

    def any(self):
        return bool(self.field_errors or self.form_errors)

    def raise_if_any(self):
        if self.any():
            raise ValidationError(self.form_errors, self.field_errors)


def read_string(errors, body, field, default=MISSING, trim=False, minimum=None, maximum=None):
    value = body.get(field)

    if value is None and default is not MISSING:
        return default

    if not isinstance(value, str):
        errors.add(field, expected_message("string", js_type_name(value)))
        return None

    text = value.strip() if trim else value

    if minimum is not None and len(text) < minimum:
        errors.add(field, too_small_message("string", minimum, "characters"))
        return None

    if maximum is not None and len(text) > maximum:
        errors.add(field, too_big_message("string", maximum, "characters"))
        return None

    return text


def read_email(errors, body, field, maximum):
    value = read_string(errors, body, field)

    if value is None:
        return None

    if not EMAIL_PATTERN.match(value):
        errors.add(field, "Invalid email address")
        return None

    if len(value) > maximum:
        errors.add(field, too_big_message("string", maximum, "characters"))
        return None

    return value.lower()


def read_number(errors, body, field, default=MISSING, minimum=None, maximum=None):
    value = body.get(field)

    if value is None and default is not MISSING:
        return default

    parsed = coerce_number(value)

    if parsed is None:
        errors.add(field, expected_message("number", "NaN"))
        return None

    if parsed != int(parsed):
        errors.add(field, expected_message("int", "number"))
        return None

    if minimum is not None and parsed < minimum:
        errors.add(field, f"Too small: expected number to be >={minimum}")
        return None

    if maximum is not None and parsed > maximum:
        errors.add(field, f"Too big: expected number to be <={maximum}")
        return None

    return int(parsed)


def coerce_number(value):
    if isinstance(value, bool):
        return float(value)

    if isinstance(value, (int, float)):
        return float(value)

    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()

        if not text:
            return 0.0

        try:
            return float(text)
        except ValueError:
            return None

    return None


def is_object_id(value):
    return isinstance(value, str) and bool(OBJECT_ID_PATTERN.match(value))
