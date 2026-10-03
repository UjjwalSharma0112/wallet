"""R8 — roles as the HTTP surface presents them, and as ownership is unaffected by them.

The first half pins that a role travels to the client on the account it already returns, and
that no request can hand itself one. The second half pins the more important guarantee: a
role is a fact about the caller, and it grants nothing over somebody else's money. The
ownership rules R4 and R5 established are the ones doing that work, and these tests exist to
show that adding roles did not loosen either of them.
"""

from bson import ObjectId

from apps.auth.constants import ADMIN_ROLE, MERCHANT_ROLE, USER_ROLE
from apps.auth.models import UserAccount
from apps.money_requests.models import MoneyRequest
from apps.refunds.models import Refund
from apps.shared.mongo import collection
from apps.transfers.models import Transfer
from apps.transfers.services import execute_transfer
from apps.wallet import repository
from apps.wallet.models import LedgerEntry, Wallet

from .base import MongoTestCase

LOGIN_PATH = "/api/v1/auth/login"
SESSION_PATH = "/api/v1/auth/session"
TRANSFERS_PATH = "/api/v1/transfers"
REQUESTS_PATH = "/api/v1/money-requests"
REFUNDS_PATH = "/api/v1/refunds"
WALLET_PATH = "/api/v1/wallet"
HISTORY_PATH = "/api/v1/wallet/transactions"

ADMIN_EMAIL = "admin@wallet.demo"
MERCHANT_EMAIL = "merchant@wallet.demo"
SENDER_EMAIL = "aarav.mehta@wallet.demo"
PAYEE_EMAIL = "fatima.noor@wallet.demo"
STRANGER_EMAIL = "grace.obi@wallet.demo"

SENDER_OPENING = 245000
PAYEE_OPENING = 12850
STRANGER_OPENING = 700
ADMIN_OPENING = 500000

AMOUNT = 12000


class RoleApiTestCase(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.admin = self.create_account(name="Admin", email=ADMIN_EMAIL, opening_balance_minor=ADMIN_OPENING, role=ADMIN_ROLE)
        self.sender = self.create_account(
            name="Aarav Mehta", email=SENDER_EMAIL, opening_balance_minor=SENDER_OPENING
        )
        self.payee = self.create_account(name="Fatima Noor", email=PAYEE_EMAIL, opening_balance_minor=PAYEE_OPENING)
        self.stranger = self.create_account(
            name="Grace Obi", email=STRANGER_EMAIL, opening_balance_minor=STRANGER_OPENING
        )

    def signed_in(self, account):
        return self.api_client(self.token_for(account.email))

    def post(self, path, account, body=None):
        return self.signed_in(account).post(path, body if body is not None else {}, content_type="application/json")

    def login_as(self, email):
        response = self.api_client().post(
            LOGIN_PATH, {"email": email, "password": self.password}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)

        return response.json()["data"]

    def stored_role(self, account):
        return collection(UserAccount).find_one({"_id": account.id}).get("role")

    def sent_transfer(self, sender, recipient, amount_minor, key):
        result, _ = execute_transfer(self.account_document(sender), recipient.email, amount_minor, None, key)

        return ObjectId(result["transfer"]["id"])

    def raise_request(self, requester=None, approver=None):
        """A pending request from one account to another, made through the real route."""
        requester = requester or self.sender
        approver = approver or self.payee

        body = {"recipientEmail": approver.email, "amountMinor": AMOUNT}
        response = self.post(REQUESTS_PATH, requester, body)

        self.assertEqual(response.status_code, 201)

        return response.json()["data"]["id"]

    def financial_state(self):
        return {
            "balances": sorted(wallet["availableBalanceMinor"] for wallet in Wallet.objects.as_pymongo()),
            "entries": LedgerEntry.objects.count(),
            "transfers": Transfer.objects.count(),
            "refunds": Refund.objects.count(),
            "requests": MoneyRequest.objects.count(),
        }

    def stored_request(self, request_id):
        """The request as it is stored, rather than as the model presents it."""
        return MoneyRequest.objects(__raw__={"_id": ObjectId(request_id)}).as_pymongo().first()


class RoleInTheAccountTest(RoleApiTestCase):
    def test_signing_in_returns_the_account_with_its_role(self):
        for account, role in ((self.sender, USER_ROLE), (self.payee, USER_ROLE), (self.admin, ADMIN_ROLE)):
            with self.subTest(email=account.email):
                self.assertEqual(self.login_as(account.email)["account"]["role"], role)

    def test_reading_the_session_returns_the_account_with_its_role(self):
        for account, role in ((self.sender, USER_ROLE), (self.admin, ADMIN_ROLE)):
            with self.subTest(email=account.email):
                response = self.signed_in(account).get(SESSION_PATH)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["data"]["account"]["role"], role)

    def test_the_role_is_added_to_the_account_and_changes_nothing_else(self):
        """The role joins the three fields the account already had, and no more."""
        signed_in = self.login_as(self.sender.email)["account"]
        session = self.signed_in(self.sender).get(SESSION_PATH).json()["data"]["account"]

        self.assertEqual(sorted(signed_in), ["email", "id", "name", "role"])
        self.assertEqual(sorted(session), ["email", "id", "name", "role"])
        self.assertEqual(signed_in["id"], str(self.sender.id))
        self.assertEqual(signed_in["email"], self.sender.email)
        self.assertEqual(signed_in["name"], self.sender.name)

    def test_a_merchant_is_told_it_is_a_merchant(self):
        merchant = self.create_account(name="Merchant", email=MERCHANT_EMAIL, opening_balance_minor=1000, role=MERCHANT_ROLE)

        self.assertEqual(self.login_as(merchant.email)["account"]["role"], MERCHANT_ROLE)

    def test_the_role_reported_is_the_stored_one_rather_than_the_one_in_the_token(self):
        """The account object is built from the document, so it reports what the database says."""
        token = self.token_for(self.sender.email)
        self.set_stored_role(self.sender, ADMIN_ROLE)

        response = self.api_client(token).get(SESSION_PATH)

        self.assertEqual(response.json()["data"]["account"]["role"], ADMIN_ROLE)


class RoleCannotBeHandedToYourselfTest(RoleApiTestCase):
    """No endpoint accepts a role, so a body carrying one is simply not read."""

    def test_a_role_in_a_transfer_body_changes_nothing(self):
        body = {"recipientEmail": PAYEE_EMAIL, "amountMinor": AMOUNT, "role": ADMIN_ROLE}
        response = self.post(TRANSFERS_PATH, self.sender, body)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.stored_role(self.sender), USER_ROLE)
        self.assertEqual(self.signed_in(self.sender).get(SESSION_PATH).json()["data"]["account"]["role"], USER_ROLE)

    def test_a_role_in_a_money_request_body_changes_nothing(self):
        body = {"recipientEmail": PAYEE_EMAIL, "amountMinor": AMOUNT, "role": ADMIN_ROLE}
        response = self.post(REQUESTS_PATH, self.sender, body)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.stored_role(self.sender), USER_ROLE)

    def test_a_role_in_a_refund_body_changes_nothing(self):
        original = self.sent_transfer(self.sender, self.payee, AMOUNT, "injectionrefund01")

        body = {"originalTransferId": str(original), "amountMinor": 1000, "role": ADMIN_ROLE}
        response = self.post(REFUNDS_PATH, self.payee, body)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.stored_role(self.payee), USER_ROLE)

    def test_a_role_in_a_sign_in_body_changes_nothing(self):
        response = self.api_client().post(
            LOGIN_PATH, {"email": SENDER_EMAIL, "password": self.password, "role": ADMIN_ROLE}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stored_role(self.sender), USER_ROLE)
        self.assertEqual(response.json()["data"]["account"]["role"], USER_ROLE)

    def test_no_route_writes_a_role(self):
        """Asked directly rather than assumed: a role changes by an operator, never by a request."""
        request_id = self.raise_request()
        approval = f"{REQUESTS_PATH}/{request_id}/approval"
        paths = (TRANSFERS_PATH, REQUESTS_PATH, REFUNDS_PATH, SESSION_PATH, WALLET_PATH, approval)

        for method in ("post", "put", "patch", "delete"):
            for path in paths:
                with self.subTest(method=method, path=path):
                    client = self.signed_in(self.sender)
                    response = getattr(client, method)(path, {"role": ADMIN_ROLE}, content_type="application/json")

                    self.assertNotEqual(response.status_code, 200, f"{method.upper()} {path} accepted a role")
                    self.assertEqual(self.stored_role(self.sender), USER_ROLE)

    def test_no_request_schema_reads_a_role(self):
        """The guard against a role entering through a body is that no schema looks for one."""
        from apps.money_requests import schema as requests_schema
        from apps.refunds import schema as refunds_schema
        from apps.transfers import schema as transfers_schema
        from apps.wallet import schema as wallet_schema

        for module in (transfers_schema, refunds_schema, requests_schema, wallet_schema):
            with self.subTest(module=module.__name__):
                self.assertNotIn("role", module.__dict__)


class AdminDoesNotOutrankOwnershipTest(RoleApiTestCase):
    """A role is a fact about the caller. It is not a key to anybody else's money.

    Every refusal below is one R4 or R5 already produced on its own. These tests exist to show
    that adding a role created no way around either of them, and that a refusal made for that
    reason moves nothing at all.
    """

    def test_an_admin_cannot_approve_a_request_addressed_to_somebody_else(self):
        request_id = self.raise_request()
        before = self.financial_state()

        response = self.post(f"{REQUESTS_PATH}/{request_id}/approval", self.admin)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "REQUEST_ACTION_NOT_ALLOWED")
        self.assertEqual(self.financial_state(), before)

        stored = self.stored_request(request_id)

        self.assertEqual(stored["state"], "PENDING")
        self.assertIsNone(stored.get("transferId"))
        self.assertIsNone(stored.get("resolvedAt"))

    def test_an_admin_cannot_decline_a_request_addressed_to_somebody_else(self):
        request_id = self.raise_request()
        before = self.financial_state()

        response = self.post(f"{REQUESTS_PATH}/{request_id}/decline", self.admin)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "REQUEST_ACTION_NOT_ALLOWED")
        self.assertEqual(self.financial_state(), before)
        self.assertEqual(self.stored_request(request_id)["state"], "PENDING")

    def test_an_admin_refusing_a_request_leaves_it_answerable_by_the_account_it_was_asked_of(self):
        """The strongest form of the same claim: the refusal did not use the request up."""
        request_id = self.raise_request()

        self.assertEqual(self.post(f"{REQUESTS_PATH}/{request_id}/approval", self.admin).status_code, 403)

        approved = self.post(f"{REQUESTS_PATH}/{request_id}/approval", self.payee)

        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["data"]["request"]["state"], "APPROVED")
        self.assertEqual(Transfer.objects.count(), 1)

    def test_an_admin_cannot_refund_a_transfer_it_did_not_receive(self):
        original = self.sent_transfer(self.sender, self.payee, AMOUNT, "adminownrefund1")
        before = self.financial_state()

        response = self.post(REFUNDS_PATH, self.admin, {"originalTransferId": str(original), "amountMinor": 1000})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "REFUND_ACTION_NOT_ALLOWED")
        self.assertEqual(self.financial_state(), before)
        self.assertEqual(Refund.objects.count(), 0)
        self.assertEqual(self.stored_role(self.admin), ADMIN_ROLE)

    def test_an_admin_cannot_refund_a_transfer_of_its_own_that_it_never_received(self):
        """The sender of a transfer is not its payee, and no role makes it one."""
        original = self.sent_transfer(self.sender, self.payee, AMOUNT, "senderrefund001")

        body = {"originalTransferId": str(original), "amountMinor": 1000}
        response = self.post(REFUNDS_PATH, self.sender, body)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "REFUND_ACTION_NOT_ALLOWED")
        self.assertEqual(Refund.objects.count(), 0)

    def test_a_refund_of_a_transfer_that_does_not_exist_is_not_told_apart_from_another_accounts(self):
        """A refusal must not become a way of finding out that a transfer is somebody else's."""
        original = self.sent_transfer(self.sender, self.payee, AMOUNT, "existencecheck01")

        someone_elses = self.post(REFUNDS_PATH, self.stranger, {"originalTransferId": str(original), "amountMinor": 1000})
        never_existed = self.post(REFUNDS_PATH, self.stranger, {"originalTransferId": str(ObjectId()), "amountMinor": 1000})

        self.assertEqual(someone_elses.status_code, 403)
        self.assertEqual(never_existed.status_code, 404)
        self.assertEqual(someone_elses.json()["error"]["code"], "REFUND_ACTION_NOT_ALLOWED")
        self.assertEqual(never_existed.json()["error"]["code"], "ORIGINAL_TRANSFER_NOT_FOUND")

    def test_an_admin_refusing_a_refund_leaves_the_payee_able_to_make_it(self):
        original = self.sent_transfer(self.sender, self.payee, AMOUNT, "payeecanrefund")

        refused = self.post(REFUNDS_PATH, self.admin, {"originalTransferId": str(original), "amountMinor": 1000})

        self.assertEqual(refused.status_code, 403)

        response = self.post(REFUNDS_PATH, self.payee, {"originalTransferId": str(original), "amountMinor": 1000})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["data"]["refund"]["state"], "COMPLETED")

    def test_an_admin_sees_only_its_own_wallet_and_its_own_ledger(self):
        """A role grants no read over anybody else, because no read is addressed to anybody else."""
        wallet = self.signed_in(self.admin).get(WALLET_PATH)
        history = self.signed_in(self.admin).get(HISTORY_PATH)
        entries = history.json()["data"]["entries"]

        self.assertEqual(wallet.json()["data"]["availableBalanceMinor"], ADMIN_OPENING)
        self.assertEqual([entry["kind"] for entry in entries], ["OPENING"])
        self.assertEqual({entry["amountMinor"] for entry in entries}, {ADMIN_OPENING})

    def test_an_admin_still_reads_an_ordinary_wallet(self):
        self.sent_transfer(self.sender, self.payee, AMOUNT, "adminreadsothers")

        balances = {
            account.email: self.signed_in(account).get(WALLET_PATH).json()["data"]["availableBalanceMinor"]
            for account in (self.sender, self.payee, self.admin)
        }

        self.assertEqual(balances[SENDER_EMAIL], SENDER_OPENING - AMOUNT)
        self.assertEqual(balances[PAYEE_EMAIL], PAYEE_OPENING + AMOUNT)
        self.assertEqual(balances[ADMIN_EMAIL], ADMIN_OPENING)


class RoleGrantsNoFinancialCapabilityTest(RoleApiTestCase):
    """MERCHANT carries nothing a USER lacks. That is a stated decision, not a shortcut."""

    def setUp(self):
        super().setUp()

        self.merchant = self.create_account(
            name="Merchant", email="merchant@wallet.demo", opening_balance_minor=90000, role=MERCHANT_ROLE
        )

    def test_a_merchant_can_send_money_exactly_as_a_user_can(self):
        response = self.post(TRANSFERS_PATH, self.merchant, {"recipientEmail": PAYEE_EMAIL, "amountMinor": 4500})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["data"]["transfer"]["state"], "COMPLETED")
        self.assertEqual(response.json()["data"]["wallet"]["availableBalanceMinor"], 85500)

    def test_a_merchant_can_answer_a_request_addressed_to_it_exactly_as_a_user_can(self):
        request_id = self.raise_request(requester=self.sender, approver=self.merchant)

        response = self.post(f"{REQUESTS_PATH}/{request_id}/approval", self.merchant)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["request"]["state"], "APPROVED")

    def test_a_merchant_can_refund_a_transfer_it_received_exactly_as_a_user_can(self):
        original = self.sent_transfer(self.sender, self.merchant, AMOUNT, "merchantrefunds")

        response = self.post(REFUNDS_PATH, self.merchant, {"originalTransferId": str(original), "amountMinor": 2500})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["data"]["refund"]["state"], "COMPLETED")

    def test_a_merchant_is_refused_another_accounts_money_request(self):
        """Sharing a role with USER does not make it the same person as the account being asked."""
        request_id = self.raise_request(requester=self.sender, approver=self.payee)

        response = self.post(f"{REQUESTS_PATH}/{request_id}/approval", self.merchant)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "REQUEST_ACTION_NOT_ALLOWED")