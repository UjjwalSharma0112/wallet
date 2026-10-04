"""R7 — the HTTP surface of a batch.

These tests pin the contract a client depends on: the status code for each outcome, the error
code and shape, where a validation problem is reported, and the fact that a batch belonging to
somebody else is refused rather than described. They also check that creating a batch pays
nothing, because that is what makes the two-step flow safe to offer: a list a person is still
typing is not a list of payments.
"""

from unittest import mock

from bson import ObjectId
from mongoengine.errors import NotUniqueError

from apps.batch import services
from apps.batch.constants import MAX_BATCH_ITEMS
from apps.transfers import repository as transfer_repository
from apps.transfers.constants import (
    IDEMPOTENCY_KEY_CONTENDED_CODE,
    IDEMPOTENCY_KEY_CONTENDED_MESSAGE,
    IDEMPOTENCY_KEY_RESERVED_CODE,
    IDEMPOTENCY_KEY_RESERVED_MESSAGE,
)
from apps.transfers.models import Transfer

from .base import MongoTestCase

BATCHES_PATH = "/api/v1/batches"

OWNER_EMAIL = "aarav.mehta@wallet.demo"
STRANGER_EMAIL = "grace.obi@wallet.demo"
FRIEND_EMAIL = "rahul.singh@wallet.demo"
SECOND_EMAIL = "priya.nair@wallet.demo"

OWNER_OPENING = 1_000_000


class BatchApiTest(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.owner = self.create_account(name="Aarav Mehta", email=OWNER_EMAIL, opening_balance_minor=OWNER_OPENING)
        self.stranger = self.create_account(name="Grace Obi", email=STRANGER_EMAIL, opening_balance_minor=700)
        self.friend = self.create_account(name="Rahul Singh", email=FRIEND_EMAIL, opening_balance_minor=0)
        self.second = self.create_account(name="Priya Nair", email=SECOND_EMAIL, opening_balance_minor=0)

    def signed_in(self, email=OWNER_EMAIL):
        return self.api_client(self.token_for(email))

    def create(self, items, email=OWNER_EMAIL):
        return self.signed_in(email).post(BATCHES_PATH, {"items": items}, content_type="application/json")

    def list(self, query="", email=OWNER_EMAIL):
        return self.signed_in(email).get(f"{BATCHES_PATH}{query}")

    def process(self, batch_id, email=OWNER_EMAIL):
        return self.signed_in(email).post(f"{BATCHES_PATH}/{batch_id}/processing", {}, content_type="application/json")

    def contend_claims(self, only_key=None):
        """Refuse attempts to claim a key, releasing it again so the key stays free.

        This is a batch item's key being raced past the engine's attempt budget by requests that
        keep claiming it and then failing it. Nothing holds the key and nothing can be sent, which
        is the one outcome a batch run has to turn into a recorded result rather than an answer
        about the whole batch — it is a refusal of this payment, not of the batch.

        `only_key` narrows the contention to one item's key, so a run can be raced on one line
        and left alone on the next. The refusals are real: each one really inserts a transfer and
        really fails it, so the key is genuinely free again afterwards rather than held by a mock.
        """
        real_create = transfer_repository.create_transfer
        calls = []

        def lose_the_claim(values):
            if only_key is not None and values.get("idempotency_key") != only_key:
                return real_create(values)

            calls.append(values)
            claimed = real_create(values)
            transfer_repository.mark_failed(claimed["_id"], "RIVAL_FAILED")

            raise NotUniqueError("lost the claim to a request that then failed")

        return mock.patch.object(transfer_repository, "create_transfer", lose_the_claim), calls

    def two_items(self, amount=50000):
        return [
            {"recipientEmail": FRIEND_EMAIL, "amountMinor": amount, "note": "rent"},
            {"recipientEmail": SECOND_EMAIL, "amountMinor": amount},
        ]


class AuthenticationTest(BatchApiTest):
    def test_creating_a_batch_requires_a_signed_in_account(self):
        response = self.api_client().post(BATCHES_PATH, {"items": self.two_items()}, content_type="application/json")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_reading_batches_requires_a_signed_in_account(self):
        self.assertEqual(self.api_client().get(BATCHES_PATH).status_code, 401)

    def test_processing_a_batch_requires_a_signed_in_account(self):
        batch = self.create(self.two_items()).json()["data"]

        response = self.api_client().post(
            f"{BATCHES_PATH}/{batch['id']}/processing", {}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_a_token_that_is_not_a_token_is_refused(self):
        response = self.api_client("not-a-jwt").get(BATCHES_PATH)

        self.assertEqual(response.status_code, 401)


class CreateTest(BatchApiTest):
    def test_a_batch_is_created_pending_and_pays_nothing(self):
        response = self.create(self.two_items())

        self.assertEqual(response.status_code, 201)

        batch = response.json()["data"]

        self.assertEqual(batch["state"], "PENDING")
        self.assertEqual(batch["summary"]["totalItems"], 2)
        self.assertEqual(batch["summary"]["pendingItems"], 2)
        self.assertEqual(batch["summary"]["completedAmountMinor"], 0)
        self.assertIsNone(batch["completedAt"])

        for item in batch["items"]:
            self.assertEqual(item["state"], "PENDING")
            self.assertIsNone(item["transferId"])
            self.assertIsNone(item["failureCode"])

    def test_the_response_echoes_every_item_in_the_order_it_was_given(self):
        batch = self.create(self.two_items()).json()["data"]

        self.assertEqual([item["position"] for item in batch["items"]], [0, 1])
        self.assertEqual([item["recipientEmail"] for item in batch["items"]], [FRIEND_EMAIL, SECOND_EMAIL])
        self.assertEqual([item["amountMinor"] for item in batch["items"]], [50000, 50000])
        self.assertEqual(batch["items"][0]["note"], "rent")
        self.assertIsNone(batch["items"][1]["note"])

    def test_a_batch_of_one_is_accepted(self):
        response = self.create([{"recipientEmail": FRIEND_EMAIL, "amountMinor": 500}])

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["data"]["summary"]["totalItems"], 1)

    def test_a_batch_of_exactly_the_maximum_size_is_accepted(self):
        items = [{"recipientEmail": FRIEND_EMAIL, "amountMinor": 100} for _ in range(MAX_BATCH_ITEMS)]

        self.assertEqual(self.create(items).status_code, 201)


class ValidationTest(BatchApiTest):
    def assert_field_error(self, response, field):
        self.assertEqual(response.status_code, 400)

        error = response.json()["error"]

        self.assertIn(field, error["details"]["fieldErrors"])

    def test_a_batch_with_no_items_is_refused(self):
        response = self.create(None)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "BATCH_EMPTY")

    def test_an_empty_batch_is_refused(self):
        self.assertEqual(self.create([]).json()["error"]["code"], "BATCH_EMPTY")

    def test_items_that_is_not_a_list_is_refused(self):
        self.assert_field_error(self.create({"recipientEmail": FRIEND_EMAIL, "amountMinor": 500}), "items")

    def test_a_batch_longer_than_the_bound_is_refused(self):
        items = [{"recipientEmail": FRIEND_EMAIL, "amountMinor": 100} for _ in range(MAX_BATCH_ITEMS + 1)]

        self.assert_field_error(self.create(items), "items")

    def test_an_item_that_is_not_an_object_is_refused(self):
        self.assert_field_error(self.create(["rahul@wallet.demo"]), "items.0.recipientEmail")

    def test_a_missing_recipient_is_reported_against_its_own_item(self):
        self.assert_field_error(self.create([{"amountMinor": 500}]), "items.0.recipientEmail")

    def test_a_recipient_that_is_not_an_address_is_reported_against_its_own_item(self):
        self.assert_field_error(
            self.create([{"recipientEmail": "not-an-address", "amountMinor": 500}]), "items.0.recipientEmail"
        )

    def test_an_amount_that_is_not_a_number_is_reported_against_its_own_item(self):
        self.assert_field_error(
            self.create([{"recipientEmail": FRIEND_EMAIL, "amountMinor": "lots"}]), "items.0.amountMinor"
        )

    def test_a_boolean_is_not_an_amount(self):
        self.assert_field_error(
            self.create([{"recipientEmail": FRIEND_EMAIL, "amountMinor": True}]), "items.0.amountMinor"
        )

    def test_an_amount_of_no_money_is_refused(self):
        self.assert_field_error(self.create([{"recipientEmail": FRIEND_EMAIL, "amountMinor": 0}]), "items.0.amountMinor")

    def test_a_negative_amount_is_refused(self):
        self.assert_field_error(
            self.create([{"recipientEmail": FRIEND_EMAIL, "amountMinor": -500}]), "items.0.amountMinor"
        )

    def test_an_amount_with_paise_is_refused(self):
        self.assert_field_error(
            self.create([{"recipientEmail": FRIEND_EMAIL, "amountMinor": 500.5}]), "items.0.amountMinor"
        )

    def test_a_note_that_is_too_long_is_reported_against_its_own_item(self):
        self.assert_field_error(
            self.create([{"recipientEmail": FRIEND_EMAIL, "amountMinor": 500, "note": "x" * 141}]), "items.0.note"
        )

    def test_every_item_is_reported_at_once(self):
        response = self.create(
            [
                {"recipientEmail": "not-an-address", "amountMinor": 500},
                {"recipientEmail": SECOND_EMAIL, "amountMinor": -1},
            ]
        )

        self.assertEqual(response.status_code, 400)

        errors = response.json()["error"]["details"]["fieldErrors"]

        self.assertIn("items.0.recipientEmail", errors)
        self.assertIn("items.1.amountMinor", errors)

    def test_two_items_with_the_same_fault_are_reported_separately(self):
        response = self.create(
            [
                {"recipientEmail": "not-an-address", "amountMinor": 500},
                {"recipientEmail": "also-not-an-address", "amountMinor": 500},
            ]
        )

        errors = response.json()["error"]["details"]["fieldErrors"]

        # The message is the shared reader's for both, but each item's is filed against that
        # item. Collected under one key, the second item's problem would be reported against
        # the first and a caller fixing a long batch would be sent to the wrong row.
        self.assertIn("items.0.recipientEmail", errors)
        self.assertIn("items.1.recipientEmail", errors)
        self.assertNotIn("recipientEmail", errors)

    def test_a_refused_batch_is_not_stored(self):
        self.create([{"recipientEmail": "not-an-address", "amountMinor": 500}])

        self.assertEqual(self.list().json()["data"]["total"], 0)

    def test_the_batch_prefix_is_reserved_and_a_client_cannot_spend_it(self):
        response = self.signed_in().post(
            "/api/v1/transfers",
            {"recipientEmail": FRIEND_EMAIL, "amountMinor": 500},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="batch:0123456789abcdef01234567:0",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], IDEMPOTENCY_KEY_RESERVED_CODE)
        self.assertEqual(response.json()["error"]["message"], IDEMPOTENCY_KEY_RESERVED_MESSAGE)
        self.assertIn("'batch:'", response.json()["error"]["message"])


class ProcessTest(BatchApiTest):
    def test_processing_a_batch_pays_every_item_and_answers_with_what_happened(self):
        batch_id = self.create(self.two_items()).json()["data"]["id"]

        response = self.process(batch_id)

        self.assertEqual(response.status_code, 200)

        data = response.json()["data"]

        self.assertEqual(data["batch"]["state"], "COMPLETED")
        self.assertEqual(data["batch"]["summary"]["completedItems"], 2)
        self.assertEqual(data["batch"]["summary"]["completedAmountMinor"], 100000)
        self.assertEqual(data["batch"]["summary"]["failedItems"], 0)
        self.assertIsNotNone(data["batch"]["completedAt"])

        for item in data["batch"]["items"]:
            self.assertEqual(item["state"], "COMPLETED")
            self.assertIsNotNone(item["transferId"])

        # The owner's own wallet is answered with the batch, so a client that moved money does
        # not have to make a second request to see what it did.
        self.assertEqual(data["wallet"]["availableBalanceMinor"], OWNER_OPENING - 100000)

    def test_processing_reports_an_item_that_could_not_be_paid_and_keeps_going(self):
        batch_id = self.create(
            [
                {"recipientEmail": FRIEND_EMAIL, "amountMinor": 50000},
                {"recipientEmail": SECOND_EMAIL, "amountMinor": 9_000_000},
                {"recipientEmail": FRIEND_EMAIL, "amountMinor": 25000},
            ]
        ).json()["data"]["id"]

        data = self.process(batch_id).json()["data"]

        self.assertEqual(data["batch"]["state"], "PARTIALLY_COMPLETED")
        self.assertEqual([item["state"] for item in data["batch"]["items"]], ["COMPLETED", "FAILED", "COMPLETED"])

        failed = data["batch"]["items"][1]

        self.assertEqual(failed["failureCode"], "INSUFFICIENT_FUNDS")
        self.assertIsNotNone(failed["failureMessage"])
        self.assertIsNone(failed["transferId"])

        self.assertEqual(data["batch"]["summary"]["completedItems"], 2)
        self.assertEqual(data["batch"]["summary"]["failedItems"], 1)
        self.assertEqual(data["batch"]["summary"]["failedAmountMinor"], 9_000_000)

        # The item after the failed one was still paid.
        self.assertEqual(data["wallet"]["availableBalanceMinor"], OWNER_OPENING - 75000)

    def test_running_a_batch_again_moves_no_more_money_and_answers_the_same_way(self):
        batch_id = self.create(self.two_items()).json()["data"]["id"]

        first = self.process(batch_id).json()["data"]
        second = self.process(batch_id).json()["data"]

        self.assertEqual(second["batch"]["summary"], first["batch"]["summary"])
        self.assertEqual(second["wallet"]["availableBalanceMinor"], first["wallet"]["availableBalanceMinor"])
        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 2)

    def test_an_item_whose_key_cannot_be_claimed_is_a_refusal_rather_than_a_server_error(self):
        # The engine can run out of attempts to race for an item's key. That has to arrive here
        # as a recorded result for that item, and the run has to carry on — it is a refusal of
        # one payment, not of the batch, and answering 500 would claim the batch was broken.
        batch_id = self.create(
            [
                {"recipientEmail": FRIEND_EMAIL, "amountMinor": 50000},
                {"recipientEmail": SECOND_EMAIL, "amountMinor": 25000},
            ]
        ).json()["data"]["id"]

        patcher, _ = self.contend_claims()

        with patcher:
            response = self.process(batch_id)

        self.assertEqual(response.status_code, 200)

        data = response.json()["data"]

        self.assertEqual(data["batch"]["state"], "FAILED")
        self.assertEqual([item["state"] for item in data["batch"]["items"]], ["FAILED", "FAILED"])
        self.assertEqual(data["batch"]["summary"]["completedItems"], 0)
        self.assertEqual(data["batch"]["summary"]["failedItems"], 2)

        for item in data["batch"]["items"]:
            self.assertEqual(item["failureCode"], IDEMPOTENCY_KEY_CONTENDED_CODE)
            self.assertEqual(item["failureMessage"], IDEMPOTENCY_KEY_CONTENDED_MESSAGE)
            self.assertIsNone(item["transferId"])

        self.assertEqual(data["wallet"]["availableBalanceMinor"], OWNER_OPENING)
        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 0)

    def test_an_item_that_races_once_is_paid_and_the_run_is_not_disturbed(self):
        # Only one item's key is contested. The other must still be paid, because a refusal on
        # one line is an outcome for that line rather than a reason to abandon the batch.
        batch_id = self.create(
            [
                {"recipientEmail": FRIEND_EMAIL, "amountMinor": 50000},
                {"recipientEmail": SECOND_EMAIL, "amountMinor": 25000},
            ]
        ).json()["data"]["id"]

        patcher, calls = self.contend_claims(only_key=services.batch_key(ObjectId(batch_id), 0))

        with patcher:
            data = self.process(batch_id).json()["data"]

        self.assertTrue(calls, "the first item's key was never actually raced")

        self.assertEqual(data["batch"]["state"], "PARTIALLY_COMPLETED")
        self.assertEqual([item["state"] for item in data["batch"]["items"]], ["FAILED", "COMPLETED"])
        self.assertEqual(data["batch"]["summary"]["completedAmountMinor"], 25000)
        self.assertEqual(data["wallet"]["availableBalanceMinor"], OWNER_OPENING - 25000)
        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 1)

    def test_a_refused_item_is_paid_when_the_batch_is_processed_again(self):
        # The refusal says the key was free, so the retry has to be able to spend it. If it could
        # not, the item would be stuck failed rather than retryable.
        batch_id = self.create(
            [{"recipientEmail": FRIEND_EMAIL, "amountMinor": 50000}]
        ).json()["data"]["id"]

        patcher, _ = self.contend_claims()

        with patcher:
            refused = self.process(batch_id).json()["data"]

        self.assertEqual(refused["batch"]["state"], "FAILED")

        retried = self.process(batch_id).json()["data"]

        self.assertEqual(retried["batch"]["state"], "COMPLETED")
        self.assertEqual(retried["batch"]["items"][0]["failureCode"], None)
        self.assertEqual(retried["wallet"]["availableBalanceMinor"], OWNER_OPENING - 50000)
        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 1)

    def test_a_batch_that_does_not_exist_is_not_found(self):
        response = self.process(ObjectId())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "BATCH_NOT_FOUND")

    def test_a_path_segment_that_is_not_an_id_is_not_found_rather_than_a_crash(self):
        for path in ("not-an-id", "12345", "batches"):
            with self.subTest(path=path):
                self.assertEqual(self.process(path).status_code, 404)


class OwnershipTest(BatchApiTest):
    def test_a_stranger_cannot_process_a_batch(self):
        batch_id = self.create(self.two_items()).json()["data"]["id"]

        response = self.process(batch_id, email=STRANGER_EMAIL)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "BATCH_ACTION_NOT_ALLOWED")
        self.assertEqual(Transfer.objects.count(), 0)

    def test_a_stranger_is_told_the_same_thing_about_a_batch_that_does_not_exist(self):
        batch_id = self.create(self.two_items()).json()["data"]["id"]

        theirs = self.process(batch_id, email=STRANGER_EMAIL)
        missing = self.process(ObjectId(), email=STRANGER_EMAIL)

        self.assertEqual(theirs.status_code, 403)
        self.assertEqual(missing.status_code, 404)
        self.assertNotEqual(theirs.json()["error"]["message"], missing.json()["error"]["message"])
        self.assertNotIn(missing.json()["error"]["code"], theirs.json()["error"]["message"])

    def test_a_batch_listing_holds_only_the_callers_own_batches(self):
        self.create(self.two_items())
        theirs = self.create(self.two_items(), email=STRANGER_EMAIL).json()["data"]["id"]

        listed = self.list().json()["data"]

        self.assertEqual(listed["total"], 1)
        self.assertNotIn(theirs, [batch["id"] for batch in listed["batches"]])

        stranger = self.list(email=STRANGER_EMAIL).json()["data"]

        self.assertEqual(stranger["total"], 1)
        self.assertEqual(stranger["batches"][0]["id"], theirs)

    def test_a_body_cannot_name_a_different_owner(self):
        response = self.create(self.two_items())

        batch_id = response.json()["data"]["id"]

        # Even if a client tries to point a batch at somebody else, the batch belongs to the
        # account that was signed in.
        self.assertEqual(self.list().json()["data"]["batches"][0]["id"], batch_id)
        self.assertEqual(self.process(batch_id).status_code, 200)
        self.assertEqual(self.list(email=STRANGER_EMAIL).json()["data"]["total"], 0)


class ListTest(BatchApiTest):
    def test_an_account_with_no_batches_is_told_so_rather_than_given_an_error(self):
        listed = self.list().json()["data"]

        self.assertEqual(listed["total"], 0)
        self.assertEqual(listed["batches"], [])

    def test_batches_are_listed_newest_first(self):
        first = self.create(self.two_items()).json()["data"]["id"]
        second = self.create(self.two_items()).json()["data"]["id"]

        listed = self.list().json()["data"]["batches"]

        self.assertEqual([batch["id"] for batch in listed], [second, first])

    def test_the_listing_can_be_narrowed_to_a_state(self):
        pending = self.create(self.two_items()).json()["data"]["id"]
        done = self.create(self.two_items()).json()["data"]["id"]
        self.process(done)

        listed = self.list("?state=COMPLETED").json()["data"]

        self.assertEqual([batch["id"] for batch in listed["batches"]], [done])
        self.assertNotIn(pending, [batch["id"] for batch in listed["batches"]])
        self.assertEqual(listed["state"], "COMPLETED")

    def test_a_state_that_is_not_a_state_is_refused_rather_than_ignored(self):
        response = self.list("?state=MAYBE")

        self.assertEqual(response.status_code, 400)
        self.assertIn("state", response.json()["error"]["details"]["fieldErrors"])

    def test_the_listing_is_paged(self):
        for _ in range(3):
            self.create(self.two_items())

        page = self.list("?limit=2&offset=1").json()["data"]

        self.assertEqual(len(page["batches"]), 2)
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["limit"], 2)
        self.assertEqual(page["offset"], 1)

    def test_a_limit_that_is_not_a_number_is_refused(self):
        self.assertEqual(self.list("?limit=many").status_code, 400)

    def test_reading_a_listing_moves_no_money(self):
        self.create(self.two_items())

        self.list()
        self.list("?state=PENDING")

        self.assertEqual(Transfer.objects.count(), 0)