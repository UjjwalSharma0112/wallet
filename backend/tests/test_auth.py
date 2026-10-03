from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings

from apps.auth.models import UserAccount

from .base import MongoTestCase

LOGIN_PATH = "/api/v1/auth/login"
SESSION_PATH = "/api/v1/auth/session"


class LoginTest(MongoTestCase):
    def login(self, email, password=None):
        return self.api_client().post(
            LOGIN_PATH,
            {"email": email, "password": self.password if password is None else password},
            content_type="application/json",
        )

    def test_login_returns_the_account_and_a_token(self):
        account = self.create_account(name="Aarav Mehta", email="aarav.mehta@wallet.demo")

        response = self.login("aarav.mehta@wallet.demo")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["data"]["account"],
            {"id": str(account.id), "name": "Aarav Mehta", "email": "aarav.mehta@wallet.demo"},
        )
        self.assertTrue(response.json()["data"]["token"])

    def test_login_matches_the_email_without_regard_to_case(self):
        self.create_account(email="aarav.mehta@wallet.demo")

        response = self.login("AARAV.MEHTA@wallet.demo")

        self.assertEqual(response.status_code, 200)

    def test_login_rejects_an_incorrect_password(self):
        self.create_account(email="aarav.mehta@wallet.demo")

        response = self.login("aarav.mehta@wallet.demo", password="wrong-password")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_login_rejects_an_unknown_email(self):
        response = self.login("nobody@wallet.demo")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_login_rejects_an_inactive_account(self):
        self.create_account(email="aarav.mehta@wallet.demo", active=False)

        response = self.login("aarav.mehta@wallet.demo")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_login_rejects_a_malformed_email(self):
        response = self.login("not-an-email")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("email", response.json()["error"]["details"]["fieldErrors"])

    def test_login_rejects_a_missing_password(self):
        response = self.api_client().post(LOGIN_PATH, {"email": "aarav.mehta@wallet.demo"}, content_type="application/json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.json()["error"]["details"]["fieldErrors"])

    def test_login_rejects_a_password_longer_than_bcrypt_accepts(self):
        self.create_account(email="aarav.mehta@wallet.demo")

        response = self.login("aarav.mehta@wallet.demo", password="x" * 73)

        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.json()["error"]["details"]["fieldErrors"])


class SessionTest(MongoTestCase):
    def test_session_returns_the_account_for_a_valid_token(self):
        account = self.create_account(name="Fatima Noor", email="fatima.noor@wallet.demo")
        token = self.token_for("fatima.noor@wallet.demo")

        response = self.api_client(token).get(SESSION_PATH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["account"]["id"], str(account.id))

    def test_session_requires_authorization(self):
        response = self.api_client().get(SESSION_PATH)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")

    def test_session_rejects_an_empty_bearer_token(self):
        response = self.api_client(" ").get(SESSION_PATH)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")

    def test_session_rejects_a_malformed_token(self):
        response = self.api_client("not-a-token").get(SESSION_PATH)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_TOKEN")

    def test_session_rejects_a_token_signed_with_another_secret(self):
        account = self.create_account(email="aarav.mehta@wallet.demo")
        issued_at = datetime.now(timezone.utc)
        foreign_token = jwt.encode(
            {
                "sub": str(account.id),
                "type": "session",
                "iat": issued_at,
                "exp": issued_at + timedelta(hours=1),
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
            },
            "a-different-secret",
            algorithm="HS256",
        )

        response = self.api_client(foreign_token).get(SESSION_PATH)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_TOKEN")

    def test_session_rejects_an_expired_token(self):
        account = self.create_account(email="aarav.mehta@wallet.demo")
        issued_at = datetime.now(timezone.utc) - timedelta(hours=2)
        expired_token = jwt.encode(
            {
                "sub": str(account.id),
                "type": "session",
                "iat": issued_at,
                "exp": issued_at + timedelta(hours=1),
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
            },
            settings.JWT_SECRET,
            algorithm="HS256",
        )

        response = self.api_client(expired_token).get(SESSION_PATH)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_TOKEN")

    def test_session_rejects_a_deactivated_account(self):
        self.create_account(email="aarav.mehta@wallet.demo")
        token = self.token_for("aarav.mehta@wallet.demo")
        UserAccount.objects(__raw__={"email": "aarav.mehta@wallet.demo"}).update(set__active=False)

        response = self.api_client(token).get(SESSION_PATH)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "ACCOUNT_UNAVAILABLE")
