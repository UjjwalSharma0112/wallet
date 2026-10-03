from bson import ObjectId

from .models import UserAccount


def find_active_by_email(email):
    return UserAccount.objects(__raw__={"email": email, "active": True}).as_pymongo().first()


def find_active_by_id(account_id):
    return UserAccount.objects(__raw__={"_id": ObjectId(account_id), "active": True}).as_pymongo().first()


def find_names_by_ids(account_ids):
    """Resolve several account names at once, so a history page costs one query."""
    if not account_ids:
        return {}

    rows = UserAccount.objects(__raw__={"_id": {"$in": list(account_ids)}}).only("name").as_pymongo()

    return {row["_id"]: row["name"] for row in rows}
