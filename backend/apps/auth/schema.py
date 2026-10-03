from apps.shared.validation import Errors, read_email, read_string

from .models import MAX_EMAIL_LENGTH

MAX_PASSWORD_LENGTH = 72


def validate_login(body):
    errors = Errors()
    email = read_email(errors, body, "email", MAX_EMAIL_LENGTH)
    password = read_string(errors, body, "password", minimum=1, maximum=MAX_PASSWORD_LENGTH)
    errors.raise_if_any()

    return email, password
