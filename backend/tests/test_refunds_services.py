"""R5 — giving a completed transfer back, in whole or in part.

The behaviour that matters is that a refund is the transfer engine's work and nobody
else's. Every refund test therefore checks what the engine did as well as what the refund
says, because a refund that says `COMPLETED` while no money moved is the one failure this
feature cannot have.

The other tests that matter are the interruptions and the race on the refundable total: an
attempt that stops partway through, an approval-style retry after a lost response, and two
refunds arriving for the same original at the same moment.
"""

import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock

from bson import ObjectId

from apps.refunds import repository, services
from apps.refunds.constants import REFUND_CLAIM_GRACE_SECONDS
from apps.refunds.models import Refund
from apps.shared.documents import now_utc
from apps.shared.errors import AppError, ValidationError
from apps.shared.mongo import collection
from apps.transfers import repository as transfer_repository
from apps.transfers import services as transfer_services
from apps.transfers.constants import COMPLETED_STATE, REFUND_KEY_PREFIX
from apps.transfers.models import Transfer
from apps.wallet import repository as wallet_repository
from apps.wallet.models import LedgerEntry

from .base import MongoTestCase

SENDER_EMAIL = "aarav.mehta@wallet.demo"
PAYEE_EMAIL = "fatima.noor@wallet.demo"
STRANGER_EMAIL = "grace.obi@wallet.demo"

SENDER_OPENING = 245000
PAYEE_OPENING = 12850
STRANGER_OPENING = 700

ORIGINAL = 50000
NOTE = "Order 2214"


@contextmanager
def interrupted_before_the_refund_is_recorded():
    """Stop a refund after the engine moved the money but before the refund knows."""
    with mock.patch.object(repository, "settle", side_effect=RuntimeError("interrupted before recording")):
        yield


@contextmanager
def the_engine_refuses():
    """The engine turning a refund down, having moved nothing."""

    def refuse(*arguments, **keywords):
        raise AppError(409, "INSUFFICIENT_FUNDS", "This wallet does not have enough available balance for that amount.")

    with mock.patch.object(transfer_services, "execute_transfer", refuse):
        yield


class RefundTestCase(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.sender = self.create_account(name="Aarav Mehta", email=SENDER_EMAIL, opening_balance_minor=SENDER_OPENING)
        self.payee = self.create_account(name="Fatima Noor", email=PAYEE_EMAIL, opening_balance_minor=PAYEE_OPENING)
        self.stranger = self.create_account(name="Grace Obi", email=STRANGER_EMAIL, opening_balance_minor=STRANGER_OPENING)

        self.original = self.payee_receives(ORIGINAL)

    def payee_receives(self, amount_minor):
        """Move money to the payee through the real engine, so there is something to refund."""
        payload, _ = transfer_services.execute_transfer(
            self.account_document(self.sender), PAYEE_EMAIL, amount_minor, NOTE, None
        )

        return payload["transfer"]["id"]

    def refund(self, amount_minor=ORIGINAL, note=NOTE, original=None, initiator=None, key=None):
        payee = initiator or self.payee

        return services.create_refund(
            self.account_document(payee), original or self.original, amount_minor, note, key
        )

    def stored_refund(self, refund_id):
        return Refund.objects(__raw__={"_id": ObjectId(refund_id)}).as_pymongo().first()

    def one_refund(self):
        return Refund.objects.as_pymongo().first()

    def stored_original(self):
        return Transfer.objects(__raw__={"_id": ObjectId(self.original)}).as_pymongo().first()

    def stored_transfer(self, transfer_id):
        return Transfer.objects(__raw__={"_id": ObjectId(transfer_id)}).as_pymongo().first()

    def balance(self, account):
        return wallet_repository.find_wallet_by_user_id(account.id)["availableBalanceMinor"]

    def legs(self, transfer_id):
        return list(LedgerEntry.objects(__raw__={"transactionId": ObjectId(transfer_id)}).as_pymongo())

    def spend(self, refund_id):
        return f"{REFUND_KEY_PREFIX}{refund_id}"

    def backdate_refund(self, refund_id, seconds):
        moment = datetime.now(timezone.utc) - timedelta(seconds=seconds)

        collection(Refund).update_one({"_id": ObjectId(refund_id)}, {"$set": {"updatedAt": moment}})


class CreatingTest(RefundTestCase):
    def test_a_full_refund_moves_the_money_back_and_records_it(self):
        data, created = self.refund()

        self.assertTrue(created)
        self.assertEqual(data["refund"]["state"], "COMPLETED")
        self.assertEqual(data["refund"]["transferId"], data["transfer"]["id"])
        self.assertTrue(data["transfer"]["id"])
        self.assertFalse(data["idempotentReplay"])

        refund_transfer = self.stored_transfer(data["transfer"]["id"])

        self.assertEqual(refund_transfer["state"], COMPLETED_STATE)
        self.assertEqual(refund_transfer["senderUserId"], self.payee.id)
        self.assertEqual(refund_transfer["recipientUserId"], self.sender.id)
        self.assertEqual(refund_transfer["amountMinor"], ORIGINAL)
        self.assertEqual(refund_transfer["refundOf"], ObjectId(self.original))

        self.assertEqual(self.balance(self.payee), PAYEE_OPENING)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING)

    def test_a_partial_refund_moves_only_its_share(self):
        data, _ = self.refund(amount_minor=20000)

        self.assertEqual(data["refund"]["amountMinor"], 20000)
        self.assertEqual(self.balance(self.payee), PAYEE_OPENING + ORIGINAL - 20000)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - (ORIGINAL - 20000))
        self.assertEqual(self.stored_original().get("refundedAmountMinor"), 20000)

    def test_a_refund_transfer_is_recorded_on_both_wallets_as_refund_legs(self):
        data, _ = self.refund(amount_minor=20000)
        legs = self.legs(data["transfer"]["id"])

        self.assertEqual(len(legs), 2)

        by_kind = {leg["kind"]: leg for leg in legs}

        self.assertEqual(set(by_kind), {"REFUND_OUT", "REFUND_IN"})
        self.assertEqual(by_kind["REFUND_OUT"]["direction"], "DEBIT")
        self.assertEqual(by_kind["REFUND_OUT"]["userId"], self.payee.id)
        self.assertEqual(by_kind["REFUND_IN"]["direction"], "CREDIT")
        self.assertEqual(by_kind["REFUND_IN"]["userId"], self.sender.id)
        self.assertEqual({leg["amountMinor"] for leg in legs}, {20000})
        self.assertEqual({leg["transactionId"] for leg in legs}, {ObjectId(data["transfer"]["id"])})

    def test_the_original_transfer_and_its_legs_are_never_touched(self):
        original_legs_before = self.legs(self.original)

        self.refund(amount_minor=20000)

        self.assertEqual(self.legs(self.original), original_legs_before)
        original = self.stored_original()

        self.assertEqual(original["amountMinor"], ORIGINAL)
        self.assertEqual(original["state"], COMPLETED_STATE)
        self.assertFalse(original.get("refundOf"))

    def test_the_transfer_history_speaks_plain_english_about_what_was_given_back(self):
        self.refund(amount_minor=20000)

        kinds = {leg["kind"] for leg in LedgerEntry.objects.as_pymongo()}

        self.assertIn("REFUND_OUT", kinds)
        self.assertIn("REFUND_IN", kinds)

    def test_the_wallet_balance_still_matches_the_ledger_after_a_refund(self):
        self.refund(amount_minor=20000)

        for account in (self.sender, self.payee):
            wallet = wallet_repository.find_wallet_by_user_id(account.id)

            self.assertEqual(wallet_repository.derived_balance(wallet["_id"]), wallet["availableBalanceMinor"])


class LimitsTest(RefundTestCase):
    def test_the_exact_remaining_amount_can_be_refunded(self):
        data, _ = self.refund(amount_minor=30000, note="first")
        data2, _ = self.refund(amount_minor=20000, note="second")

        self.assertEqual(self.stored_original()["refundedAmountMinor"], ORIGINAL)
        self.assertEqual(self.balance(self.payee), PAYEE_OPENING)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING)

        legs_total = sum(leg["amountMinor"] for leg in LedgerEntry.objects(__raw__={"kind": "REFUND_IN"}).as_pymongo())

        self.assertEqual(legs_total, ORIGINAL)

    def test_refunding_past_the_remaining_amount_is_refused_without_a_penny(self):
        self.refund(amount_minor=30000)

        with self.assertRaises(AppError) as raised:
            self.refund(amount_minor=20001)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "REFUND_LIMIT_EXCEEDED")
        self.assertEqual(self.balance(self.payee), PAYEE_OPENING + ORIGINAL - 30000)
        self.assertEqual(self.stored_original()["refundedAmountMinor"], 30000)
        self.assertEqual(Refund.objects.count(), 2)  # the failed attempt is recorded, honestly

        failed = Refund.objects.order_by("-created_at").as_pymongo().first()

        self.assertEqual(failed["state"], "FAILED")
        self.assertEqual(failed["failureCode"], "REFUND_LIMIT_EXCEEDED")

    def test_a_refund_larger_than_the_original_is_refused(self):
        with self.assertRaises(AppError) as raised:
            self.refund(amount_minor=ORIGINAL + 1)

        self.assertEqual(raised.exception.code, "REFUND_LIMIT_EXCEEDED")

    def test_a_refund_transfer_cannot_itself_be_refunded(self):
        data, _ = self.refund(amount_minor=1000)

        # The refund transfer paid money to the original sender, so that is the account
        # for whom it would look refundable. The answer is that it is not refundable at
        # all, from anyone.
        with self.assertRaises(AppError) as raised:
            services.create_refund(self.account_document(self.sender), data["transfer"]["id"], 500, None, None)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "REFUND_OF_REFUND")

    def test_refunding_an_unknown_transfer_is_a_404(self):
        with self.assertRaises(AppError) as raised:
            self.refund(original=str(ObjectId()))

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.code, "ORIGINAL_TRANSFER_NOT_FOUND")

    def test_refunding_a_transfer_that_did_not_complete_is_refused(self):
        # Only a completed transfer describes money that can be given back, so a FAILED
        # original is answered from that state rather than guessing it completed.
        with mock.patch.object(
            transfer_repository,
            "find_transfer",
            return_value={"_id": ObjectId(), "state": "FAILED", "recipientUserId": self.payee.id},
        ):
            with self.assertRaises(AppError) as raised:
                services.create_refund(self.account_document(self.payee), str(ObjectId()), 100, None, None)

        self.assertEqual(raised.exception.code, "TRANSFER_NOT_REFUNDABLE")


class AuthorizationTest(RefundTestCase):
    def test_the_payee_can_refund(self):
        data, _ = self.refund(amount_minor=1000)

        self.assertEqual(data["refund"]["state"], "COMPLETED")

    def test_the_original_sender_cannot_refund(self):
        with self.assertRaises(AppError) as raised:
            self.refund(initiator=self.sender)

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.code, "REFUND_ACTION_NOT_ALLOWED")

    def test_a_third_account_cannot_refund(self):
        with self.assertRaises(AppError) as raised:
            self.refund(initiator=self.stranger)

        self.assertEqual(raised.exception.status_code, 403)


class IdempotencyTest(RefundTestCase):
    def test_the_same_key_and_the_same_request_answer_with_the_same_refund(self):
        first, created = self.refund(key="refund-key-0001")
        second, created_again = self.refund(key="refund-key-0001")

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertTrue(second["idempotentReplay"])
        self.assertEqual(second["refund"]["id"], first["refund"]["id"])
        self.assertEqual(second["transfer"]["id"], first["transfer"]["id"])
        self.assertEqual(Transfer.objects.count(), 2)  # the original, plus the refund
        self.assertEqual(LedgerEntry.objects(__raw__={"kind": "REFUND_IN"}).count(), 1)
        self.assertEqual(self.stored_original()["refundedAmountMinor"], ORIGINAL)

    def test_the_same_key_with_a_different_amount_is_refused(self):
        self.refund(key="refund-key-0002")

        with self.assertRaises(AppError) as raised:
            self.refund(amount_minor=ORIGINAL - 1, key="refund-key-0002")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "IDEMPOTENCY_KEY_REUSED")

    def test_the_same_key_with_a_different_note_is_refused(self):
        self.refund(key="refund-key-0003", note="first note")

        with self.assertRaises(AppError) as raised:
            self.refund(key="refund-key-0003", note="second note")

        self.assertEqual(raised.exception.code, "IDEMPOTENCY_KEY_REUSED")

    def test_the_same_key_with_a_different_original_is_refused(self):
        self.refund(key="refund-key-0004")
        other = self.payee_receives(1000)

        with self.assertRaises(AppError) as raised:
            self.refund(original=other, amount_minor=ORIGINAL, key="refund-key-0004")

        self.assertEqual(raised.exception.code, "IDEMPOTENCY_KEY_REUSED")

    def test_a_failed_refund_hands_its_key_back(self):
        with the_engine_refuses():
            with self.assertRaises(AppError) as raised:
                self.refund(key="refund-key-0005")

        self.assertEqual(raised.exception.code, "INSUFFICIENT_FUNDS")

        released = self.one_refund()

        self.assertEqual(released["state"], "FAILED")
        self.assertIsNone(released.get("idempotencyKey"))

        # The key is free again, so the genuine retry is a new attempt, not a replay.
        data, created = self.refund(key="refund-key-0005")

        self.assertTrue(created)
        self.assertEqual(data["refund"]["state"], "COMPLETED")
        self.assertEqual(self.balance(self.payee), PAYEE_OPENING)

    def test_a_new_key_is_a_new_refund_even_for_the_same_request(self):
        self.refund(amount_minor=1000, key="refund-key-0006")
        data, created = self.refund(amount_minor=1000, key="refund-key-0007")

        self.assertTrue(created)
        self.assertEqual(Refund.objects.count(), 2)
        self.assertEqual(LedgerEntry.objects(__raw__={"kind": "REFUND_OUT"}).count(), 2)

    def test_a_settled_refund_cannot_be_failed(self):
        data, _ = self.refund()

        failed = repository.fail(data["refund"]["id"], "INSUFFICIENT_FUNDS", now_utc())

        self.assertIsNone(failed)
        self.assertEqual(self.stored_refund(data["refund"]["id"])["state"], "COMPLETED")
        self.assertEqual(self.stored_original()["refundedAmountMinor"], ORIGINAL)

    def test_a_failed_refund_cannot_be_settled(self):
        with the_engine_refuses():
            with self.assertRaises(AppError):
                self.refund()

        refund = self.one_refund()
        settled = repository.settle(refund["_id"], str(ObjectId()), now_utc())

        self.assertIsNone(settled)
        self.assertEqual(self.stored_refund(refund["_id"])["state"], "FAILED")

    def test_a_completed_refund_is_never_resettled_even_by_a_second_read(self):
        data, _ = self.refund()
        transfer_id = data["transfer"]["id"]

        settled = repository.settle(data["refund"]["id"], transfer_id, now_utc())

        self.assertIsNone(settled)
        self.assertEqual(self.stored_refund(data["refund"]["id"])["transferId"], ObjectId(transfer_id))

    def test_the_claim_is_only_released_when_the_refusal_actually_closed_the_refund(self):
        # The refund row is marked failed in the same moment that owns releasing its
        # claim. If that transition was not the call this caller made — here, because the
        # refund had already moved on — the counter must not be touched.
        with mock.patch.object(transfer_services, "execute_transfer", side_effect=AppError(409, "INSUFFICIENT_FUNDS", "This wallet does not have enough available balance for that amount.")):
            with mock.patch.object(repository, "fail", return_value=None):
                with mock.patch.object(transfer_repository, "release_refundable_amount") as release:
                    with self.assertRaises(AppError):
                        self.refund(key="refund-key-guard-1")

        release.assert_not_called()
        self.assertEqual(self.stored_original()["refundedAmountMinor"], ORIGINAL)

    def test_the_settle_pass_is_only_allowed_to_release_after_it_marks_the_refund_failed(self):
        # A pending refund row in one hand, a failed transfer for its engine key in the
        # other: the pass should mark the refund failed and hand the claim back — here the
        # transition is taken away from it, so the counter must not be touched either.
        refund = repository.create_refund(
            {
                "original_transfer_id": ObjectId(self.original),
                "initiator_user_id": self.payee.id,
                "amount_minor": 1000,
                "state": "PENDING",
            }
        )
        transfer_repository.create_transfer(
            {
                "sender_user_id": self.payee.id,
                "sender_wallet_id": wallet_repository.find_wallet_by_user_id(self.payee.id)["_id"],
                "recipient_user_id": self.sender.id,
                "recipient_wallet_id": wallet_repository.find_wallet_by_user_id(self.sender.id)["_id"],
                "amount_minor": 1000,
                "state": "FAILED",
                "failure_code": "INSUFFICIENT_FUNDS",
                "idempotency_key": self.spend(refund["_id"]),
            }
        )

        with mock.patch.object(repository, "fail", return_value=None):
            with mock.patch.object(transfer_repository, "release_refundable_amount") as release:
                services.list_refunds(self.payee.id, 25, 0)

        release.assert_not_called()


class RacingRefundsTest(RefundTestCase):
    def run_together(self, *calls):
        outcomes = [None] * len(calls)
        gate = threading.Barrier(len(calls))

        def worker(index, call):
            gate.wait()

            try:
                outcomes[index] = ("ok", call())
            except AppError as error:
                outcomes[index] = ("refused", error.code)

        threads = [threading.Thread(target=worker, args=(index, call)) for index, call in enumerate(calls)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        return outcomes

    def settled_refunds(self):
        return Refund.objects(__raw__={"state": "COMPLETED"}).count()

    def test_two_full_refunds_at_once_refund_it_once(self):
        outcomes = self.run_together(lambda: self.refund(), lambda: self.refund())

        kinds = sorted(kind for kind, _ in outcomes)

        self.assertEqual(kinds, ["ok", "refused"])
        refused = [outcome for kind, outcome in outcomes if kind == "refused"]

        self.assertEqual(refused, ["REFUND_LIMIT_EXCEEDED"])
        self.assertEqual(self.settled_refunds(), 1)
        self.assertEqual(self.stored_original()["refundedAmountMinor"], ORIGINAL)
        self.assertEqual(self.balance(self.payee), PAYEE_OPENING)
        self.assertEqual(LedgerEntry.objects(__raw__={"kind": "REFUND_OUT"}).count(), 1)

    def test_two_partial_refunds_whose_sum_exceeds_remaining_settle_only_what_fits(self):
        outcomes = self.run_together(
            lambda: self.refund(amount_minor=40000),
            lambda: self.refund(amount_minor=20000),
        )

        settled = self.settled_refunds()
        refunded = self.stored_original()["refundedAmountMinor"]

        # Exactly one of the two fits inside the original; the other was refused.
        self.assertEqual(settled, 1)
        self.assertIn(refunded, [40000, 20000])

    def test_six_refunds_at_once_never_pass_the_original(self):
        outcomes = self.run_together(*[lambda: self.refund(amount_minor=15000) for _ in range(6)])

        refunded = self.stored_original()["refundedAmountMinor"]

        self.assertLessEqual(refunded, ORIGINAL)
        self.assertEqual(refunded % 15000, 0)
        self.assertEqual(self.settled_refunds(), refunded // 15000)
        self.assertEqual(self.balance(self.payee), PAYEE_OPENING + ORIGINAL - refunded)
        self.assertEqual(LedgerEntry.objects(__raw__={"kind": "REFUND_IN"}).count(), refunded // 15000)

    def test_the_same_key_from_two_tabs_moves_the_money_once(self):
        outcomes = self.run_together(lambda: self.refund(key="refund-key-race-1"), lambda: self.refund(key="refund-key-race-1"))

        settled = self.settled_refunds()

        self.assertEqual(settled, 1)
        self.assertEqual(Refund.objects.count(), 1)
        self.assertEqual(Transfer.objects.count(), 2)
        self.assertEqual(LedgerEntry.objects(__raw__={"kind": "REFUND_OUT"}).count(), 1)
        self.assertEqual(self.balance(self.payee), PAYEE_OPENING)

    def test_a_refund_and_an_unrelated_transfer_at_once_both_land_correctly(self):
        def refund_half():
            return self.refund(amount_minor=ORIGINAL // 2)

        def send_half():
            return transfer_services.execute_transfer(
                self.account_document(self.sender), "grace.obi@wallet.demo", 1000, None, None
            )

        outcomes = self.run_together(refund_half, send_half)

        self.assertTrue(all(kind == "ok" for kind, _ in outcomes))
        self.assertEqual(self.balance(self.payee), PAYEE_OPENING + ORIGINAL // 2)
        self.assertEqual(self.balance(self.sender), SENDER_OPENING - ORIGINAL + ORIGINAL // 2 - 1000)


class InterruptionTest(RefundTestCase):
    def test_an_attempt_stopped_after_the_money_moved_leaves_the_refund_pending(self):
        with interrupted_before_the_refund_is_recorded():
            with self.assertRaises(RuntimeError):
                self.refund()

        stored = self.one_refund()

        self.assertEqual(stored["state"], "PENDING")
        self.assertIsNone(stored.get("transferId"))
        self.assertEqual(self.balance(self.payee), PAYEE_OPENING)  # the money did move
        self.assertEqual(self.stored_original()["refundedAmountMinor"], ORIGINAL)

    def test_reading_the_refunds_records_a_refund_that_had_already_paid(self):
        with interrupted_before_the_refund_is_recorded():
            with self.assertRaises(RuntimeError):
                self.refund()

        page = services.list_refunds(self.payee.id, 25, 0)

        self.assertEqual(page["refunds"][0]["state"], "COMPLETED")
        self.assertEqual(page["refunds"][0]["transferId"], str(Transfer.objects.order_by("-created_at").first().id))
        self.assertEqual(Transfer.objects.count(), 2)
        self.assertEqual(Refund.objects.count(), 1)

    def test_retrying_the_key_after_a_stop_finishes_the_same_refund(self):
        with interrupted_before_the_refund_is_recorded():
            with self.assertRaises(RuntimeError):
                self.refund(key="refund-key-stop-1")

        refund = self.one_refund()
        self.backdate_refund(refund["_id"], REFUND_CLAIM_GRACE_SECONDS + 5)

        data, created = self.refund(key="refund-key-stop-1")

        self.assertEqual(self.stored_refund(refund["_id"])["state"], "COMPLETED")
        self.assertEqual(data["refund"]["id"], str(refund["_id"]))
        self.assertFalse(created)
        self.assertEqual(Transfer.objects.count(), 2)
        self.assertEqual(self.balance(self.payee), PAYEE_OPENING)

    def test_an_attempt_stopped_before_the_engine_ran_leaves_a_pending_refund_with_its_claim(self):
        with mock.patch.object(transfer_services, "execute_transfer", side_effect=RuntimeError("gone")):
            with self.assertRaises(RuntimeError):
                self.refund()

        stored = self.one_refund()

        self.assertEqual(stored["state"], "PENDING")
        self.assertEqual(self.stored_original()["refundedAmountMinor"], ORIGINAL)
        self.assertEqual(self.balance(self.payee), PAYEE_OPENING + ORIGINAL)  # nothing moved

    def test_a_stuck_pending_refund_is_failed_and_its_claim_released_when_the_key_is_retried(self):
        with mock.patch.object(transfer_services, "execute_transfer", side_effect=RuntimeError("gone")):
            with self.assertRaises(RuntimeError):
                self.refund(key="refund-key-stuck-1")

        refund = self.one_refund()
        self.backdate_refund(refund["_id"], REFUND_CLAIM_GRACE_SECONDS + 5)

        with mock.patch.object(transfer_services, "execute_transfer", side_effect=AppError(409, "INSUFFICIENT_FUNDS", "This wallet does not have enough available balance for that amount.")):
            with self.assertRaises(AppError) as raised:
                self.refund(key="refund-key-stuck-1")

        self.assertEqual(raised.exception.code, "INSUFFICIENT_FUNDS")
        self.assertEqual(self.stored_refund(refund["_id"])["state"], "FAILED")
        self.assertEqual(self.stored_original()["refundedAmountMinor"], 0)

    def test_a_young_pending_refund_is_not_taken_over(self):
        with mock.patch.object(transfer_services, "execute_transfer", side_effect=RuntimeError("gone")):
            with self.assertRaises(RuntimeError):
                self.refund(key="refund-key-young-1")

        with self.assertRaises(AppError) as raised:
            self.refund(key="refund-key-young-1")

        self.assertEqual(raised.exception.code, "REFUND_IN_PROGRESS")
        self.assertEqual(self.stored_original()["refundedAmountMinor"], ORIGINAL)
