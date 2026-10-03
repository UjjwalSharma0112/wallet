"""R5 — the HTTP surface of a refund.

These tests pin the contract a client depends on: the status code for each outcome, the
error code and shape, and a reply that tells the caller what happened to a refund rather
than asking it to come back and ask again.
"""

from datetime import datetime, timedelta, timezone

from bson import ObjectId

from apps.refunds.models import Refund
from apps.shared.mongo import collection
from apps.transfers.models import Transfer
from apps.wallet import repository

from .base import MongoTestCase

REFUNDS_PATH = "/api/v1/refunds"
HISTORY_PATH = "/api/v1/wallet/transactions"

SENDER_EMAIL = "aarav.mehta@wallet.demo"
PAYEE_EMAIL = "fatima.noor@wallet.demo"
STRANGER_EMAIL = "grace.obi@wallet.demo"

SENDER_OPENING = 245000
PAYEE_OPENING = 12850

ORIGINAL = 50000
NOTE = "Order 2214"


def now():
    return datetime.now(timezone.utc)


class RefundApiTest(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.sender = self.create_account(name="Aarav Mehta", email=SENDER_EMAIL, opening_balance_minor=SENDER_OPENING)
        self.payee = self.create_account(name="Fatima Noor", email=PAYEE_EMAIL, opening_balance_minor=PAYEE_OPENING)
        self.stranger = self.create_account(name="Grace Obi", email=STRANGER_EMAIL, opening_balance_minor=700)

    def signed_in(self, email):
        return self.api_client(self.token_for(email))

    def post(self, body, email=PAYEE_EMAIL, key=None):
        client = self.signed_in(email)

        if key:
            return client.post(REFUNDS_PATH, body, content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)

        return client.post(REFUNDS_PATH, body, content_type="application/json")

    def post_with_key(self, body, key, email=PAYEE_EMAIL):
        return self.signed_in(email).post(REFUNDS_PATH, body, content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)

    def get(self, query="", email=PAYEE_EMAIL):
        return self.signed_in(email).get(f"{REFUNDS_PATH}{query}")

    def error_of(self, response):
        return response.json()["error"]

    def field_errors(self, response):
        return self.error_of(response)["details"]["fieldErrors"]

    def make_original(self, amount_minor=ORIGINAL, email=PAYEE_EMAIL):
        """Give the payee something to refund, through the real transfer route's service."""
        from apps.transfers import services as transfer_services

        payload, _ = transfer_services.execute_transfer(
            self.account_document(self.sender), PAYEE_EMAIL, amount_minor, NOTE, None
        )

        return payload["transfer"]["id"]

    def make_refund(self, body=None, email=PAYEE_EMAIL, key=None):
        if body is None:
            body = {"originalTransferId": self.make_original(), "amountMinor": ORIGINAL}

        return self.post(body, email, key=key)


class CreatingTest(RefundApiTest):
    def test_refunding_returns_201_with_the_refund_the_transfer_and_the_payers_wallet(self):
        original = self.make_original()
        response = self.post({"originalTransferId": original, "amountMinor": 20000, "note": "Partial"})

        self.assertEqual(response.status_code, 201)

        data = response.json()["data"]

        self.assertEqual(data["refund"]["state"], "COMPLETED")
        self.assertEqual(data["refund"]["originalTransferId"], original)
        self.assertEqual(data["refund"]["amountMinor"], 20000)
        self.assertEqual(data["transfer"]["state"], "COMPLETED")
        self.assertEqual(data["transfer"]["amountMinor"], 20000)
        self.assertEqual(data["wallet"]["availableBalanceMinor"], PAYEE_OPENING + ORIGINAL - 20000)
        self.assertFalse(data["idempotentReplay"])

    def test_a_full_refund_is_201_and_drains_the_refundable_total(self):
        original = self.make_original()
        response = self.post({"originalTransferId": original, "amountMinor": ORIGINAL})

        self.assertEqual(response.status_code, 201)

        stored = Transfer.objects(__raw__={"_id": ObjectId(original)}).as_pymongo().first()

        self.assertEqual(stored["refundedAmountMinor"], ORIGINAL)

        over = self.post({"originalTransferId": original, "amountMinor": 1})

        self.assertEqual(over.status_code, 409)
        self.assertEqual(over.json()["error"]["code"], "REFUND_LIMIT_EXCEEDED")

    def test_the_same_body_but_a_new_key_is_a_new_refund(self):
        original = self.make_original()

        first = self.post_with_key({"originalTransferId": original, "amountMinor": 1000}, "refund-key-api-1")
        second = self.post_with_key({"originalTransferId": original, "amountMinor": 1000}, "refund-key-api-2")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json()["data"]["refund"]["id"], second.json()["data"]["refund"]["id"])

    def test_the_same_key_the_same_body_returns_200_with_the_same_money(self):
        original = self.make_original()

        first = self.post_with_key({"originalTransferId": original, "amountMinor": 1000}, "refund-key-api-3")
        second = self.post_with_key({"originalTransferId": original, "amountMinor": 1000}, "refund-key-api-3")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second["Idempotent-Replayed"], "true")
        self.assertEqual(second.json()["data"]["refund"]["id"], first.json()["data"]["refund"]["id"])
        self.assertEqual(second.json()["data"]["wallet"]["availableBalanceMinor"], PAYEE_OPENING + ORIGINAL - 1000)

    def test_the_same_key_with_a_different_body_is_a_409(self):
        original = self.make_original()

        self.post_with_key({"originalTransferId": original, "amountMinor": 1000}, "refund-key-api-4")
        different = self.post_with_key({"originalTransferId": original, "amountMinor": 2000}, "refund-key-api-4")

        self.assertEqual(different.status_code, 409)
        self.assertEqual(different.json()["error"]["code"], "IDEMPOTENCY_KEY_REUSED")

    def test_a_malformed_key_is_a_400(self):
        original = self.make_original()
        response = self.post_with_key({"originalTransferId": original, "amountMinor": 1000}, "short")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "IDEMPOTENCY_KEY_INVALID")

    def test_a_key_in_the_refunds_namespace_is_a_400(self):
        original = self.make_original()
        response = self.post_with_key({"originalTransferId": original, "amountMinor": 1000}, "refund:notmine123")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "IDEMPOTENCY_KEY_RESERVED")

    def test_a_key_in_the_money_requests_namespace_is_still_a_400(self):
        original = self.make_original()
        response = self.post_with_key({"originalTransferId": original, "amountMinor": 1000}, "money-request:notmine123")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "IDEMPOTENCY_KEY_RESERVED")


class RefusalsTest(RefundApiTest):
    def test_an_unknown_transfer_is_a_404(self):
        response = self.post({"originalTransferId": str(ObjectId()), "amountMinor": 1000})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "ORIGINAL_TRANSFER_NOT_FOUND")

    def test_a_malformed_transfer_id_is_a_400(self):
        response = self.post({"originalTransferId": "not-an-id", "amountMinor": 1000})

        self.assertEqual(response.status_code, 400)
        self.assertEqual("originalTransferId" in self.field_errors(response), True)

    def test_only_the_payee_can_refund(self):
        original = self.make_original()
        as_sender = self.post({"originalTransferId": original, "amountMinor": 1000}, email=SENDER_EMAIL)
        as_stranger = self.post({"originalTransferId": original, "amountMinor": 1000}, email=STRANGER_EMAIL)

        for refusal in (as_sender, as_stranger):
            self.assertEqual(refusal.status_code, 403)
            self.assertEqual(refusal.json()["error"]["code"], "REFUND_ACTION_NOT_ALLOWED")

    def test_a_refund_transfer_cannot_be_refunded(self):
        original = self.make_original()
        made = self.post({"originalTransferId": original, "amountMinor": 1000})
        refund_transfer_id = made.json()["data"]["transfer"]["id"]

        response = self.post({"originalTransferId": refund_transfer_id, "amountMinor": 500}, email=SENDER_EMAIL)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "REFUND_OF_REFUND")

    def test_anonymous_callers_get_401(self):
        client = self.api_client("")

        response = client.post(REFUNDS_PATH, {"originalTransferId": str(ObjectId()), "amountMinor": 1000}, content_type="application/json")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")

    def test_non_completed_originals_cannot_be_refunded(self):
        from apps.shared.errors import AppError
        from apps.transfers import services as transfer_services

        # More than the sender holds: the engine records the FAILED transfer rather than
        # moving money, and the failed transfer is what a refund would be asked of.
        try:
            transfer_services.execute_transfer(self.account_document(self.sender), "grace.obi@wallet.demo", SENDER_OPENING + 1, None, None)
        except AppError as error:
            self.assertEqual(error.code, "INSUFFICIENT_FUNDS")

        failed_transfer = Transfer.objects(__raw__={"state": "FAILED"}).as_pymongo().first()

        self.assertIsNotNone(failed_transfer)

        # The engine refused this transfer to grace, recording it FAILED. Only its
        # recipient may ask to refund it, and the answer is that it cannot be refunded.
        response = self.post({"originalTransferId": str(failed_transfer["_id"]), "amountMinor": 1000}, email=STRANGER_EMAIL)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "TRANSFER_NOT_REFUNDABLE")

    def test_over_refunding_is_a_409_and_moves_nothing(self):
        original = self.make_original()
        self.post({"originalTransferId": original, "amountMinor": 30000})
        response = self.post({"originalTransferId": original, "amountMinor": 20001})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "REFUND_LIMIT_EXCEEDED")

        wallet = repository.find_wallet_by_user_id(self.payee.id)

        self.assertEqual(wallet["availableBalanceMinor"], PAYEE_OPENING + ORIGINAL - 30000)

    def test_bad_amounts_fail_validation(self):
        original = self.make_original()

        for bad in (0, -5, True, 10_000_001):
            with self.subTest(bad=bad):
                response = self.post({"originalTransferId": original, "amountMinor": bad})

                self.assertEqual(response.status_code, 400)
                self.assertIn("amountMinor", self.field_errors(response))

        # A quoted amount like "1000" is coerced, not refused, exactly as R2's /transfers
        # has always done; refusing it here would make the two routes disagree.
        accepted = self.post({"originalTransferId": original, "amountMinor": "1000"})

        self.assertEqual(accepted.status_code, 201)

    def test_put_and_delete_are_not_allowed(self):
        client = self.signed_in(PAYEE_EMAIL)

        self.assertEqual(client.put(REFUNDS_PATH, {}, content_type="application/json").status_code, 405)
        self.assertEqual(client.delete(REFUNDS_PATH).status_code, 405)


class ListingTest(RefundApiTest):
    def test_a_caller_sees_only_their_own_refunds_newest_first(self):
        first = self.make_original()
        second = self.make_original(amount_minor=1000)

        self.post({"originalTransferId": first, "amountMinor": 1000}, email=PAYEE_EMAIL)
        self.post({"originalTransferId": second, "amountMinor": 500}, email=PAYEE_EMAIL)

        page = self.get().json()["data"]

        self.assertEqual(page["total"], 2)
        self.assertEqual([r["amountMinor"] for r in page["refunds"]], [500, 1000])
        self.assertTrue(all(r["state"] == "COMPLETED" for r in page["refunds"]))

        sender_page = self.get(email=SENDER_EMAIL).json()["data"]

        self.assertEqual(sender_page["total"], 0)
        self.assertEqual(sender_page["refunds"], [])

    def test_the_page_shape_is_the_one_the_client_renders(self):
        original = self.make_original()
        self.post({"originalTransferId": original, "amountMinor": 1000, "note": NOTE})

        page = self.get().json()["data"]
        refund = page["refunds"][0]

        self.assertEqual(page["limit"], 25)
        self.assertEqual(page["offset"], 0)
        self.assertEqual(refund["amountMinor"], 1000)
        self.assertEqual(refund["note"], NOTE)
        self.assertEqual(refund["counterparty"]["name"], "Aarav Mehta")
        self.assertEqual(refund["state"], "COMPLETED")

    def test_pagination_limits_and_offsets(self):
        original = self.make_original()

        for amount in (100, 200, 300):
            self.post({"originalTransferId": original, "amountMinor": amount})

        page = self.get("?limit=2&offset=2").json()["data"]

        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["refunds"]), 1)

    def test_a_failed_refund_is_listed_as_failed(self):
        original = self.make_original()
        self.post({"originalTransferId": original, "amountMinor": ORIGINAL + 1})

        page = self.get().json()["data"]

        self.assertEqual(page["total"], 1)
        self.assertEqual(page["refunds"][0]["state"], "FAILED")
        self.assertEqual(page["refunds"][0]["failureCode"], "REFUND_LIMIT_EXCEEDED")


class HistoryDecorationTest(RefundApiTest):
    def test_the_payees_history_shows_what_can_still_be_given_back(self):
        original = self.make_original()
        self.post({"originalTransferId": original, "amountMinor": 20000})

        entries = self.signed_in(PAYEE_EMAIL).get(HISTORY_PATH).json()["data"]["entries"]
        original_entry = [entry for entry in entries if entry["transactionId"] == original][0]
        refund_entry = [entry for entry in entries if entry["kind"] in ("REFUND_OUT", "REFUND_IN")]

        self.assertEqual(original_entry["refundedAmountMinor"], 20000)
        self.assertEqual(original_entry["remainingRefundableMinor"], ORIGINAL - 20000)
        self.assertEqual(len(refund_entry), 1)
        self.assertEqual(refund_entry[0]["kind"], "REFUND_OUT")
        self.assertEqual(refund_entry[0]["amountMinor"], 20000)

    def test_the_senders_history_shows_the_incoming_refund_and_nothing_left_to_refund(self):
        original = self.make_original()
        made = self.post({"originalTransferId": original, "amountMinor": 20000})
        refund_transfer = made.json()["data"]["transfer"]["id"]

        entries = self.signed_in(SENDER_EMAIL).get(HISTORY_PATH).json()["data"]["entries"]
        refund_leg = [entry for entry in entries if entry["kind"] == "REFUND_IN"][0]
        original_leg = [entry for entry in entries if entry["transactionId"] == original][0]

        self.assertEqual(refund_leg["refundOf"], original)
        self.assertEqual(original_leg["remainingRefundableMinor"], None)
