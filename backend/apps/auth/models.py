from mongoengine import BooleanField, StringField

from apps.shared.documents import TimestampedDocument

from .constants import ROLES, USER_ROLE

MAX_NAME_LENGTH = 120
MAX_EMAIL_LENGTH = 254


class UserAccount(TimestampedDocument):
    meta = {
        "collection": "useraccounts",
        "indexes": [{"fields": ["email"], "unique": True, "name": "email_unique_idx"}],
    }

    name = StringField(required=True, max_length=MAX_NAME_LENGTH)
    email = StringField(required=True, max_length=MAX_EMAIL_LENGTH)
    password_hash = StringField(db_field="passwordHash", required=True)
    active = BooleanField(default=True)
    # What the account is permitted to do, as opposed to what it owns. It is deliberately not
    # writable by any request: an account that could promote itself would make every other
    # role in this application meaningless. Absence reads as USER, so an account written
    # before roles existed is an ordinary holder rather than an unusable one.
    role = StringField(required=True, choices=ROLES, default=USER_ROLE)
