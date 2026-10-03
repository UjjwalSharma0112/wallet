from bson import ObjectId

from apps.wallet import repository

from .base import MongoTestCase

WALLET_PATH = "/api/v1/wallet"
TRANSACTIONS_PATH = "/api/v1/wallet/transactions"


class WalletApiTest(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.aarav = self.create_account(name="Aarav Mehta", email="aarav.mehta@wallet.demo", opening_balance_minor=245000)
        self.fatima = self.create_account(name="Fatima Noor", email="fatima.noor@wallet.demo", opening_balance_minor=12850)
        self.token = self.token_for("aarav.mehta@wallet.demo")

    def test_the_wallet_route_returns_the_callers_wallet(self):
        response = self.api_client(self.token).get(WALLET_PATH)

        self.assertEqual(response.status_code, 200)

        wallet = response.json()["data"]

        self.assertEqual(wallet["userId"], str(self.aarav.id))
        self.assertEqual(wallet["currency"], "INR")
        self.assertEqual(wallet["availableBalanceMinor"], 245000)

    def test_the_wallet_route_requires_authentication(self):
        response = self.api_client().get(WALLET_PATH)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")

    def test_a_wallet_is_never_returned_to_another_account(self):
        response = self.api_client(self.token_for("fatima.noor@wallet.demo")).get(WALLET_PATH)

        wallet = response.json()["data"]

        self.assertEqual(wallet["userId"], str(self.fatima.id))
        self.assertEqual(wallet["availableBalanceMinor"], 12850)
        self.assertNotEqual(wallet["id"], repository.find_wallet_by_user_id(self.aarav.id)["_id"].__str__())

    def test_a_wallet_cannot_be_requested_by_identifier(self):
        other_wallet_id = repository.find_wallet_by_user_id(self.fatima.id)["_id"]

        response = self.api_client(self.token).get(f"{WALLET_PATH}/{other_wallet_id}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    def test_an_account_without_a_wallet_is_reported_as_missing(self):
        account = self.create_account(email="grace.obi@wallet.demo", provision=False)

        response = self.api_client(self.token_for("grace.obi@wallet.demo")).get(WALLET_PATH)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "WALLET_NOT_FOUND")
        self.assertEqual(account.active, True)

    def test_the_wallet_route_cannot_be_written_to(self):
        response = self.api_client(self.token).post(WALLET_PATH, {}, content_type="application/json")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["error"]["code"], "METHOD_NOT_ALLOWED")


class TransactionApiTest(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=245000)
        self.token = self.token_for("aarav.mehta@wallet.demo")
        self.wallet = repository.find_wallet_by_user_id(self.account.id)

    def record(self, direction, amount_minor):
        return repository.create_entry(
            {
                "wallet_id": self.wallet["_id"],
                "user_id": self.account.id,
                "transaction_id": ObjectId(),
                "kind": "OPENING",
                "direction": direction,
                "amount_minor": amount_minor,
                "balance_after_minor": self.wallet["availableBalanceMinor"] + amount_minor,
            }
        )

    def test_the_transactions_route_returns_the_history_newest_first(self):
        self.record("DEBIT", 100)

        response = self.api_client(self.token).get(TRANSACTIONS_PATH)

        self.assertEqual(response.status_code, 200)

        page = response.json()["data"]

        self.assertEqual(page["total"], 2)
        self.assertEqual(page["limit"], 25)
        self.assertEqual(page["offset"], 0)
        self.assertEqual([entry["amountMinor"] for entry in page["entries"]], [100, 245000])
        self.assertNotIn("userId", page["entries"][0])
        self.assertNotIn("walletId", page["entries"][0])

    def test_the_transactions_route_requires_authentication(self):
        response = self.api_client().get(TRANSACTIONS_PATH)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")

    def test_paging_parameters_are_honoured(self):
        self.record("CREDIT", 500)
        self.record("CREDIT", 700)

        response = self.api_client(self.token).get(f"{TRANSACTIONS_PATH}?limit=2&offset=1")

        page = response.json()["data"]

        self.assertEqual(page["limit"], 2)
        self.assertEqual(page["offset"], 1)
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["entries"]), 2)

    def test_an_empty_history_is_reported_as_an_empty_page(self):
        account = self.create_account(email="grace.obi@wallet.demo", opening_balance_minor=0)

        response = self.api_client(self.token_for("grace.obi@wallet.demo")).get(TRANSACTIONS_PATH)

        page = response.json()["data"]

        self.assertEqual(page["entries"], [])
        self.assertEqual(page["total"], 0)
        self.assertEqual(account.active, True)

    def test_a_limit_outside_the_allowed_range_is_rejected(self):
        for query in ("limit=0", "limit=101", "limit=abc", "offset=-1"):
            with self.subTest(query=query):
                response = self.api_client(self.token).get(f"{TRANSACTIONS_PATH}?{query}")

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
