from datetime import datetime, timezone

from mongoengine import DateTimeField, Document

from .mongo import collection


def now_utc():
    return datetime.now(timezone.utc)


def stamp(document):
    moment = now_utc()

    if document.created_at is None:
        document.created_at = moment

    document.updated_at = moment

    return document


def ensure_index(model, name, fields, **options):
    """Create an index that a model cannot declare for itself.

    MongoEngine only applies a partial index expression when it creates the index, so a
    model that needs one asks the database for it directly. Creating an index that already
    exists with the same definition is a no-op, so this is safe to repeat.
    """
    collection(model).create_index(fields, name=name, **options)


class TimestampedDocument(Document):
    meta = {"abstract": True}

    created_at = DateTimeField(db_field="createdAt")
    updated_at = DateTimeField(db_field="updatedAt")

    def save(self, *args, **kwargs):
        stamp(self)

        return super().save(*args, **kwargs)
