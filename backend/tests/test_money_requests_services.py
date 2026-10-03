"""R4 — asking another account for money.

The behaviour that matters here is that an approval is the transfer engine's work and nobody
else's. Every test that approves a request therefore checks what the engine did as well as
what the request says, because a request that says `APPROVED` while no money moved is the one
failure this feature cannot have.

The other tests that matter are the interruptions and the races: an approval that stops part
way through, an approval and a decline at the same moment, and six approvals at once.
"""

import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock

from bson import ObjectId

from apps.auth.models import UserAccount
from apps.money_requests import repository, services
from apps.money_requests.constants import APPROVAL_CLAIM_GRACE_SECONDS
from apps.money_requests.models import MoneyRequest
from apps.shared.documents import now_utc
from apps.shared.errors import AppError, ValidationError
from apps.shared.mongo import collection
from apps.transfers import repository as transfer_repository
from apps.transfers import services as transfer_services
from apps.transfers.constants import COMPLETED_STATE, MONEY_REQUEST_KEY_PREFIX, PENDING_STATE
from apps.transfers.models import Transfer
from apps.wallet import repository as wallet_repository
from apps.wallet.models import LedgerEntry

from .base import MongoTestCase

APPROVER_EMAIL = "aarav.mehta@wallet.demo"
REQUESTER_EMAIL = "fatima.noor@wallet.demo"
STRANGER_EMAIL = "grace.obi@wallet.demo"

APPROVER_OPENING = 200000
REQUESTER_OPENING = 500
STRANGER_OPENING = 700
OPENING_ENTRIES = 3

AMOUNT = 12000
NOTE = "Rent share for April"


@contextmanager
def interrupted_before_the_request_is_recorded():
    """Stop an approval after the engine has moved the money but before the request knows.

    This is the window the feature has to survive, so it is made to happen on purpose.
    """
    with mock.patch.object(repository, "settle_approved", side_effect=RuntimeError("interrupted before recording")):
        yield


@contextmanager
def refused_by_the_engine():
    """The engine turning an approval down, having moved nothing."""

    def refuse(*arguments, **keywords):
        raise AppError(409, "INSUFFICIENT_FUNDS", "Not enough balance to send that much.")

    with mock.patch.object(transfer_services, "execute_transfer", refuse):
        yield


class MoneyRequestTestCase(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.approver = self.create_account(
            name="Aarav Mehta", email=APPROVER_EMAIL, opening_balance_minor=APPROVER_OPENING
        )
        self.requester = self.create_account(
            name="Fatima Noor", email=REQUESTER_EMAIL, opening_balance_minor=REQUESTER_OPENING
        )
        self.stranger = self.create_account(name="Grace Obi", email=STRANGER_EMAIL, opening_balance_minor=STRANGER_OPENING)

    def ask(self, amount_minor=AMOUNT, note=NOTE, approver_email=APPROVER_EMAIL, requester=None):
        """Ask for money the way the route does, with the caller as the account asking."""
        return services.create_request(
            self.account_document(requester or self.requester), approver_email, amount_minor, note
        )

    def approve(self, request_id, approver=None):
        return services.approve_request(self.account_document(approver or self.approver), request_id)

    def decline(self, request_id, approver=None):
        return services.decline_request(self.account_document(approver or self.approver), request_id)

    def stored(self, request_id):
        return MoneyRequest.objects(__raw__={"_id": ObjectId(request_id)}).as_pymongo().first()

    def balance(self, account):
        return wallet_repository.find_wallet_by_user_id(account.id)["availableBalanceMinor"]

    def movements(self, account):
        return wallet_repository.find_wallet_by_user_id(account.id)["pendingMovements"]

    def legs(self, transfer_id):
        return LedgerEntry.objects(__raw__={"transactionId": ObjectId(transfer_id)}).count()

    def spend(self, request_id):
        """The key an approval of this request spends, which is derived from the request."""
        return f"{MONEY_REQUEST_KEY_PREFIX}{request_id}"

    def write_transfer_for_the_request(self, request_id, state):
        """Put a transfer behind a request's own key without going through an approval."""
        transfer = transfer_repository.create_transfer(
            {
                "sender_user_id": self.approver.id,
                "sender_wallet_id": wallet_repository.find_wallet_by_user_id(self.approver.id)["_id"],
                "recipient_user_id": self.requester.id,
                "recipient_wallet_id": wallet_repository.find_wallet_by_user_id(self.requester.id)["_id"],
                "amount_minor": AMOUNT,
                "state": state,
                "note": NOTE,
                "idempotency_key": self.spend(request_id),
                "idempotency_fingerprint": transfer_services.request_fingerprint(self.requester.id, AMOUNT, NOTE),
            }
        )

        return transfer["_id"]

    def claim_directly(self, request_id):
        """Take a request for approval without answering it, as a crashed approval would."""
        return repository.claim(ObjectId(request_id), datetime.now(timezone.utc), services.APPROVING_STATE)

    def backdate_claim(self, request_id):
        """Age a claim past the point where an approval is assumed to have gone."""
        moment = datetime.now(timezone.utc) - timedelta(seconds=APPROVAL_CLAIM_GRACE_SECONDS * 2)

        collection(MoneyRequest).update_one({"_id": ObjectId(request_id)}, {"$set": {"approvalStartedAt": moment}})

    def backdate_expiry(self, request_id, days=8):
        """Close a request's window, as the passing of a week would."""
        moment = datetime.now(timezone.utc) - timedelta(days=days)

        collection(MoneyRequest).update_one({"_id": ObjectId(request_id)}, {"$set": {"expiresAt": moment}})


class CreationTest(MoneyRequestTestCase):
    def test_a_request_is_written_pending_with_both_parties_named_by_id(self):
        request = self.ask()

        self.assertEqual(request["state"], "PENDING")
        self.assertEqual(request["role"], "REQUESTER")
        self.assertEqual(request["amountMinor"], AMOUNT)
        self.assertEqual(request["note"], NOTE)
        self.assertEqual(request["requesterUserId"], str(self.requester.id))
        self.assertEqual(request["approverUserId"], str(self.approver.id))
        self.assertIsNone(request["transferId"])
        self.assertIsNone(request["resolvedAt"])

    def test_a_request_names_two_accounts_and_stores_no_address_or_wallet(self):
        # Who a request is with is resolved when it is read, so a request cannot be made for
        # an address that later belongs to somebody else, and cannot outlive a wallet.
        self.ask()

        stored = self.stored(MoneyRequest.objects.first().id)

        self.assertEqual(stored["requesterUserId"], self.requester.id)
        self.assertEqual(stored["approverUserId"], self.approver.id)
        self.assertEqual([key for key in stored if "mail" in key.lower() or "allet" in key.lower()], [])

    def test_a_request_is_answerable_for_a_week(self):
        request = self.ask()
        window = request["expiresAt"] - request["createdAt"]

        # The window runs from the moment the request was written to the moment its deadline
        # was computed, which are not the same instant, so it is a week less than a sliver.
        self.assertGreater(window, timedelta(days=services.REQUEST_TTL_DAYS) - timedelta(seconds=1))
        self.assertLessEqual(window, timedelta(days=services.REQUEST_TTL_DAYS))

    def test_a_note_is_optional(self):
        request = self.ask(note=None)

        self.assertIsNone(request["note"])
        self.assertIsNone(self.stored(request["id"]).get("note"))

    def test_the_other_side_is_the_only_one_that_can_answer(self):
        request = self.ask()

        self.assertEqual(self.approve(request["id"])["request"]["role"], "APPROVER")

    def test_a_request_to_yourself_is_refused(self):
        with self.assertRaises(ValidationError) as raised:
            self.ask(approver_email=REQUESTER_EMAIL)

        self.assertEqual(raised.exception.details["fieldErrors"]["recipientEmail"], [services.SELF_TRANSFER_MESSAGE])
        self.assertEqual(MoneyRequest.objects.count(), 0)

    def test_a_request_to_somebody_unknown_is_refused(self):
        with self.assertRaises(AppError) as raised:
            self.ask(approver_email="nobody@wallet.demo")

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.code, "RECIPIENT_NOT_FOUND")
        self.assertEqual(MoneyRequest.objects.count(), 0)

    def test_a_request_to_somebody_without_a_wallet_is_refused(self):
        self.create_account(name="No Wallet", email="no.wallet@wallet.demo", provision=False)

        with self.assertRaises(AppError) as raised:
            self.ask(approver_email="no.wallet@wallet.demo")

        self.assertEqual(raised.exception.code, "RECIPIENT_NOT_FOUND")
        self.assertEqual(MoneyRequest.objects.count(), 0)

    def test_a_request_from_an_account_without_a_wallet_is_refused(self):
        walletless = self.create_account(name="No Wallet", email="no.wallet@wallet.demo", provision=False)

        with self.assertRaises(AppError) as raised:
            self.ask(requester=walletless)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.code, "WALLET_NOT_FOUND")
        self.assertEqual(MoneyRequest.objects.count(), 0)

    def test_two_requests_for_the_same_money_are_two_requests(self):
        # Asking twice is not an error: the second one is how somebody asks again when the
        # first was simply not answered.
        first = self.ask()
        second = self.ask()

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(MoneyRequest.objects.count(), 2)

    def test_asking_moves_no_money(self):
        self.ask()

        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(LedgerEntry.objects.count(), OPENING_ENTRIES)
        self.assertEqual(self.balance(self.approver), APPROVER_OPENING)


class ApprovalMovesTheMoneyTest(MoneyRequestTestCase):
    def test_approving_answers_approved_and_names_the_transfer_that_paid_it(self):
        request = self.ask()

        answer = self.approve(request["id"])

        self.assertEqual(answer["request"]["state"], "APPROVED")
        self.assertEqual(answer["request"]["transferId"], answer["transfer"]["id"])
        self.assertEqual(answer["idempotentReplay"], False)

        stored = self.stored(request["id"])

        self.assertEqual(stored["state"], "APPROVED")
        self.assertEqual(stored["transferId"], ObjectId(answer["transfer"]["id"]))
        self.assertIsNotNone(stored["resolvedAt"])
        self.assertNotIn("approvalStartedAt", stored)

    def test_approving_moves_the_money_the_way_a_transfer_does(self):
        request = self.ask()

        answer = self.approve(request["id"])

        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(self.legs(answer["transfer"]["id"]), 2)
        self.assertEqual(LedgerEntry.objects.count(), OPENING_ENTRIES + 2)
        self.assertEqual(self.balance(self.approver), APPROVER_OPENING - AMOUNT)
        self.assertEqual(self.balance(self.requester), REQUESTER_OPENING + AMOUNT)
        self.assertEqual(self.movements(self.approver), [])

    def test_the_transfer_is_the_engine_sending_to_the_requester(self):
        request = self.ask()

        answer = self.approve(request["id"])
        transfer = transfer_repository.find_by_key(self.approver.id, self.spend(request["id"]))

        self.assertEqual(transfer["_id"], ObjectId(answer["transfer"]["id"]))
        self.assertEqual(transfer["senderUserId"], self.approver.id)
        self.assertEqual(transfer["recipientUserId"], self.requester.id)
        self.assertEqual(transfer["amountMinor"], AMOUNT)
        self.assertEqual(transfer["note"], NOTE)
        self.assertEqual(transfer["state"], COMPLETED_STATE)

    def test_approving_answers_with_the_approvers_wallet_as_it_stands(self):
        request = self.ask()

        answer = self.approve(request["id"])

        self.assertEqual(answer["wallet"]["availableBalanceMinor"], APPROVER_OPENING - AMOUNT)
        self.assertEqual(answer["wallet"]["userId"], str(self.approver.id))

    def test_the_key_an_approval_spends_is_derived_from_the_request(self):
        request = self.ask()

        self.approve(request["id"])

        self.assertEqual(
            transfer_repository.find_by_key(self.approver.id, self.spend(request["id"]))["idempotencyKey"],
            self.spend(request["id"]),
        )

    def test_the_same_request_approved_twice_sends_the_money_once(self):
        request = self.ask()

        first = self.approve(request["id"])
        second = self.approve(request["id"])

        self.assertEqual(first["transfer"]["id"], second["transfer"]["id"])
        self.assertEqual(first["idempotentReplay"], False)
        self.assertEqual(second["idempotentReplay"], True)
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(self.balance(self.approver), APPROVER_OPENING - AMOUNT)

    def test_the_requester_is_paid_the_account_the_request_names_not_an_address_it_did(self):
        # The request holds an account, so an approval pays whoever that account is now, and
        # not an address that may since have been taken by somebody else.
        request = self.ask()
        collection(UserAccount).update_one({"_id": self.requester.id}, {"$set": {"email": "moved@wallet.demo"}})

        answer = self.approve(request["id"])

        self.assertEqual(answer["transfer"]["recipientUserId"], str(self.requester.id))
        self.assertEqual(self.balance(self.requester), REQUESTER_OPENING + AMOUNT)

    def test_a_requester_cannot_approve_their_own_request(self):
        request = self.ask()

        with self.assertRaises(AppError) as raised:
            services.approve_request(self.account_document(self.requester), request["id"])

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.code, "REQUEST_ACTION_NOT_ALLOWED")
        self.assertEqual(self.stored(request["id"])["state"], "PENDING")
        self.assertEqual(Transfer.objects.count(), 0)

    def test_nobody_else_can_approve_a_request_they_were_not_asked_to_answer(self):
        request = self.ask()

        with self.assertRaises(AppError) as raised:
            self.approve(request["id"], approver=self.stranger)

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_a_request_that_does_not_exist_is_refused(self):
        with self.assertRaises(AppError) as raised:
            self.approve(ObjectId())

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.code, "REQUEST_NOT_FOUND")

    def test_an_id_that_is_not_an_id_is_a_request_that_does_not_exist(self):
        with self.assertRaises(AppError) as raised:
            self.approve("not-an-object-id")

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.code, "REQUEST_NOT_FOUND")

    def test_a_requester_who_is_no_longer_active_cannot_be_paid(self):
        request = self.ask()
        collection(UserAccount).update_one({"_id": self.requester.id}, {"$set": {"active": False}})

        with self.assertRaises(AppError) as raised:
            self.approve(request["id"])

        self.assertEqual(raised.exception.code, "RECIPIENT_NOT_FOUND")
        self.assertEqual(self.balance(self.approver), APPROVER_OPENING)
        self.assertEqual(self.stored(request["id"])["state"], "PENDING")


class ApprovalRefusedTest(MoneyRequestTestCase):
    def test_an_approval_the_engine_refuses_leaves_the_request_answerable(self):
        request = self.ask()

        with refused_by_the_engine():
            with self.assertRaises(AppError) as raised:
                self.approve(request["id"])

        self.assertEqual(raised.exception.code, "INSUFFICIENT_FUNDS")

        stored = self.stored(request["id"])

        self.assertEqual(stored["state"], "PENDING")
        self.assertNotIn("approvalStartedAt", stored)
        self.assertIsNone(stored.get("transferId"))

    def test_a_refused_approval_changes_nothing_about_either_wallet(self):
        request = self.ask()

        with refused_by_the_engine():
            with self.assertRaises(AppError):
                self.approve(request["id"])

        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(LedgerEntry.objects.count(), OPENING_ENTRIES)
        self.assertEqual(self.balance(self.approver), APPROVER_OPENING)
        self.assertEqual(self.balance(self.requester), REQUESTER_OPENING)

    def test_a_refused_approval_leaves_the_key_free_so_the_request_can_be_paid_afterwards(self):
        request = self.ask()

        with refused_by_the_engine():
            with self.assertRaises(AppError):
                self.approve(request["id"])

        self.assertIsNone(transfer_repository.find_by_key(self.approver.id, self.spend(request["id"])))

        answer = self.approve(request["id"])

        self.assertEqual(answer["request"]["state"], "APPROVED")
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(self.balance(self.requester), REQUESTER_OPENING + AMOUNT)

    def test_a_request_the_approver_cannot_afford_is_left_answerable(self):
        request = self.ask(amount_minor=APPROVER_OPENING + 1)

        with self.assertRaises(AppError) as raised:
            self.approve(request["id"])

        self.assertEqual(raised.exception.code, "INSUFFICIENT_FUNDS")
        self.assertEqual(self.balance(self.approver), APPROVER_OPENING)
        self.assertEqual(self.balance(self.requester), REQUESTER_OPENING)
        self.assertEqual(self.stored(request["id"])["state"], "PENDING")

    def test_a_request_that_cannot_be_afforded_can_be_asked_for_again_at_a_price_that_works(self):
        unaffordable = self.ask(amount_minor=APPROVER_OPENING + 1)

        with self.assertRaises(AppError):
            self.approve(unaffordable["id"])

        affordable = self.ask(amount_minor=AMOUNT)

        self.assertEqual(self.approve(affordable["id"])["request"]["state"], "APPROVED")
        self.assertEqual(self.stored(unaffordable["id"])["state"], "PENDING")


class DeclineTest(MoneyRequestTestCase):
    def test_declining_answers_declined_and_moves_no_money(self):
        request = self.ask()

        answer = self.decline(request["id"])

        self.assertEqual(answer["state"], "DECLINED")
        self.assertIsNotNone(answer["resolvedAt"])
        self.assertIsNone(answer["transferId"])
        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(LedgerEntry.objects.count(), OPENING_ENTRIES)
        self.assertEqual(self.balance(self.approver), APPROVER_OPENING)

    def test_a_declined_request_is_never_paid_afterwards(self):
        request = self.ask()

        self.decline(request["id"])

        with self.assertRaises(AppError) as raised:
            self.approve(request["id"])

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "REQUEST_ALREADY_DECLINED")
        self.assertEqual(Transfer.objects.count(), 0)

    def test_a_declined_request_cannot_be_declined_again(self):
        request = self.ask()

        self.decline(request["id"])

        with self.assertRaises(AppError) as raised:
            self.decline(request["id"])

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "REQUEST_ALREADY_DECLINED")

    def test_a_requester_cannot_decline_their_own_request(self):
        request = self.ask()

        with self.assertRaises(AppError) as raised:
            services.decline_request(self.account_document(self.requester), request["id"])

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(self.stored(request["id"])["state"], "PENDING")

    def test_a_request_that_does_not_exist_cannot_be_declined(self):
        with self.assertRaises(AppError) as raised:
            self.decline(ObjectId())

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.code, "REQUEST_NOT_FOUND")


class TerminalStateTest(MoneyRequestTestCase):
    def test_an_approved_request_cannot_be_approved_again_with_money_moving(self):
        request = self.ask()

        self.approve(request["id"])
        before = self.balance(self.approver)

        self.approve(request["id"])

        self.assertEqual(self.balance(self.approver), before)
        self.assertEqual(Transfer.objects.count(), 1)

    def test_an_approved_request_cannot_be_declined(self):
        request = self.ask()

        self.approve(request["id"])

        with self.assertRaises(AppError) as raised:
            self.decline(request["id"])

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "REQUEST_ALREADY_APPROVED")
        self.assertEqual(self.stored(request["id"])["state"], "APPROVED")

    def test_a_terminal_state_is_never_rewritten(self):
        request = self.ask()

        self.approve(request["id"])
        first = self.stored(request["id"])

        self.approve(request["id"])

        with self.assertRaises(AppError):
            self.decline(request["id"])

        second = self.stored(request["id"])

        self.assertEqual(second["state"], "APPROVED")
        self.assertEqual(second["transferId"], first["transferId"])
        self.assertEqual(second["resolvedAt"], first["resolvedAt"])

    def test_the_write_that_records_a_payment_will_not_re_point_an_answered_request(self):
        """Pin the condition on `settle_approved` itself, which no caller can violate today.

        Every route into that write checks the request's state first, so as the code stands
        no caller can hand it a request that has already been answered — the tests above
        pass whether or not the write is filtered. The filter is the reason that is true
        rather than a coincidence of the call sites, so it is asserted here where it lives:
        this is the write that points a request at money that has moved, and it must never be
        able to aim an answered request at a second transfer.
        """
        request = self.ask()

        self.approve(request["id"])
        answered = self.stored(request["id"])

        recorded = repository.settle_approved(request["id"], ObjectId(), now_utc())
        after = self.stored(request["id"])

        self.assertIsNone(recorded)
        self.assertEqual(after["state"], "APPROVED")
        self.assertEqual(after["transferId"], answered["transferId"])
        self.assertEqual(after["resolvedAt"], answered["resolvedAt"])

    def test_the_write_that_records_a_payment_will_not_approve_a_declined_request(self):
        """The same guarantee for the one terminal state with nothing to preserve but honesty.

        A declined request holds no transfer. Letting this write reach it would invent one,
        which is the single most misleading thing this feature could do: a row that says a
        declined request was paid.
        """
        request = self.ask()

        self.decline(request["id"])
        declined_at = self.stored(request["id"])["resolvedAt"]

        recorded = repository.settle_approved(request["id"], ObjectId(), now_utc())
        after = self.stored(request["id"])

        self.assertIsNone(recorded)
        self.assertEqual(after["state"], "DECLINED")
        # Absent rather than null, which is how a request that was never paid is stored.
        self.assertIsNone(after.get("transferId"))
        self.assertEqual(after["resolvedAt"], declined_at)


class ExpiryTest(MoneyRequestTestCase):
    def test_a_request_past_its_window_is_expired_when_it_is_listed(self):
        request = self.ask()
        self.backdate_expiry(request["id"])

        page = services.list_requests(self.approver.id, None, None, 25, 0)

        self.assertEqual(page["requests"][0]["state"], "EXPIRED")
        self.assertIsNotNone(self.stored(request["id"])["resolvedAt"])

    def test_an_expired_request_cannot_be_approved(self):
        request = self.ask()
        self.backdate_expiry(request["id"])

        with self.assertRaises(AppError) as raised:
            self.approve(request["id"])

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "REQUEST_EXPIRED")
        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(self.balance(self.approver), APPROVER_OPENING)
        self.assertEqual(self.stored(request["id"])["state"], "EXPIRED")

    def test_an_expired_request_cannot_be_declined(self):
        request = self.ask()
        self.backdate_expiry(request["id"])

        with self.assertRaises(AppError) as raised:
            self.decline(request["id"])

        self.assertEqual(raised.exception.code, "REQUEST_EXPIRED")
        self.assertEqual(self.stored(request["id"])["state"], "EXPIRED")

    def test_reading_one_accounts_requests_never_expires_somebody_elses(self):
        request = self.ask()
        self.backdate_expiry(request["id"])

        # The stranger is a party to no request at all, so their read must not reach across.
        services.list_requests(self.stranger.id, None, None, 25, 0)

        self.assertEqual(self.stored(request["id"])["state"], "PENDING")

    def test_a_request_inside_its_window_is_not_expired(self):
        request = self.ask()

        page = services.list_requests(self.approver.id, None, None, 25, 0)

        self.assertEqual(page["requests"][0]["state"], "PENDING")
        self.assertIsNone(self.stored(request["id"]).get("resolvedAt"))

    def test_expiry_needs_no_scheduled_work_to_be_correct(self):
        # Nothing expires a request on a timer, so whether a request can be answered is
        # settled by the window stored on it rather than by whether anybody has looked.
        request = self.ask()
        self.backdate_expiry(request["id"])

        with self.assertRaises(AppError) as raised:
            self.approve(request["id"])

        self.assertEqual(raised.exception.code, "REQUEST_EXPIRED")
        self.assertEqual(self.stored(request["id"])["state"], "EXPIRED")


class ListingTest(MoneyRequestTestCase):
    def setUp(self):
        super().setUp()

        # Three requests are addressed to the approver, one of them by somebody else, and two
        # more are asked for by the requester, so both sides of every filter have something.
        self.asked_of_me = self.ask()
        self.declined = self.ask()
        self.decline(self.declined["id"])
        self.from_a_stranger = services.create_request(
            self.account_document(self.stranger), APPROVER_EMAIL, 4000, "Borrowing for a bike"
        )
        self.asked_by_me = services.create_request(
            self.account_document(self.requester), STRANGER_EMAIL, AMOUNT + 1, "From me to you"
        )
        self.asked_of_you = services.create_request(
            self.account_document(self.stranger), REQUESTER_EMAIL, 550, "Splitting the bill"
        )
        self.outsider = self.create_account(name="Nobody", email="nobody@wallet.demo")

    def page(self, account=None, role=None, state=None, limit=25, offset=0):
        return services.list_requests((account or self.approver).id, role, state, limit, offset)

    def test_an_account_sees_the_requests_addressed_to_it(self):
        page = self.page()

        self.assertEqual(page["total"], 3)
        self.assertEqual(
            {request["id"] for request in page["requests"]},
            {self.asked_of_me["id"], self.declined["id"], self.from_a_stranger["id"]},
        )
        self.assertTrue(all(request["role"] == "APPROVER" for request in page["requests"]))

    def test_an_account_sees_the_requests_it_asked_for_too(self):
        page = self.page(account=self.requester)

        self.assertEqual(page["total"], 4)
        self.assertEqual(
            {request["id"] for request in page["requests"]},
            {self.asked_of_me["id"], self.declined["id"], self.asked_by_me["id"], self.asked_of_you["id"]},
        )
        self.assertEqual([request["role"] for request in page["requests"]].count("REQUESTER"), 3)
        self.assertEqual([request["role"] for request in page["requests"]].count("APPROVER"), 1)

    def test_a_stranger_sees_nothing(self):
        self.assertEqual(self.page(account=self.outsider)["total"], 0)

    def test_the_other_side_is_named_on_every_request(self):
        page = self.page()

        self.assertEqual([request["counterparty"]["name"] for request in page["requests"]].count("Fatima Noor"), 2)
        self.assertEqual([request["counterparty"]["name"] for request in page["requests"]].count("Grace Obi"), 1)

        for request in page["requests"]:
            self.assertEqual(request["counterparty"]["role"], "REQUESTER")
            self.assertNotEqual(request["counterparty"]["userId"], str(self.approver.id))

    def test_the_other_side_of_a_request_you_asked_for_is_named_too(self):
        asked = self.page(account=self.requester, role="REQUESTER")["requests"][0]

        self.assertEqual(asked["counterparty"]["name"], "Grace Obi")
        self.assertEqual(asked["counterparty"]["role"], "APPROVER")

    def test_a_side_can_be_asked_for(self):
        as_requester = self.page(account=self.requester, role="REQUESTER")
        as_approver = self.page(account=self.requester, role="APPROVER")

        self.assertEqual(as_requester["total"], 3)
        self.assertTrue(all(request["role"] == "REQUESTER" for request in as_requester["requests"]))
        self.assertEqual(as_approver["total"], 1)
        self.assertEqual(as_approver["requests"][0]["id"], self.asked_of_you["id"])

    def test_a_state_can_be_asked_for(self):
        declined = self.page(state="DECLINED")

        self.assertEqual(declined["total"], 1)
        self.assertEqual(declined["requests"][0]["id"], self.declined["id"])
        self.assertEqual(self.page(state="PENDING")["total"], 2)
        self.assertEqual(self.page(state="APPROVED")["total"], 0)

    def test_a_side_and_a_state_can_be_asked_for_together(self):
        page = self.page(account=self.requester, role="APPROVER", state="PENDING")

        self.assertEqual(page["total"], 1)
        self.assertEqual(page["requests"][0]["id"], self.asked_of_you["id"])
        self.assertEqual(self.page(account=self.requester, role="APPROVER", state="DECLINED")["total"], 0)

    def test_the_page_is_newest_first_and_reports_the_total_beside_the_page(self):
        newest = self.ask()
        page = self.page(limit=2)

        self.assertEqual(page["total"], 4)
        self.assertEqual(page["limit"], 2)
        self.assertEqual(page["offset"], 0)
        self.assertEqual(page["requests"][0]["id"], newest["id"])

    def test_the_page_can_be_walked_with_an_offset(self):
        first = self.page(limit=2)
        second = self.page(limit=2, offset=2)

        self.assertEqual(len(first["requests"]), 2)
        self.assertEqual(len(second["requests"]), 1)
        self.assertFalse(
            {request["id"] for request in first["requests"]} & {request["id"] for request in second["requests"]}
        )
        self.assertEqual(second["total"], 3)
        self.assertEqual(second["offset"], 2)

    def test_an_account_with_no_requests_is_an_empty_page(self):
        self.assertEqual(self.page(account=self.outsider), {"requests": [], "total": 0, "limit": 25, "offset": 0})

    def test_a_request_hands_out_only_what_the_viewer_may_see(self):
        self.assertEqual(
            set(self.page()["requests"][0]),
            {
                "id",
                "state",
                "role",
                "requesterUserId",
                "approverUserId",
                "amountMinor",
                "note",
                "createdAt",
                "updatedAt",
                "expiresAt",
                "resolvedAt",
                "transferId",
                "counterparty",
            },
        )


class InterruptionTest(MoneyRequestTestCase):
    def test_an_approval_stopped_after_the_money_moved_leaves_the_request_claiming(self):
        request = self.ask()

        with interrupted_before_the_request_is_recorded():
            with self.assertRaises(RuntimeError):
                self.approve(request["id"])

        stored = self.stored(request["id"])

        self.assertEqual(stored["state"], "APPROVING")
        self.assertIsNone(stored.get("transferId"))
        self.assertEqual(self.balance(self.requester), REQUESTER_OPENING + AMOUNT)

    def test_reading_the_requests_records_an_approval_that_had_already_paid(self):
        request = self.ask()

        with interrupted_before_the_request_is_recorded():
            with self.assertRaises(RuntimeError):
                self.approve(request["id"])

        page = services.list_requests(self.approver.id, None, None, 25, 0)

        self.assertEqual(page["requests"][0]["state"], "APPROVED")
        self.assertEqual(page["requests"][0]["transferId"], str(Transfer.objects.first().id))
        self.assertEqual(self.stored(request["id"])["state"], "APPROVED")
        self.assertEqual(Transfer.objects.count(), 1)

    def test_the_requester_is_told_too_when_an_approval_is_recorded(self):
        request = self.ask()

        with interrupted_before_the_request_is_recorded():
            with self.assertRaises(RuntimeError):
                self.approve(request["id"])

        page = services.list_requests(self.requester.id, None, None, 25, 0)

        self.assertEqual(page["requests"][0]["state"], "APPROVED")
        self.assertEqual(page["requests"][0]["role"], "REQUESTER")

    def test_approving_a_request_whose_approval_had_already_paid_sends_nothing_again(self):
        request = self.ask()

        with interrupted_before_the_request_is_recorded():
            with self.assertRaises(RuntimeError):
                self.approve(request["id"])

        answer = self.approve(request["id"])

        self.assertEqual(answer["request"]["state"], "APPROVED")
        self.assertEqual(answer["idempotentReplay"], True)
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(self.balance(self.requester), REQUESTER_OPENING + AMOUNT)

    def test_reading_does_not_record_an_approval_that_has_not_happened(self):
        request = self.ask()
        self.claim_directly(request["id"])

        page = services.list_requests(self.approver.id, None, None, 25, 0)

        self.assertEqual(page["requests"][0]["state"], "APPROVING")
        self.assertIsNone(page["requests"][0]["transferId"])

    def test_reading_will_not_settle_a_request_from_a_transfer_someone_else_sent(self):
        """Pin the sender the read path insists on, which the reserved prefix makes redundant.

        The key an approval spends is derived from the request and refused from an incoming
        `Idempotency-Key`, so no transfer can hold that key unless the approval itself put it
        there — which means the sender check the read path makes is, on today's code,
        unreachable. It is kept and tested anyway, because it is the layer that would still
        hold if the reservation were ever weakened: a completed transfer carrying this
        request's exact key, sent by a different account, must not be able to mark somebody
        else's request paid.
        """
        request = self.ask()
        self.claim_directly(request["id"])

        # A real, completed transfer, from a different account, spending the approval's own key.
        squatter, _ = transfer_services.execute_transfer(
            self.account_document(self.stranger),
            REQUESTER_EMAIL,
            500,
            None,
            idempotency_key=services.approval_key(request["id"]),
        )

        page = services.list_requests(self.approver.id, None, None, 25, 0)
        stored = self.stored(request["id"])

        self.assertEqual(page["requests"][0]["state"], "APPROVING")
        self.assertIsNone(page["requests"][0]["transferId"])
        self.assertEqual(stored["state"], "APPROVING")
        self.assertIsNone(stored.get("transferId"))
        squatted = Transfer.objects(__raw__={"_id": ObjectId(squatter["transfer"]["id"])}).first()

        self.assertEqual(squatted["state"], COMPLETED_STATE)

    def test_an_approval_stopped_before_the_engine_ran_leaves_a_claim_nobody_is_working_on(self):
        request = self.ask()

        with mock.patch.object(transfer_services, "execute_transfer", side_effect=RuntimeError("gone")):
            with self.assertRaises(RuntimeError):
                self.approve(request["id"])

        stored = self.stored(request["id"])

        self.assertEqual(stored["state"], "APPROVING")
        self.assertIsNotNone(stored["approvalStartedAt"])
        self.assertEqual(self.balance(self.requester), REQUESTER_OPENING)

    def test_a_request_being_approved_cannot_be_declined(self):
        request = self.ask()
        self.claim_directly(request["id"])

        with self.assertRaises(AppError) as raised:
            self.decline(request["id"])

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "REQUEST_APPROVAL_IN_PROGRESS")
        self.assertEqual(self.stored(request["id"])["state"], "APPROVING")

    def test_a_claim_that_is_still_being_worked_on_is_reported_as_such(self):
        request = self.ask()
        self.claim_directly(request["id"])

        with self.assertRaises(AppError) as raised:
            self.approve(request["id"])

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "REQUEST_APPROVAL_IN_PROGRESS")
        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(self.stored(request["id"])["state"], "APPROVING")

    def test_an_approval_taken_over_after_its_claim_was_abandoned_pays_the_request(self):
        request = self.ask()
        self.claim_directly(request["id"])
        self.backdate_claim(request["id"])

        answer = self.approve(request["id"])

        self.assertEqual(answer["request"]["state"], "APPROVED")
        self.assertEqual(answer["idempotentReplay"], False)
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(self.balance(self.requester), REQUESTER_OPENING + AMOUNT)

    def test_taking_over_a_stale_claim_does_not_reopen_the_request(self):
        # Taking over keeps the request claimed rather than handing it back, so there is no
        # window in which a request that has been paid could be answered a second time.
        request = self.ask()
        self.claim_directly(request["id"])
        self.backdate_claim(request["id"])

        self.approve(request["id"])

        with self.assertRaises(AppError) as raised:
            self.decline(request["id"])

        self.assertEqual(raised.exception.code, "REQUEST_ALREADY_APPROVED")
        self.assertEqual(Transfer.objects.count(), 1)

    def test_an_approval_whose_transfer_had_already_settled_is_answered_as_a_replay(self):
        request = self.ask()
        self.claim_directly(request["id"])
        self.backdate_claim(request["id"])
        transfer_id = self.write_transfer_for_the_request(request["id"], COMPLETED_STATE)

        answer = self.approve(request["id"])

        self.assertEqual(answer["request"]["state"], "APPROVED")
        self.assertEqual(answer["request"]["transferId"], str(transfer_id))
        self.assertEqual(answer["idempotentReplay"], True)
        self.assertEqual(self.balance(self.requester), REQUESTER_OPENING)

    def test_an_approval_whose_transfer_has_not_settled_is_reported_as_still_working(self):
        request = self.ask()
        self.claim_directly(request["id"])
        self.backdate_claim(request["id"])
        self.write_transfer_for_the_request(request["id"], PENDING_STATE)

        with self.assertRaises(AppError) as raised:
            self.approve(request["id"])

        self.assertEqual(raised.exception.code, "REQUEST_APPROVAL_IN_PROGRESS")
        # A transfer the engine has already written is left exactly as it is, because only the
        # engine's own recovery is allowed to finish one.
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(Transfer.objects.first().state, PENDING_STATE)
        self.assertEqual(self.stored(request["id"])["state"], "APPROVING")


class RacingActionsTest(MoneyRequestTestCase):
    def run_together(self, *calls):
        """Start every call at the same moment and collect what each one produced."""
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

    def assert_the_money_moved_at_most_once(self, amount_minor=AMOUNT):
        transfers = Transfer.objects.count()

        self.assertLessEqual(transfers, 1)
        self.assertEqual(LedgerEntry.objects.count(), OPENING_ENTRIES + 2 * transfers)
        self.assertEqual(self.balance(self.approver), APPROVER_OPENING - amount_minor * transfers)
        self.assertEqual(self.balance(self.requester), REQUESTER_OPENING + amount_minor * transfers)
        self.assertEqual(self.movements(self.approver), [])

    def test_two_approvals_at_the_same_moment_send_the_money_once(self):
        request = self.ask()

        self.run_together(lambda: self.approve(request["id"]), lambda: self.approve(request["id"]))

        self.assertEqual(self.stored(request["id"])["state"], "APPROVED")
        self.assert_the_money_moved_at_most_once()

    def test_six_approvals_at_the_same_moment_send_the_money_once(self):
        request = self.ask()

        self.run_together(*[lambda: self.approve(request["id"]) for _ in range(6)])

        self.assertEqual(self.stored(request["id"])["state"], "APPROVED")
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(LedgerEntry.objects.count(), OPENING_ENTRIES + 2)

    def test_an_approval_and_a_decline_at_the_same_moment_leave_one_answer(self):
        request = self.ask()

        self.run_together(lambda: self.approve(request["id"]), lambda: self.decline(request["id"]))

        state = self.stored(request["id"])["state"]

        self.assertIn(state, ["APPROVED", "DECLINED"])
        self.assert_the_money_moved_at_most_once()

        if state == "APPROVED":
            self.assertIsNotNone(self.stored(request["id"])["transferId"])
        else:
            self.assertIsNone(self.stored(request["id"]).get("transferId"))

    def test_two_declines_at_the_same_moment_decline_it_once(self):
        request = self.ask()

        self.run_together(lambda: self.decline(request["id"]), lambda: self.decline(request["id"]))

        self.assertEqual(self.stored(request["id"])["state"], "DECLINED")
        self.assertEqual(Transfer.objects.count(), 0)

    def test_a_request_past_its_window_is_never_approved_however_many_approve_at_once(self):
        request = self.ask()
        self.backdate_expiry(request["id"])

        self.run_together(*[lambda: self.approve(request["id"]) for _ in range(3)])

        self.assertEqual(self.stored(request["id"])["state"], "EXPIRED")
        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(self.balance(self.requester), REQUESTER_OPENING)
