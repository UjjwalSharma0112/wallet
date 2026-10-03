from mongoengine import BooleanField, StringField

from apps.shared.documents import TimestampedDocument

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
