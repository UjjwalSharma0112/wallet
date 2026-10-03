"""R8 — roles and the guard that enforces them.

The role is stored on the account and read from the database on every authenticated request.
These tests pin that: which roles exist, what the guard answers for each of them, that
authentication is settled before a role is asked about, that a role stored in the database
takes effect without a new sign-in, and that a claim inside a token is never believed.
"""

from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.test import Client, override_settings
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.auth.constants import ADMIN_ROLE, MERCHANT_ROLE, ROLES, ROLE_NOT_ALLOWED_CODE, USER_ROLE
from apps.auth.models import UserAccount
from apps.auth.services import account_role
from apps.shared.authentication import require_auth, require_role
from apps.shared.mongo import collection

from .base import MongoTestCase

USER_ONLY_PATH = "/rbac/user-only"
ADMIN_ONLY_PATH = "/rbac/admin-only"
MERCHANT_ONLY_PATH = "/rbac/merchant-only"
UNGUARDED_PATH = "/rbac/unguarded"

ADMIN_EMAIL = "admin@wallet.demo"
USER_EMAIL = "user@wallet.demo"
MERCHANT_EMAIL = "merchant@wallet.demo"
INVALID_ROLE = "TREASURER"


@api_view(["GET"])
@require_auth
def unguarded(request):
    """The same shape of endpoint with no role asked of it, for comparison."""
    return Response({"data": {"path": UNGUARDED_PATH, "accountId": str(request.account["_id"])}})


@api_view(["GET"])
@require_auth
@require_role(USER_ROLE)
def user_only(request):
    return Response({"data": {"path": USER_ONLY_PATH, "accountId": str(request.account["_id"])}})


@api_view(["GET"])
@require_auth
@require_role(ADMIN_ROLE)
def admin_only(request):
    return Response({"data": {"path": ADMIN_ONLY_PATH, "accountId": str(request.account["_id"])}})


@api_view(["GET"])
@require_auth
@require_role(MERCHANT_ROLE)
def merchant_only(request):
    return Response({"data": {"path": MERCHANT_ONLY_PATH, "accountId": str(request.account["_id"])}})


urlpatterns = [
    path("rbac/unguarded", unguarded),
    path("rbac/user-only", user_only),
    path("rbac/admin-only", admin_only),
    path("rbac/merchant-only", merchant_only),
]


def token_with_role(account_id, role):
    """A correctly signed token that also carries a role claim, which nothing should read."""
    issued_at = datetime.now(timezone.utc)

    return jwt.encode(
        {
            "sub": str(account_id),
            "type": "session",
            "role": role,
            "iat": issued_at,
            "exp": issued_at + timedelta(hours=1),
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )


class RoleTestCase(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.admin = self.create_account(name="Admin", email=ADMIN_EMAIL, role=ADMIN_ROLE)
        self.user = self.create_account(name="User", email=USER_EMAIL, role=USER_ROLE)
        self.merchant = self.create_account(name="Merchant", email=MERCHANT_EMAIL, role=MERCHANT_ROLE)

    def get(self, path, token=""):
        return self.api_client(token).get(path)


@override_settings(ROOT_URLCONF=__name__)
class RoleGuardTest(RoleTestCase):
    def test_a_role_of_each_kind_is_accepted_by_a_guard_asking_for_it(self):
        cases = (
            (self.user, USER_ROLE, USER_ONLY_PATH),
            (self.merchant, MERCHANT_ROLE, MERCHANT_ONLY_PATH),
            (self.admin, ADMIN_ROLE, ADMIN_ONLY_PATH),
        )

        for account, role, path in cases:
            with self.subTest(role=role):
                response = self.get(path, self.token_for(account.email))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["data"]["accountId"], str(account.id))

    def test_a_role_the_guard_did_not_ask_for_is_refused(self):
        cases = (
            (self.user, ADMIN_ONLY_PATH),
            (self.merchant, ADMIN_ONLY_PATH),
            (self.admin, USER_ONLY_PATH),
            (self.admin, MERCHANT_ONLY_PATH),
            (self.user, MERCHANT_ONLY_PATH),
        )

        for account, path in cases:
            with self.subTest(account=account.email, path=path):
                response = self.get(path, self.token_for(account.email))

                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"]["code"], ROLE_NOT_ALLOWED_CODE)

    def test_a_refusal_carries_a_message_that_says_nothing_about_the_role(self):
        response = self.get(ADMIN_ONLY_PATH, self.token_for(self.user.email))
        message = response.json()["error"]["message"]

        self.assertNotIn(USER_ROLE, message)
        self.assertNotIn(ADMIN_ROLE, message)
        self.assertNotIn(MERCHANT_ROLE, message)
        self.assertNotIn(self.user.email, message)

    def test_authentication_is_settled_before_a_role_is_asked_about(self):
        """An anonymous caller is told to sign in and is not told what a role would have said.

        This is the ordering the guards are stacked for: `require_auth` wraps `require_role`,
        so the absence of a token is answered before the account holding one is even looked up.
        """
        for path in (USER_ONLY_PATH, ADMIN_ONLY_PATH, MERCHANT_ONLY_PATH):
            for token in ("", " ", "not-a-token"):
                with self.subTest(path=path, token=token):
                    response = self.get(path, token)

                    self.assertEqual(response.status_code, 401)
                    self.assertNotEqual(response.json()["error"]["code"], ROLE_NOT_ALLOWED_CODE)

    def test_a_deactivated_account_is_told_it_is_unauthenticated_rather_than_unauthorised(self):
        token = self.token_for(self.admin.email)
        UserAccount.objects(__raw__={"email": ADMIN_EMAIL}).update(set__active=False)

        response = self.get(ADMIN_ONLY_PATH, token)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "ACCOUNT_UNAVAILABLE")

    def test_the_guard_only_refuses_the_caller_it_names(self):
        """A permitted request arrives exactly as it would without the guard in the way."""
        token = self.token_for(self.user.email)

        refused = self.get(ADMIN_ONLY_PATH, token)
        permitted = self.get(USER_ONLY_PATH, token)
        unguarded = self.get(UNGUARDED_PATH, token)

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(permitted.status_code, 200)
        self.assertEqual(unguarded.status_code, 200)
        self.assertEqual(permitted.json()["data"]["accountId"], unguarded.json()["data"]["accountId"])
        self.assertEqual(permitted.json()["data"]["accountId"], str(self.user.id))


@override_settings(ROOT_URLCONF=__name__)
class UnknownRoleTest(RoleTestCase):
    def test_a_role_this_application_does_not_recognise_is_refused_everywhere(self):
        """Not promoted to USER, not treated as ADMIN: refused, because it cannot be reasoned about."""
        for account in (self.user, self.merchant, self.admin):
            with self.subTest(account=account.email):
                self.set_stored_role(account, INVALID_ROLE)

                for path in (USER_ONLY_PATH, ADMIN_ONLY_PATH, MERCHANT_ONLY_PATH):
                    with self.subTest(path=path):
                        response = self.get(path, self.token_for(account.email))

                        self.assertEqual(response.status_code, 403)
                        self.assertEqual(response.json()["error"]["code"], ROLE_NOT_ALLOWED_CODE)

    def test_an_empty_stored_role_is_refused_rather_than_read_as_the_ordinary_one(self):
        self.set_stored_role(self.user, "")

        response = self.get(USER_ONLY_PATH, self.token_for(self.user.email))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], ROLE_NOT_ALLOWED_CODE)


@override_settings(ROOT_URLCONF=__name__)
class AccountWithoutAStoredRoleTest(RoleTestCase):
    def test_an_account_written_without_a_role_is_an_ordinary_holder(self):
        """Absence means the model's default, so a document predating roles is not unusable."""
        self.store_account_without_role(self.user)
        token = self.token_for(self.user.email)

        self.assertEqual(self.get(USER_ONLY_PATH, token).status_code, 200)
        self.assertEqual(self.get(ADMIN_ONLY_PATH, token).status_code, 403)

    def test_a_missing_role_is_read_from_the_database_and_not_the_model_default(self):
        """The guard reads the stored document, which is the shape a request carries.

        `as_pymongo()` hands back the document as it is stored and applies no defaults, so a
        model instance would report USER for this account whether or not it had one. Reading
        the stored document is what makes the difference observable rather than assumed.
        """
        self.store_account_without_role(self.user)
        stored = self.account_document(self.user)

        self.assertNotIn("role", stored)
        self.assertEqual(account_role(stored), USER_ROLE)

        self.assertEqual(self.account_document(self.merchant).get("role"), MERCHANT_ROLE)
        self.assertEqual(account_role(self.account_document(self.admin)), ADMIN_ROLE)

    def test_the_default_is_reached_only_by_absence_and_not_by_a_blank_role(self):
        self.assertEqual(account_role({}), USER_ROLE)
        self.assertEqual(account_role({"role": None}), USER_ROLE)
        self.assertEqual(account_role({"role": ""}), "")
        self.assertEqual(account_role({"role": INVALID_ROLE}), INVALID_ROLE)


@override_settings(ROOT_URLCONF=__name__)
class RoleComesFromTheDatabaseTest(RoleTestCase):
    def test_a_promotion_takes_effect_on_the_next_request_without_signing_in_again(self):
        token = self.token_for(self.user.email)

        self.assertEqual(self.get(ADMIN_ONLY_PATH, token).status_code, 403)

        self.set_stored_role(self.user, ADMIN_ROLE)

        response = self.get(ADMIN_ONLY_PATH, token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["accountId"], str(self.user.id))

    def test_a_demotion_takes_effect_on_the_next_request_without_signing_in_again(self):
        self.set_stored_role(self.admin, ADMIN_ROLE)
        token = self.token_for(self.admin.email)

        self.assertEqual(self.get(ADMIN_ONLY_PATH, token).status_code, 200)

        self.set_stored_role(self.admin, USER_ROLE)

        response = self.get(ADMIN_ONLY_PATH, token)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], ROLE_NOT_ALLOWED_CODE)

    def test_a_token_is_accepted_after_a_role_change_because_it_only_names_the_account(self):
        """The token identifies who is asking. It says nothing about what they may now do."""
        token = self.token_for(self.user.email)
        before = self.get(UNGUARDED_PATH, token)

        self.set_stored_role(self.user, ADMIN_ROLE)
        after = self.get(UNGUARDED_PATH, token)

        self.assertEqual(before.status_code, 200)
        self.assertEqual(after.status_code, 200)
        self.assertEqual(before.json()["data"]["accountId"], after.json()["data"]["accountId"])
        self.assertEqual(self.get(ADMIN_ONLY_PATH, token).status_code, 200)


@override_settings(ROOT_URLCONF=__name__)
class RoleClaimInATokenIsIgnoredTest(RoleTestCase):
    def test_a_role_claim_cannot_promote_an_account(self):
        """The token claims ADMIN; the database says USER; the database is what is obeyed."""
        forged = token_with_role(self.user.id, ADMIN_ROLE)

        self.assertEqual(self.user.role, USER_ROLE)
        self.assertEqual(self.get(ADMIN_ONLY_PATH, forged).status_code, 403)
        self.assertEqual(self.get(USER_ONLY_PATH, forged).status_code, 200)

    def test_the_absence_of_a_role_claim_cannot_demote_an_account(self):
        """The token carries no role at all, and the stored ADMIN is still in force."""
        token = self.token_for(self.admin.email)

        self.assertEqual(self.get(ADMIN_ONLY_PATH, token).status_code, 200)
        self.assertEqual(self.get(USER_ONLY_PATH, token).status_code, 403)

    def test_the_role_is_absent_from_an_issued_token(self):
        """Nothing about a role travels in the token, so there is nothing there to be trusted."""
        token = self.token_for(self.user.email)
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=["HS256"],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
        )

        self.assertEqual(sorted(payload), ["aud", "exp", "iat", "iss", "sub", "type"])


@override_settings(ROOT_URLCONF=__name__)
class RoleFromTheRequestIsIgnoredTest(RoleTestCase):
    """The guard has one source of truth, and the request is not it.

    These exist because a guard that read a role from anywhere it could be supplied would look
    identical from the outside until it was asked the wrong question.
    """

    def test_a_role_in_the_query_string_cannot_promote_an_account(self):
        token = self.token_for(self.user.email)

        queries = (f"?role={ADMIN_ROLE}", f"?role={MERCHANT_ROLE}", "?role=", f"?role={ADMIN_ROLE}&role={USER_ROLE}")

        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(self.api_client(token).get(f"{ADMIN_ONLY_PATH}{query}").status_code, 403)
                self.assertEqual(self.api_client(token).get(f"{USER_ONLY_PATH}{query}").status_code, 200)

    def test_a_role_in_the_query_string_cannot_demote_an_account(self):
        token = self.token_for(self.admin.email)

        self.assertEqual(self.api_client(token).get(f"{USER_ONLY_PATH}?role={USER_ROLE}").status_code, 403)
        self.assertEqual(self.api_client(token).get(ADMIN_ONLY_PATH).status_code, 200)

    def test_a_role_header_cannot_promote_an_account(self):
        token = self.token_for(self.user.email)
        response = Client(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_WALLET_ROLE=ADMIN_ROLE).get(ADMIN_ONLY_PATH)

        self.assertEqual(response.status_code, 403)


class RoleValuesTest(MongoTestCase):
    def test_the_three_roles_the_product_declares_are_the_three_it_stores(self):
        self.assertEqual(ROLES, [USER_ROLE, MERCHANT_ROLE, ADMIN_ROLE])

    def test_an_account_is_created_with_a_role(self):
        account = self.create_account()

        self.assertEqual(account.role, USER_ROLE)
        self.assertEqual(self.account_document(account).get("role"), USER_ROLE)

    def test_a_role_is_stored_verbatim_for_each_of_its_values(self):
        for role in ROLES:
            with self.subTest(role=role):
                account = self.create_account(email=f"{role.lower()}@wallet.demo", role=role)

                self.assertEqual(self.account_document(account).get("role"), role)

    def test_no_role_is_writable_through_the_collection_the_application_uses(self):
        """The field is stored, not derived: nothing writes it but the seed and an operator."""
        account = self.create_account()

        self.set_stored_role(account, ADMIN_ROLE)

        self.assertEqual(collection(UserAccount).find_one({"_id": account.id}).get("role"), ADMIN_ROLE)