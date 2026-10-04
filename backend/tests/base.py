"""Shared test setup for the wallet application.

Tests run against a dedicated `<database>_test` MongoDB database so they can never
touch the seeded application database. The application's own `MONGODB_URI` is reused,
which keeps tests runnable with `manage.py test` and no extra configuration.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wallet_backend.settings")

import django  # noqa: E402

django.setup()

import bcrypt  # noqa: E402
import mongoengine  # noqa: E402
from django.conf import settings  # noqa: E402
from django.test import Client, SimpleTestCase  # noqa: E402

from apps.auth.constants import USER_ROLE  # noqa: E402
from apps.auth.models import UserAccount  # noqa: E402
from apps.auth.repository import find_active_by_id  # noqa: E402
from apps.auth.services import login  # noqa: E402
from apps.batch.models import Batch  # noqa: E402
from apps.money_requests.models import MoneyRequest  # noqa: E402
from apps.refunds.models import Refund, ensure_refund_indexes  # noqa: E402
from apps.shared.mongo import collection  # noqa: E402
from apps.transfers.models import Transfer  # noqa: E402
from apps.transfers.models import ensure_transfer_indexes  # noqa: E402
from apps.wallet.models import LedgerEntry, Wallet  # noqa: E402
from apps.wallet.services import create_wallet  # noqa: E402

TEST_DATABASE_SUFFIX = "_test"
TEST_PASSWORD = "Wallet@2026"
TEST_SALT_ROUNDS = 4
APPLICATION_MODELS = (UserAccount, Wallet, LedgerEntry, Transfer, MoneyRequest, Refund, Batch)


def test_database_uri(uri):
    parts = urlsplit(uri)
    name = parts.path.lstrip("/") or "wallet_db"

    return urlunsplit((parts.scheme, parts.netloc, f"/{name}{TEST_DATABASE_SUFFIX}", parts.query, parts.fragment))


mongoengine.disconnect()
mongoengine.connect(host=test_database_uri(settings.MONGODB_URI), uuidRepresentation="standard")


class MongoTestCase(SimpleTestCase):
    password = TEST_PASSWORD

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        for model in APPLICATION_MODELS:
            model.ensure_indexes()
            # Wiped before the idempotency index is built below, so transfers left behind by
            # an aborted run cannot stop that index from being created.
            model.objects.delete()

        # The idempotency index is partial and MongoEngine cannot declare one, so it is
        # created separately. Tests that assert a key cannot be spent twice are only true
        # while it exists.
        ensure_transfer_indexes()
        ensure_refund_indexes()

    def setUp(self):
        for model in APPLICATION_MODELS:
            model.objects.delete()

    def create_account(
        self, name="Test User", email="test@wallet.demo", opening_balance_minor=0, active=True, provision=True, role=USER_ROLE
    ):
        """An account to sign in as. `role` is last so existing call sites are unaffected."""
        account = UserAccount(
            name=name,
            email=email,
            password_hash=bcrypt.hashpw(self.password.encode(), bcrypt.gensalt(TEST_SALT_ROUNDS)).decode(),
            active=active,
            role=role,
        ).save()

        if provision:
            create_wallet(account.id, opening_balance_minor)

        return account

    def set_stored_role(self, account, role):
        """Change the role on the stored document, as an operator would, not as a request could.

        The role guard reads this value, so writing it here is what a promotion or a demotion
        looks like from the outside: a change in the database that a token already issued
        knows nothing about.
        """
        collection(UserAccount).update_one({"_id": account.id}, {"$set": {"role": role}})

    def store_account_without_role(self, account):
        """Remove the role from the stored document, as an account written before roles existed."""
        collection(UserAccount).update_one({"_id": account.id}, {"$unset": {"role": ""}})

    def token_for(self, email):
        return login(email, self.password)["token"]

    def account_document(self, account):
        """The account in the shape a request carries it, so services can be called directly."""
        return find_active_by_id(str(account.id))

    def api_client(self, token=""):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}

        return Client(**headers)
