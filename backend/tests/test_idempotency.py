"""R3 — one client operation, at most one transfer.

The tests that matter most here are the two kinds of repeat: the same key sent again, and
the same key sent again at the same moment. The first is a retry after an ambiguous
response, the second is the race the requirement exists to close. Both must leave one
transfer, one debit, and one pair of ledger entries.
"""

import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock

from bson import ObjectId
from mongoengine.errors import NotUniqueError

from apps.shared.errors import AppError
from apps.shared.mongo import collection
from apps.transfers import repository as transfer_repository
from apps.transfers import services as transfer_services
from apps.transfers.constants import (
    FAILED_STATE,
    IDEMPOTENCY_KEY_CONTENDED_CODE,
    IDEMPOTENCY_KEY_CONTENDED_MESSAGE,
    RECOVERY_AGE_SECONDS,
)
from apps.transfers.models import Transfer
from apps.wallet import repository as wallet_repository
from apps.wallet.models import LedgerEntry

from .base import MongoTestCase

TRANSFERS_PATH = "/api/v1/transfers"
HISTORY_PATH = "/api/v1/wallet/transactions"

SENDER_EMAIL = "aarav.mehta@wallet.demo"
RECIPIENT_EMAIL = "fatima.noor@wallet.demo"
OTHER_EMAIL = "diego.santos@wallet.demo"

SENDER_OPENING = 100000
RECIPIENT_OPENING = 500
OTHER_OPENING = 700
OPENING_ENTRIES = 3

KEY = "transfer-key-0001"
OTHER_KEY = "transfer-key-0002"

# Captured before any test patches it. Tests that stand in for a competing request really do
# insert and really do fail a transfer, which they cannot do through the patch without recursing
# into their own refusal.
REAL_CREATE_TRANSFER = transfer_repository.create_transfer


@contextmanager
def interrupted_before_completion():
    """Stop a transfer after both ledger entries exist but before it is marked complete."""
    with mock.patch.object(
        transfer_repository, "mark_completed", side_effect=RuntimeError("interrupted before completion")
    ):
        yield


@contextmanager
def interrupted_before_any_movement():
    """Stop a transfer before either wallet has moved, as a crash on its first write would."""
    original = transfer_services.recorded_movement
    seen = []

    def replacement(wallet_id, transfer, role, amount_minor):
        seen.append(role)

        if len(seen) == 1:
            raise RuntimeError("interrupted before any money moved")

        return original(wallet_id, transfer, role, amount_minor)

    with mock.patch.object(transfer_services, "recorded_movement", replacement):
        yield


class IdempotencyTestCase(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.sender = self.create_account(name="Aarav Mehta", email=SENDER_EMAIL, opening_balance_minor=SENDER_OPENING)
        self.recipient = self.create_account(
            name="Fatima Noor", email=RECIPIENT_EMAIL, opening_balance_minor=RECIPIENT_OPENING
        )
        self.other = self.create_account(name="Diego Santos", email=OTHER_EMAIL, opening_balance_minor=OTHER_OPENING)

    def send(self, amount_minor=1000, recipient_email=RECIPIENT_EMAIL, note=None, key=None, account=None):
        """Run a transfer the way the route does, with whether it created the transfer."""
        sender = self.account_document(account or self.sender)

        return transfer_services.execute_transfer(sender, recipient_email, amount_minor, note, key)

    def balance(self, account):
        return wallet_repository.find_wallet_by_user_id(account.id)["availableBalanceMinor"]

    def movements(self, account):
        return wallet_repository.find_wallet_by_user_id(account.id)["pendingMovements"]

    def legs(self, transfer_id):
        return LedgerEntry.objects(__raw__={"transactionId": transfer_id}).count()

    def stored_transfer(self, key=KEY, account=None):
        return transfer_repository.find_by_key((account or self.sender).id, key)

    def only_transfer(self):
        return Transfer.objects(__raw__={}).as_pymongo().first()

    def backdate(self, transfer_id):
        """Age a transfer past the point where recovery assumes its request has gone."""
        moment = datetime.now(timezone.utc) - timedelta(seconds=RECOVERY_AGE_SECONDS * 2)

        collection(Transfer).update_one({"_id": transfer_id}, {"$set": {"createdAt": moment}})

    def request(self, body, key=None, token=None):
        headers = {}

        if key is not None:
            headers["HTTP_IDEMPOTENCY_KEY"] = key

        # An empty token signs the request out, so it is checked against None explicitly.
        client = self.api_client(self.token_for(SENDER_EMAIL) if token is None else token)

        return client.post(TRANSFERS_PATH, body, content_type="application/json", **headers)

    def error_of(self, response):
        return response.json()["error"]


class FirstRequestTest(IdempotencyTestCase):
    def test_a_key_is_recorded_on_the_transfer_it_named(self):
        response, created = self.send(key=KEY)

        self.assertTrue(created)
        self.assertEqual(response["transfer"]["state"], "COMPLETED")

        stored = self.stored_transfer()

        self.assertEqual(stored["idempotencyKey"], KEY)
        self.assertEqual(stored["senderUserId"], self.sender.id)
        self.assertTrue(stored["idempotencyFingerprint"])
        self.assertIsNone(stored.get("idempotencyKeyReleasedAt"))

    def test_a_transfer_sent_without_a_key_holds_no_key(self):
        response, created = self.send()

        self.assertTrue(created)
        self.assertEqual(Transfer.objects.count(), 1)

        stored = self.only_transfer()

        self.assertNotIn("idempotencyKey", stored)
        self.assertNotIn("idempotencyFingerprint", stored)
        self.assertNotIn("idempotencyKeyReleasedAt", stored)

    def test_the_key_and_its_fingerprint_are_never_handed_to_the_client(self):
        response, _ = self.send(key=KEY)

        self.assertNotIn(KEY, str(response))
        self.assertNotIn(self.stored_transfer()["idempotencyFingerprint"], str(response))

    def test_the_same_request_sent_twice_without_a_key_is_still_two_transfers(self):
        # The key is optional, so a client that does not use one keeps the behaviour it had.
        self.send(amount_minor=1000)
        self.send(amount_minor=1000)

        self.assertEqual(Transfer.objects.count(), 2)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - 2000)

    def test_a_key_sends_only_one_transfer_beside_a_keyless_one(self):
        # The index that enforces this covers keys that were sent, so it cannot start
        # limiting the transfers a keyless client makes.
        self.send(amount_minor=1000)
        self.send(amount_minor=1000, key=KEY)
        self.send(amount_minor=1000, key=KEY)

        self.assertEqual(Transfer.objects.count(), 2)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - 2000)

    def test_the_fingerprint_differs_when_the_amount_the_recipient_or_the_note_differs(self):
        # Every refusal below rests on this, so it is pinned directly rather than only
        # through the refusals it produces.
        recipient = self.account_document(self.recipient)["_id"]
        other = self.account_document(self.other)["_id"]
        base = transfer_services.request_fingerprint(recipient, 1000, None)
        variants = {
            "amount": transfer_services.request_fingerprint(recipient, 1001, None),
            "recipient": transfer_services.request_fingerprint(other, 1000, None),
            "note": transfer_services.request_fingerprint(recipient, 1000, "dinner"),
        }

        self.assertEqual(len({base, *variants.values()}), 4)

    def test_a_separator_in_a_note_cannot_rearrange_the_request_it_describes(self):
        recipient = self.account_document(self.recipient)["_id"]
        crafted = transfer_services.request_fingerprint(recipient, 1000, "note|700|someone@wallet.demo")
        honest = transfer_services.request_fingerprint(recipient, 1000, "note")

        self.assertNotEqual(crafted, honest)


class ExactRetryTest(IdempotencyTestCase):
    def setUp(self):
        super().setUp()

        self.first, _ = self.send(key=KEY)

    def test_the_retry_returns_the_original_transfer(self):
        response, created = self.send(key=KEY)

        self.assertFalse(created)
        self.assertEqual(response["transfer"]["id"], self.first["transfer"]["id"])
        self.assertEqual(response["transfer"]["createdAt"], self.first["transfer"]["createdAt"])
        self.assertEqual(response["transfer"]["completedAt"], self.first["transfer"]["completedAt"])

    def test_the_retry_reports_that_it_replayed_the_operation(self):
        response, _ = self.send(key=KEY)

        self.assertTrue(response["idempotentReplay"])

    def test_the_original_response_is_not_marked_as_a_replay(self):
        self.assertNotIn("idempotentReplay", self.first)

    def test_the_retry_does_not_move_money_again(self):
        self.send(key=KEY)

        self.assertEqual(self.balance(self.sender), SENDER_OPENING - 1000)
        self.assertEqual(self.balance(self.recipient), RECIPIENT_OPENING + 1000)

    def test_the_retry_records_no_second_ledger_entry(self):
        self.send(key=KEY)

        self.assertEqual(self.legs(ObjectId(self.first["transfer"]["id"])), 2)
        self.assertEqual(LedgerEntry.objects.count(), OPENING_ENTRIES + 2)

    def test_the_retry_creates_no_second_transfer(self):
        self.send(key=KEY)

        self.assertEqual(Transfer.objects.count(), 1)

    def test_the_retry_reports_the_balance_the_ledger_holds_now(self):
        # Another transfer may have moved the wallet since, so a replay reads the balance
        # rather than quoting the one the original request produced.
        self.send(amount_minor=1000, recipient_email=OTHER_EMAIL)

        response, _ = self.send(key=KEY)

        self.assertEqual(response["wallet"]["availableBalanceMinor"], SENDER_OPENING - 2000)
        self.assertEqual(response["transfer"]["amountMinor"], 1000)


class DifferentRequestTest(IdempotencyTestCase):
    """A key names one request. A second request that reuses it must not borrow its result."""

    def setUp(self):
        super().setUp()

        self.send(amount_minor=1000, recipient_email=RECIPIENT_EMAIL, key=KEY)

    def assert_refused(self, **kwargs):
        with self.assertRaises(AppError) as raised:
            self.send(key=KEY, **kwargs)

        self.assertEqual(raised.exception.code, "IDEMPOTENCY_KEY_REUSED")

        return raised.exception

    def test_the_same_key_with_a_different_amount_is_refused(self):
        self.assert_refused(amount_minor=10000)

    def test_the_same_key_with_a_different_recipient_is_refused(self):
        self.assert_refused(amount_minor=1000, recipient_email=OTHER_EMAIL)

    def test_the_same_key_with_a_note_it_did_not_have_is_refused(self):
        self.assert_refused(amount_minor=1000, note="dinner")

    def test_the_same_key_with_a_different_note_is_refused(self):
        self.send(note="dinner", key=OTHER_KEY)

        with self.assertRaises(AppError) as raised:
            self.send(amount_minor=1000, note="taxi", key=OTHER_KEY)

        self.assertEqual(raised.exception.code, "IDEMPOTENCY_KEY_REUSED")

    def test_a_refused_key_moves_no_money_and_creates_no_transfer(self):
        self.assert_refused(amount_minor=10000, recipient_email=OTHER_EMAIL, note="dinner")

        self.assertEqual(self.balance(self.sender), SENDER_OPENING - 1000)
        self.assertEqual(self.balance(self.recipient), RECIPIENT_OPENING + 1000)
        self.assertEqual(self.balance(self.other), OTHER_OPENING)
        self.assertEqual(Transfer.objects.count(), 1)

    def test_the_refusal_says_nothing_about_the_transfer_the_key_claimed(self):
        error = self.assert_refused(amount_minor=10000, recipient_email=OTHER_EMAIL)

        self.assertNotIn("Fatima", error.message)
        self.assertNotIn("10000", error.message)
        self.assertIsNone(error.details)

    def test_a_key_that_was_never_spent_is_not_refused(self):
        self.send(amount_minor=1000, key=OTHER_KEY)

        self.assertEqual(Transfer.objects.count(), 2)


class ConcurrentRequestTest(IdempotencyTestCase):
    def run_together(self, *calls):
        """Start every call at the same moment and collect what each one produced."""
        outcomes = [None] * len(calls)
        gate = threading.Barrier(len(calls))

        def worker(index, call):
            gate.wait()

            try:
                response, created = call()

                outcomes[index] = ("ok", response, created)
            except AppError as error:
                outcomes[index] = ("refused", error, None)

        threads = [threading.Thread(target=worker, args=(index, call)) for index, call in enumerate(calls)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        return outcomes

    def assert_one_transfer(self, amount_minor=1000):
        stored = self.only_transfer()

        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(self.legs(stored["_id"]), 2)
        self.assertEqual(LedgerEntry.objects.count(), OPENING_ENTRIES + 2)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - amount_minor)

    def test_two_identical_requests_at_the_same_moment_move_money_once(self):
        outcomes = self.run_together(lambda: self.send(key=KEY), lambda: self.send(key=KEY))

        self.assertEqual([kind for kind, _, _ in outcomes], ["ok", "ok"])
        self.assertEqual(sorted(created for _, _, created in outcomes), [False, True])
        self.assertEqual(len({response["transfer"]["id"] for _, response, _ in outcomes}), 1)
        self.assertEqual(self.balance(self.recipient), RECIPIENT_OPENING + 1000)
        self.assert_one_transfer()

    def test_many_retries_of_one_request_move_money_once(self):
        outcomes = self.run_together(*[lambda: self.send(key=KEY) for _ in range(6)])

        self.assertEqual([kind for kind, _, _ in outcomes], ["ok"] * 6)
        self.assertEqual(len({response["transfer"]["id"] for _, response, _ in outcomes}), 1)
        self.assert_one_transfer()

    def test_the_same_key_with_a_different_amount_at_the_same_moment_transfers_once(self):
        outcomes = self.run_together(
            lambda: self.send(amount_minor=1000, key=KEY),
            lambda: self.send(amount_minor=10000, key=KEY),
        )

        self.assertEqual(sorted(kind for kind, _, _ in outcomes), ["ok", "refused"])

        refused = next(error for kind, error, _ in outcomes if kind == "refused")

        self.assertEqual(refused.code, "IDEMPOTENCY_KEY_REUSED")

        # Whichever amount won the claim is the one that moved money, exactly once.
        self.assert_one_transfer(self.only_transfer()["amountMinor"])

    def test_the_same_key_with_a_different_recipient_at_the_same_moment_transfers_once(self):
        outcomes = self.run_together(
            lambda: self.send(recipient_email=RECIPIENT_EMAIL, key=KEY),
            lambda: self.send(recipient_email=OTHER_EMAIL, key=KEY),
        )

        self.assertEqual(sorted(kind for kind, _, _ in outcomes), ["ok", "refused"])
        self.assert_one_transfer()

        # Exactly one of the two was paid, and the amount is the one the winner sent.
        paid = self.balance(self.recipient) - RECIPIENT_OPENING + self.balance(self.other) - OTHER_OPENING

        self.assertEqual(paid, 1000)

    def test_the_same_key_with_a_different_note_at_the_same_moment_transfers_once(self):
        outcomes = self.run_together(
            lambda: self.send(note="dinner", key=KEY),
            lambda: self.send(note="taxi", key=KEY),
        )

        self.assertEqual(sorted(kind for kind, _, _ in outcomes), ["ok", "refused"])
        self.assertEqual(self.balance(self.recipient), RECIPIENT_OPENING + 1000)
        self.assert_one_transfer()


class RetryWhileInFlightTest(IdempotencyTestCase):
    """What a retry does while the request that claimed its key has not finished."""

    def leave_in_flight(self, key=KEY, before_movement=False):
        """Interrupt a transfer so it holds its key and is not yet settled."""
        interruption = interrupted_before_any_movement() if before_movement else interrupted_before_completion()

        with interruption:
            with self.assertRaises(RuntimeError):
                self.send(key=key)

        return self.stored_transfer(key)

    def test_a_retry_of_a_transfer_still_in_flight_reports_it_as_it_is(self):
        in_flight = self.leave_in_flight()

        response, created = self.send(key=KEY)

        self.assertFalse(created)
        self.assertTrue(response["idempotentReplay"])
        self.assertEqual(response["transfer"]["state"], "PENDING")
        self.assertIsNone(response["transfer"]["completedAt"])
        self.assertEqual(response["transfer"]["id"], str(in_flight["_id"]))

    def test_a_retry_does_not_become_a_second_way_to_finish_the_same_transfer(self):
        # Recovery is the one thing that finishes a transfer after the fact, and it leaves
        # alone any transfer young enough for its own request to still be working on it.
        in_flight = self.leave_in_flight()

        self.send(key=KEY)

        self.assertEqual(self.stored_transfer()["state"], "PENDING")
        self.assertEqual(len(self.movements(self.sender)), 1)
        self.assertEqual(len(self.movements(self.recipient)), 1)

    def test_a_retry_of_a_request_that_was_never_answered_resolves_to_the_one_transfer(self):
        in_flight = self.leave_in_flight()
        self.backdate(in_flight["_id"])

        response, created = self.send(key=KEY)

        self.assertFalse(created)
        self.assertTrue(response["idempotentReplay"])
        self.assertEqual(response["transfer"]["state"], "COMPLETED")
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(self.legs(in_flight["_id"]), 2)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - 1000)
        self.assertEqual(self.balance(self.recipient), RECIPIENT_OPENING + 1000)
        self.assertEqual(self.movements(self.sender), [])
        self.assertEqual(self.movements(self.recipient), [])

    def test_a_retry_of_a_request_that_moved_no_money_resolves_the_same_way(self):
        in_flight = self.leave_in_flight(before_movement=True)

        self.assertEqual(self.balance(self.sender), SENDER_OPENING)
        self.assertEqual(self.movements(self.sender), [])

        self.backdate(in_flight["_id"])

        response, created = self.send(key=KEY)

        self.assertFalse(created)
        self.assertEqual(response["transfer"]["state"], "COMPLETED")
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - 1000)

    def test_the_key_is_held_while_the_transfer_settles_and_survives_afterwards(self):
        in_flight = self.leave_in_flight()
        self.backdate(in_flight["_id"])

        self.send(key=KEY)

        self.assertEqual(self.stored_transfer()["idempotencyKey"], KEY)
        self.assertEqual(self.send(key=KEY)[0]["transfer"]["id"], str(in_flight["_id"]))


class FailedTransferTest(IdempotencyTestCase):
    """A transfer that failed moved no money, so it never spent the sender's key."""

    def fail_a_transfer(self, key=KEY, amount_minor=1000000):
        with self.assertRaises(AppError) as raised:
            self.send(amount_minor=amount_minor, key=key)

        self.assertEqual(raised.exception.code, "INSUFFICIENT_FUNDS")

        return self.only_transfer()

    def test_a_failed_transfer_moves_no_money_at_all(self):
        failed = self.fail_a_transfer()

        self.assertEqual(failed["state"], FAILED_STATE)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING)
        self.assertEqual(self.balance(self.recipient), RECIPIENT_OPENING)
        self.assertEqual(self.movements(self.sender), [])
        self.assertEqual(self.movements(self.recipient), [])
        self.assertEqual(self.legs(failed["_id"]), 0)
        self.assertEqual(LedgerEntry.objects.count(), OPENING_ENTRIES)

    def test_a_failed_transfer_gives_its_key_back_in_the_same_write_that_fails_it(self):
        failed = self.fail_a_transfer()

        self.assertEqual(failed["failureCode"], "INSUFFICIENT_FUNDS")
        self.assertNotIn("idempotencyKey", failed)
        self.assertNotIn("idempotencyFingerprint", failed)
        self.assertIsNotNone(failed["idempotencyKeyReleasedAt"])
        self.assertIsNone(self.stored_transfer())

    def test_a_key_released_by_a_failure_can_be_spent_again(self):
        self.fail_a_transfer()

        response, created = self.send(amount_minor=1000, key=KEY)

        self.assertTrue(created)
        self.assertEqual(response["transfer"]["state"], "COMPLETED")
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - 1000)
        self.assertEqual(self.balance(self.recipient), RECIPIENT_OPENING + 1000)
        self.assertEqual(LedgerEntry.objects.count(), OPENING_ENTRIES + 2)

    def test_a_released_key_takes_a_new_request_rather_than_repeating_the_old_one(self):
        self.fail_a_transfer(amount_minor=1000000)

        response, created = self.send(amount_minor=2000, recipient_email=OTHER_EMAIL, key=KEY)

        self.assertTrue(created)
        self.assertEqual(response["transfer"]["amountMinor"], 2000)
        self.assertEqual(response["transfer"]["recipientUserId"], str(self.other.id))
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - 2000)
        self.assertEqual(self.balance(self.other), OTHER_OPENING + 2000)

    def test_a_key_left_on_a_failed_transfer_is_refused_rather_than_answered(self):
        # A failed transfer releases its key, so the route cannot reach this. If it ever
        # could, the answer must not be a result for an operation that never happened.
        recipient = self.account_document(self.recipient)
        failed = transfer_repository.create_transfer(
            {
                "sender_user_id": self.sender.id,
                "sender_wallet_id": wallet_repository.find_wallet_by_user_id(self.sender.id)["_id"],
                "recipient_user_id": recipient["_id"],
                "recipient_wallet_id": wallet_repository.find_wallet_by_user_id(self.recipient.id)["_id"],
                "amount_minor": 1000,
                "state": FAILED_STATE,
                "failure_code": "INSUFFICIENT_FUNDS",
                "idempotency_key": KEY,
                "idempotency_fingerprint": transfer_services.request_fingerprint(recipient["_id"], 1000, None),
            }
        )

        with mock.patch.dict(failed, {}, clear=False):
            with self.assertRaises(AppError) as raised:
                self.send(key=KEY)

        self.assertEqual(raised.exception.code, "TRANSFER_ALREADY_FAILED")
        self.assertEqual(self.balance(self.sender), SENDER_OPENING)


class OwnershipTest(IdempotencyTestCase):
    def test_two_senders_may_each_spend_the_same_key(self):
        first, _ = self.send(key=KEY)

        second_sender = self.create_account(
            name="Ravi Anand", email="ravi.anand@wallet.demo", opening_balance_minor=5000
        )
        response, created = self.send(amount_minor=1000, recipient_email=OTHER_EMAIL, key=KEY, account=second_sender)

        self.assertTrue(created)
        self.assertNotEqual(response["transfer"]["id"], first["transfer"]["id"])
        self.assertEqual(Transfer.objects.count(), 2)
        self.assertEqual(self.balance(second_sender), 4000)

    def test_a_sender_reusing_a_key_it_spent_on_something_else_is_refused(self):
        self.send(key=KEY)

        with self.assertRaises(AppError) as raised:
            self.send(amount_minor=2000, key=KEY)

        self.assertEqual(raised.exception.code, "IDEMPOTENCY_KEY_REUSED")
        self.assertEqual(Transfer.objects.count(), 1)

    def test_the_key_is_never_published_to_the_other_party(self):
        self.send(key=KEY)

        response = self.api_client(self.token_for(RECIPIENT_EMAIL)).get(HISTORY_PATH)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(KEY, response.content.decode())
        self.assertNotIn(self.stored_transfer()["idempotencyFingerprint"], response.content.decode())

    def test_a_request_without_a_session_cannot_spend_a_key(self):
        response = self.request(
            {"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 1000}, key=KEY, token=""
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.error_of(response)["code"], "AUTH_REQUIRED")
        self.assertIsNone(self.stored_transfer())

    def test_the_route_derives_the_key_owner_from_the_session_not_the_request(self):
        self.send(key=KEY)

        second_sender = self.create_account(
            name="Ravi Anand", email="ravi.anand@wallet.demo", opening_balance_minor=5000
        )
        response = self.request(
            {"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 1000},
            key=KEY,
            token=self.token_for("ravi.anand@wallet.demo"),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Transfer.objects.count(), 2)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - 1000)
        self.assertEqual(self.balance(second_sender), 4000)


class HttpContractTest(IdempotencyTestCase):
    def body(self, **overrides):
        return {"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 1000, **overrides}

    def test_a_first_request_answers_201_and_a_replay_answers_200(self):
        first = self.request(self.body(), key=KEY)
        second = self.request(self.body(), key=KEY)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["data"]["transfer"]["id"], second.json()["data"]["transfer"]["id"])
        self.assertNotIn("idempotentReplay", first.json()["data"])
        self.assertTrue(second.json()["data"]["idempotentReplay"])
        self.assertEqual(second["Idempotent-Replayed"], "true")

    def test_a_request_without_the_header_is_honoured_as_before(self):
        response = self.request(self.body())

        self.assertEqual(response.status_code, 201)
        self.assertNotIn("idempotentReplay", response.json()["data"])

    def test_the_same_request_written_differently_is_the_same_request(self):
        # The fingerprint is taken from what the request was validated into, so an address in
        # another case, an amount written as a string and a padded note all name one
        # operation rather than three.
        first = self.request(self.body(), key=KEY)
        second = self.request(
            self.body(recipientEmail=RECIPIENT_EMAIL.upper(), amountMinor="1000"), key=KEY
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["data"]["transfer"]["id"], second.json()["data"]["transfer"]["id"])
        self.assertEqual(Transfer.objects.count(), 1)

    def test_a_note_that_differs_only_in_its_padding_is_the_same_request(self):
        first = self.request(self.body(note="dinner"), key=KEY)
        second = self.request(self.body(note="  dinner  "), key=KEY)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["data"]["transfer"]["note"], "dinner")
        self.assertEqual(Transfer.objects.count(), 1)

    def test_the_header_is_read_without_regard_to_its_case(self):
        response = self.api_client(self.token_for(SENDER_EMAIL)).post(
            TRANSFERS_PATH, self.body(), content_type="application/json", HTTP_IDEMPOTENCY_KEY=KEY
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.stored_transfer()["idempotencyKey"], KEY)

    def test_a_key_surrounded_by_whitespace_is_the_key_it_was_sent_as(self):
        self.request(self.body(), key=KEY)

        response = self.request(self.body(), key=f"  {KEY}  ")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["idempotentReplay"])

    def test_a_key_used_for_a_different_request_is_refused_with_its_own_code(self):
        self.request(self.body(), key=KEY)

        response = self.request(self.body(amountMinor=10000), key=KEY)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_of(response)["code"], "IDEMPOTENCY_KEY_REUSED")
        self.assertNotIn("details", self.error_of(response))
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - 1000)

    def test_a_key_used_for_a_different_recipient_is_refused(self):
        self.request(self.body(), key=KEY)

        response = self.request(self.body(recipientEmail=OTHER_EMAIL), key=KEY)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_of(response)["code"], "IDEMPOTENCY_KEY_REUSED")

    def test_a_key_used_for_a_different_note_is_refused(self):
        self.request(self.body(note="dinner"), key=KEY)

        response = self.request(self.body(note="taxi"), key=KEY)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_of(response)["code"], "IDEMPOTENCY_KEY_REUSED")

    def unusable_keys(self):
        return [
            ("empty", ""),
            ("blank", "   "),
            ("too short", "abc123"),
            ("too long", "a" * 65),
            ("a space", "transfer key"),
            ("a slash", "transfer/key"),
            ("an at sign", "transfer@key"),
            ("not ascii", "transfère"),
        ]

    def test_every_malformed_key_is_refused_before_anything_is_written(self):
        for label, key in self.unusable_keys():
            with self.subTest(key=label):
                response = self.request(self.body(), key=key)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.error_of(response)["code"], "IDEMPOTENCY_KEY_INVALID")

        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING)
        self.assertEqual(self.balance(self.recipient), RECIPIENT_OPENING)

    def test_the_boundaries_of_the_key_format_are_accepted(self):
        for key in ("a" * 8, "a" * 64, "9f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f", "key.with:all_1-2"):
            with self.subTest(key=key):
                response = self.request(self.body(), key=key)

                self.assertEqual(response.status_code, 201)

        self.assertEqual(Transfer.objects.count(), 4)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - 4000)

    def test_a_malformed_key_is_not_the_error_a_wrong_body_is_reported_as(self):
        response = self.request({"recipientEmail": "nope"}, key="short")

        # The body is checked first, so a client is told about the field it can correct.
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["code"], "VALIDATION_ERROR")

    def contend_every_claim(self):
        """Refuse every attempt to claim a key, and release it again each time.

        This is the request that loses the race more times than the engine will race for it:
        every refusal proves a competing request got there first, and every one of them handed
        the key back, so nothing is there to answer from. A mock that only refused would leave
        the key held and be answered by a replay instead, which is a different outcome.
        """

        def lose_the_claim(values):
            claimed = REAL_CREATE_TRANSFER(values)
            transfer_repository.mark_failed(claimed["_id"], "RIVAL_FAILED")

            raise NotUniqueError("lost the claim to a request that then failed")

        return mock.patch.object(transfer_repository, "create_transfer", lose_the_claim)

    def test_a_key_raced_past_the_attempt_budget_answers_409_rather_than_failing(self):
        # The engine runs out of attempts to race for this key, and nothing holds it. That has to
        # reach the caller as a refusal it may repeat, because a request answered 500 here would
        # tell a client its transfer is in an unknown state when in fact nothing was sent.
        with self.contend_every_claim():
            response = self.request(self.body(), key=KEY)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_of(response)["code"], IDEMPOTENCY_KEY_CONTENDED_CODE)
        self.assertEqual(self.error_of(response)["message"], IDEMPOTENCY_KEY_CONTENDED_MESSAGE)
        self.assertNotIn("details", self.error_of(response))

    def test_a_refused_for_contention_request_moves_no_money_and_spends_no_key(self):
        with self.contend_every_claim():
            self.request(self.body(), key=KEY)

        self.assertEqual(self.balance(self.sender), SENDER_OPENING)
        self.assertEqual(self.balance(self.recipient), RECIPIENT_OPENING)
        self.assertEqual(LedgerEntry.objects.count(), OPENING_ENTRIES)
        self.assertIsNone(self.stored_transfer(), "a refused request must not leave the key spent")
        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 0)

    def test_the_same_request_answered_again_after_contention_is_accepted(self):
        # The refusal is only honest if repeating it works, which is the whole reason it is a
        # refusal: by then the rivals have stopped and the key is free.
        with self.contend_every_claim():
            self.assertEqual(self.request(self.body(), key=KEY).status_code, 409)

        retry = self.request(self.body(), key=KEY)

        self.assertEqual(retry.status_code, 201)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - 1000)
        self.assertEqual(self.balance(self.recipient), RECIPIENT_OPENING + 1000)
        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 1)

    def test_a_key_on_a_request_that_is_refused_for_funds_is_not_spent(self):
        response = self.request(self.body(amountMinor=1000000), key=KEY)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_of(response)["code"], "INSUFFICIENT_FUNDS")
        self.assertIsNone(self.stored_transfer())

        retry = self.request(self.body(), key=KEY)

        self.assertEqual(retry.status_code, 201)
        self.assertEqual(self.balance(self.recipient), RECIPIENT_OPENING + 1000)

    def test_a_key_on_a_request_that_names_no_recipient_is_not_spent(self):
        response = self.request({"amountMinor": 1000}, key=KEY)

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.stored_transfer())

        retry = self.request(self.body(), key=KEY)

        self.assertEqual(retry.status_code, 201)
        self.assertEqual(Transfer.objects.count(), 1)

    def test_a_key_on_a_request_to_yourself_is_not_spent(self):
        response = self.request(self.body(recipientEmail=SENDER_EMAIL), key=KEY)

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.stored_transfer())

    def test_a_key_on_a_request_to_someone_unknown_is_not_spent(self):
        response = self.request(self.body(recipientEmail="nobody@wallet.demo"), key=KEY)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.error_of(response)["code"], "RECIPIENT_NOT_FOUND")
        self.assertIsNone(self.stored_transfer())

    def test_the_route_still_only_accepts_posts(self):
        response = self.api_client(self.token_for(SENDER_EMAIL)).get(TRANSFERS_PATH)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.error_of(response)["code"], "METHOD_NOT_ALLOWED")
