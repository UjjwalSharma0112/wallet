"""R4 — the HTTP surface of a money request.

These tests pin the contract a client depends on: the status code for each outcome, the
error code and shape, and the fact that approving answers with the payment it made, so the
interface never has to ask twice to find out what happened to a request.
"""

from datetime import datetime, timedelta, timezone

from bson import ObjectId

from apps.money_requests.models import MoneyRequest
from apps.shared.mongo import collection
from apps.transfers.models import Transfer
from apps.wallet import repository

from .base import MongoTestCase

REQUESTS_PATH = "/api/v1/money-requests"
HISTORY_PATH = "/api/v1/wallet/transactions"

APPROVER_EMAIL = "aarav.mehta@wallet.demo"
REQUESTER_EMAIL = "fatima.noor@wallet.demo"
STRANGER_EMAIL = "grace.obi@wallet.demo"

APPROVER_OPENING = 245000
REQUESTER_OPENING = 12850

AMOUNT = 12000
NOTE = "Rent share for April"


def now():
    return datetime.now(timezone.utc)


class MoneyRequestApiTest(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.approver = self.create_account(
            name="Aarav Mehta", email=APPROVER_EMAIL, opening_balance_minor=APPROVER_OPENING
        )
        self.requester = self.create_account(
            name="Fatima Noor", email=REQUESTER_EMAIL, opening_balance_minor=REQUESTER_OPENING
        )
        self.stranger = self.create_account(name="Grace Obi", email=STRANGER_EMAIL, opening_balance_minor=700)

    def signed_in(self, email):
        return self.api_client(self.token_for(email))

    def post(self, path="", body=None, email=REQUESTER_EMAIL, token=None):
        client = self.api_client(self.token_for(email) if token is None else token)

        return client.post(f"{REQUESTS_PATH}{path}", body if body is not None else {}, content_type="application/json")

    def get(self, path="", email=REQUESTER_EMAIL, token=None):
        client = self.api_client(self.token_for(email) if token is None else token)

        return client.get(f"{REQUESTS_PATH}{path}")

    def ask(self, body=None, email=REQUESTER_EMAIL, token=None):
        return self.post(
            "",
            body if body is not None else {"recipientEmail": APPROVER_EMAIL, "amountMinor": AMOUNT},
            email,
            token,
        )

    def approve(self, request_id, email=APPROVER_EMAIL, token=None):
        return self.post(f"/{request_id}/approval", None, email, token)

    def decline(self, request_id, email=APPROVER_EMAIL, token=None):
        return self.post(f"/{request_id}/decline", None, email, token)

    def error_of(self, response):
        return response.json()["error"]

    def field_errors(self, response):
        return self.error_of(response)["details"]["fieldErrors"]


class CreatingTest(MoneyRequestApiTest):
    def test_asking_for_money_returns_201_and_the_request_as_it_was_asked(self):
        response = self.ask({"recipientEmail": APPROVER_EMAIL, "amountMinor": AMOUNT, "note": NOTE})

        self.assertEqual(response.status_code, 201)

        request = response.json()["data"]

        self.assertEqual(request["state"], "PENDING")
        self.assertEqual(request["role"], "REQUESTER")
        self.assertEqual(request["amountMinor"], AMOUNT)
        self.assertEqual(request["note"], NOTE)
        self.assertEqual(request["requesterUserId"], str(self.requester.id))
        self.assertEqual(request["approverUserId"], str(self.approver.id))
        self.assertEqual(request["counterparty"]["name"], "Aarav Mehta")
        self.assertIsNotNone(request["expiresAt"])

    def test_the_body_is_the_same_one_a_transfer_takes(self):
        # A request and a payment are the same two things said at different times, so a client
        # that can fill in a transfer form can fill in a request form.
        response = self.ask({"recipientEmail": APPROVER_EMAIL, "amountMinor": AMOUNT})

        self.assertEqual(response.status_code, 201)

        missing_recipient = self.ask({"amountMinor": AMOUNT})
        missing_amount = self.ask({"recipientEmail": APPROVER_EMAIL})

        for refusal in (missing_recipient, missing_amount):
            self.assertEqual(refusal.status_code, 400)
            self.assertEqual(self.error_of(refusal)["code"], "VALIDATION_ERROR")

        self.assertIn("recipientEmail", self.field_errors(missing_recipient))
        self.assertIn("amountMinor", self.field_errors(missing_amount))

    def test_a_note_is_optional(self):
        response = self.ask({"recipientEmail": APPROVER_EMAIL, "amountMinor": AMOUNT})

        self.assertIsNone(response.json()["data"]["note"])

    def test_asking_requires_authentication(self):
        response = self.ask(token="")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.error_of(response)["code"], "AUTH_REQUIRED")

    def test_a_rejected_token_is_refused(self):
        response = self.ask(token="not-a-token")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.error_of(response)["code"], "INVALID_TOKEN")

    def test_asking_for_your_own_money_is_refused_with_the_field_that_is_wrong(self):
        response = self.ask({"recipientEmail": REQUESTER_EMAIL, "amountMinor": AMOUNT}, email=REQUESTER_EMAIL)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["code"], "VALIDATION_ERROR")
        self.assertEqual(MoneyRequest.objects.count(), 0)

    def test_asking_for_an_amount_that_is_not_money_is_refused(self):
        for amount in (0, -100, "twelve hundred", 1.5, None):
            with self.subTest(amount=amount):
                self.assertEqual(self.ask({"recipientEmail": APPROVER_EMAIL, "amountMinor": amount}).status_code, 400)

        self.assertEqual(MoneyRequest.objects.count(), 0)

    def test_asking_for_somebody_unknown_is_refused(self):
        response = self.ask({"recipientEmail": "nobody@wallet.demo", "amountMinor": AMOUNT})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.error_of(response)["code"], "RECIPIENT_NOT_FOUND")
        self.assertEqual(MoneyRequest.objects.count(), 0)

    def test_the_collection_only_answers_get_and_post(self):
        refused = self.signed_in(REQUESTER_EMAIL).put(REQUESTS_PATH, {}, content_type="application/json")

        self.assertEqual(refused.status_code, 405)
        self.assertEqual(self.signed_in(REQUESTER_EMAIL).delete(REQUESTS_PATH).status_code, 405)


class ListingTest(MoneyRequestApiTest):
    def setUp(self):
        super().setUp()

        self.mine = self.ask({"recipientEmail": APPROVER_EMAIL, "amountMinor": AMOUNT, "note": NOTE}).json()["data"]
        self.theirs = self.ask(
            {"recipientEmail": APPROVER_EMAIL, "amountMinor": 4000}, email=STRANGER_EMAIL
        ).json()["data"]

    def page(self, query="", email=APPROVER_EMAIL):
        return self.get(query, email=email)

    def test_listing_returns_the_requests_the_caller_is_a_party_to(self):
        response = self.page()

        self.assertEqual(response.status_code, 200)

        page = response.json()["data"]

        self.assertEqual(page["total"], 2)
        self.assertEqual({request["id"] for request in page["requests"]}, {self.mine["id"], self.theirs["id"]})
        self.assertEqual(page["limit"], 25)
        self.assertEqual(page["offset"], 0)

    def test_a_side_can_be_asked_for(self):
        asking = self.page("?role=REQUESTER", email=REQUESTER_EMAIL)
        being_asked = self.page("?role=APPROVER", email=APPROVER_EMAIL)

        # Both requests were addressed to the approver, and only one of them was asked for by
        # the requester, so the two filters have to disagree or the view is not scoped.
        self.assertEqual(asking.json()["data"]["total"], 1)
        self.assertEqual(asking.json()["data"]["requests"][0]["id"], self.mine["id"])
        self.assertEqual(asking.json()["data"]["requests"][0]["role"], "REQUESTER")
        self.assertEqual(being_asked.json()["data"]["total"], 2)
        self.assertEqual(
            {request["id"] for request in being_asked.json()["data"]["requests"]},
            {self.mine["id"], self.theirs["id"]},
        )
        self.assertTrue(all(request["role"] == "APPROVER" for request in being_asked.json()["data"]["requests"]))

    def test_a_state_can_be_asked_for(self):
        self.assertEqual(self.page("?state=PENDING").json()["data"]["total"], 2)
        self.assertEqual(self.page("?state=APPROVED").json()["data"]["total"], 0)

    def test_a_filter_that_is_not_one_of_the_choices_is_refused_rather_than_ignored(self):
        for query in ("?role=ADMIN", "?state=PENDINGISH"):
            with self.subTest(query=query):
                response = self.page(query)

                self.assertEqual(response.status_code, 400)
                self.assertIn(query[1:].split("=")[0], self.field_errors(response))

    def test_a_page_beyond_the_end_is_an_empty_page_rather_than_an_error(self):
        page = self.page("?limit=1&offset=50").json()["data"]

        self.assertEqual(page["requests"], [])
        self.assertEqual(page["total"], 2)

    def test_listing_requires_authentication(self):
        self.assertEqual(self.get(token="").status_code, 401)

    def test_the_list_is_scoped_to_the_signed_in_account(self):
        # The same call made by each side returns that side's own view of the same request,
        # which is the only way a list can be scoped without a caller naming an account.
        mine = self.page("?role=REQUESTER", email=REQUESTER_EMAIL).json()["data"]["requests"][0]
        theirs = self.page("?role=APPROVER", email=APPROVER_EMAIL).json()["data"]["requests"][0]

        self.assertEqual(mine["id"], self.mine["id"])
        self.assertEqual(theirs["id"], self.theirs["id"])
        self.assertEqual(mine["counterparty"]["name"], "Aarav Mehta")
        self.assertEqual(theirs["counterparty"]["name"], "Grace Obi")

    def test_a_request_hands_out_no_key_and_no_fingerprint(self):
        request = self.page().json()["data"]["requests"][0]

        self.assertNotIn("idempotencyKey", request)
        self.assertNotIn("fingerprint", request)


class ApprovingOverHttpTest(MoneyRequestApiTest):
    def test_approving_returns_200_with_the_payment_it_made_and_the_new_balance(self):
        request = self.ask({"recipientEmail": APPROVER_EMAIL, "amountMinor": AMOUNT, "note": NOTE}).json()["data"]

        response = self.approve(request["id"])

        self.assertEqual(response.status_code, 200)

        data = response.json()["data"]

        self.assertEqual(data["request"]["state"], "APPROVED")
        self.assertEqual(data["request"]["transferId"], data["transfer"]["id"])
        self.assertEqual(data["transfer"]["state"], "COMPLETED")
        self.assertEqual(data["transfer"]["role"], "SENDER")
        self.assertEqual(data["transfer"]["senderUserId"], str(self.approver.id))
        self.assertEqual(data["transfer"]["recipientUserId"], str(self.requester.id))
        self.assertEqual(data["transfer"]["amountMinor"], AMOUNT)
        self.assertEqual(data["wallet"]["availableBalanceMinor"], APPROVER_OPENING - AMOUNT)
        self.assertEqual(data["wallet"]["userId"], str(self.approver.id))

    def test_approving_twice_answers_the_second_time_as_a_replay(self):
        request = self.ask().json()["data"]

        first = self.approve(request["id"])
        second = self.approve(request["id"])

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.json()["data"]["idempotentReplay"])
        self.assertTrue(second.json()["data"]["idempotentReplay"])
        self.assertEqual(first.json()["data"]["transfer"]["id"], second.json()["data"]["transfer"]["id"])
        self.assertEqual(Transfer.objects.count(), 1)
        held = repository.find_wallet_by_user_id(self.approver.id)["availableBalanceMinor"]

        self.assertEqual(held, APPROVER_OPENING - AMOUNT)

    def test_the_payment_appears_in_the_ledger_of_both_accounts(self):
        asked = self.ask().json()["data"]
        paid = self.approve(asked["id"]).json()["data"]

        for email in (APPROVER_EMAIL, REQUESTER_EMAIL):
            with self.subTest(email=email):
                entries = self.api_client(self.token_for(email)).get(HISTORY_PATH).json()["data"]["entries"]
                payments = [entry for entry in entries if entry["transactionId"] == paid["transfer"]["id"]]

                self.assertEqual(len(payments), 1)
                self.assertEqual(payments[0]["amountMinor"], AMOUNT)
                self.assertEqual(payments[0]["direction"], "DEBIT" if email == APPROVER_EMAIL else "CREDIT")

    def test_the_requester_cannot_approve_their_own_request(self):
        request = self.ask().json()["data"]

        response = self.approve(request["id"], email=REQUESTER_EMAIL)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.error_of(response)["code"], "REQUEST_ACTION_NOT_ALLOWED")
        self.assertEqual(Transfer.objects.count(), 0)

    def test_somebody_who_was_never_asked_cannot_approve_it(self):
        request = self.ask().json()["data"]

        response = self.approve(request["id"], email=STRANGER_EMAIL)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.error_of(response)["code"], "REQUEST_ACTION_NOT_ALLOWED")

    def test_approving_requires_authentication(self):
        request = self.ask().json()["data"]

        self.assertEqual(self.approve(request["id"], token="").status_code, 401)

    def test_a_request_that_does_not_exist_is_a_404(self):
        response = self.approve("000000000000000000000000")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.error_of(response)["code"], "REQUEST_NOT_FOUND")

    def test_an_id_that_is_not_an_id_is_the_same_404(self):
        response = self.approve("not-a-request")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.error_of(response)["code"], "REQUEST_NOT_FOUND")

    def test_the_approval_routes_only_accept_posts(self):
        request = self.ask().json()["data"]

        self.assertEqual(self.signed_in(APPROVER_EMAIL).get(f"{REQUESTS_PATH}/{request['id']}/approval").status_code, 405)
        self.assertEqual(self.signed_in(APPROVER_EMAIL).get(f"{REQUESTS_PATH}/{request['id']}/decline").status_code, 405)

    def test_a_request_that_was_already_approved_answers_409(self):
        request = self.ask().json()["data"]
        self.approve(request["id"])

        response = self.approve(request["id"])

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["idempotentReplay"])

    def test_a_request_that_was_declined_answers_409(self):
        request = self.ask().json()["data"]
        self.decline(request["id"])

        response = self.approve(request["id"])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_of(response)["code"], "REQUEST_ALREADY_DECLINED")

    def test_a_request_that_has_expired_answers_409_and_moves_no_money(self):
        request = self.ask().json()["data"]
        collection(MoneyRequest).update_one(
            {"_id": ObjectId(request["id"])}, {"$set": {"expiresAt": now() - timedelta(days=1)}}
        )

        response = self.approve(request["id"])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_of(response)["code"], "REQUEST_EXPIRED")
        self.assertEqual(Transfer.objects.count(), 0)

    def test_a_request_that_is_being_approved_answers_409(self):
        request = self.ask().json()["data"]
        collection(MoneyRequest).update_one(
            {"_id": ObjectId(request["id"])}, {"$set": {"state": "APPROVING", "approvalStartedAt": now()}}
        )

        response = self.approve(request["id"])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_of(response)["code"], "REQUEST_APPROVAL_IN_PROGRESS")
        self.assertEqual(Transfer.objects.count(), 0)


class DecliningOverHttpTest(MoneyRequestApiTest):
    def test_declining_returns_200_with_the_request_and_moves_no_money(self):
        request = self.ask().json()["data"]

        response = self.decline(request["id"])

        self.assertEqual(response.status_code, 200)

        declined = response.json()["data"]

        self.assertEqual(declined["state"], "DECLINED")
        self.assertIsNone(declined["transferId"])
        self.assertIsNotNone(declined["resolvedAt"])
        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(repository.find_wallet_by_user_id(self.approver.id)["availableBalanceMinor"], APPROVER_OPENING)

    def test_declining_twice_answers_409(self):
        request = self.ask().json()["data"]
        self.decline(request["id"])

        response = self.decline(request["id"])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_of(response)["code"], "REQUEST_ALREADY_DECLINED")

    def test_an_approved_request_cannot_be_declined(self):
        request = self.ask().json()["data"]
        self.approve(request["id"])

        response = self.decline(request["id"])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_of(response)["code"], "REQUEST_ALREADY_APPROVED")

    def test_the_requester_cannot_decline_their_own_request(self):
        request = self.ask().json()["data"]

        response = self.decline(request["id"], email=REQUESTER_EMAIL)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.error_of(response)["code"], "REQUEST_ACTION_NOT_ALLOWED")

    def test_declining_a_request_that_does_not_exist_is_a_404(self):
        response = self.decline("not-a-request")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.error_of(response)["code"], "REQUEST_NOT_FOUND")


class ReservedKeyTest(MoneyRequestApiTest):
    """The prefix an approval spends is reserved, so a client can never hold it.

    Without this, a client could spend `money-request:<id>` before the approver did and the
    approval would meet its own key — and, since the index is per sender, only the approver's
    own account could do it. Reserving the prefix turns that into a refusal at the door
    instead of a mystery later.
    """

    def test_a_key_beginning_with_the_reserved_prefix_is_refused(self):
        response = self.signed_in(APPROVER_EMAIL).post(
            "/api/v1/transfers",
            {"recipientEmail": REQUESTER_EMAIL, "amountMinor": 1000},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="money-request:000000000000000000000000",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["code"], "IDEMPOTENCY_KEY_RESERVED")

    def test_the_refusal_says_which_part_of_the_key_is_the_problem(self):
        response = self.signed_in(APPROVER_EMAIL).post(
            "/api/v1/transfers",
            {"recipientEmail": REQUESTER_EMAIL, "amountMinor": 1000},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="money-request:abc",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("money-request:", self.error_of(response)["message"])
        self.assertEqual(Transfer.objects.count(), 0)

    def test_a_key_that_merely_contains_the_prefix_is_still_a_ordinary_key(self):
        # Only the beginning of the key is reserved, so a key that happens to contain the
        # words is left alone rather than refused for something it did not do.
        response = self.signed_in(APPROVER_EMAIL).post(
            "/api/v1/transfers",
            {"recipientEmail": REQUESTER_EMAIL, "amountMinor": 1000},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="payment:money-request:abc",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Transfer.objects.count(), 1)
