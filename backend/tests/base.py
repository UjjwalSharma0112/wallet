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

from apps.auth.models import UserAccount  # noqa: E402
from apps.auth.services import login  # noqa: E402
from apps.wallet.models import LedgerEntry, Wallet  # noqa: E402
from apps.wallet.services import create_wallet  # noqa: E402

TEST_DATABASE_SUFFIX = "_test"
TEST_PASSWORD = "Wallet@2026"
TEST_SALT_ROUNDS = 4
APPLICATION_MODELS = (UserAccount, Wallet, LedgerEntry)


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

    def setUp(self):
        for model in APPLICATION_MODELS:
            model.objects.delete()

    def create_account(self, name="Test User", email="test@wallet.demo", opening_balance_minor=0, active=True, provision=True):
        account = UserAccount(
            name=name,
            email=email,
            password_hash=bcrypt.hashpw(self.password.encode(), bcrypt.gensalt(TEST_SALT_ROUNDS)).decode(),
            active=active,
        ).save()

        if provision:
            create_wallet(account.id, opening_balance_minor)

        return account

    def token_for(self, email):
        return login(email, self.password)["token"]

    def api_client(self, token=""):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}

        return Client(**headers)
