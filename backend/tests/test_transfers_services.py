"""R2 — the behaviour of a P2P transfer.

The tests that matter most here are the interruption ones. Each one stops a transfer
between two of its writes, then asserts that picking it up later leaves both wallets
agreeing with their own ledgers and the system holding the same total money.
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
from apps.transfers.constants import RECOVERY_AGE_SECONDS
from apps.transfers.models import Transfer
from apps.wallet import repository as wallet_repository
from apps.wallet.models import LedgerEntry, Wallet

from .base import MongoTestCase

SENDER_EMAIL = "aarav.mehta@wallet.demo"
RECIPIENT_EMAIL = "fatima.noor@wallet.demo"
THIRD_PARTY_EMAIL = "diego.santos@wallet.demo"


@contextmanager
def interrupted_at(call_number):
    """Stop a transfer at its `call_number` ledger entry, as a crash between writes would."""
    original = transfer_services.record_leg
    seen = []

    def replacement(transfer, role, movement):
        seen.append(role)

        if len(seen) == call_number:
            raise RuntimeError("interrupted between writes")

        return original(transfer, role, movement)

    with mock.patch.object(transfer_services, "record_leg", replacement):
        yield


@contextmanager
def interrupted_before_completion():
    """Stop a transfer after both ledger entries exist but before it is marked complete."""
    with mock.patch.object(
        transfer_repository, "mark_completed", side_effect=RuntimeError("interrupted before completion")
    ):
        yield


class TransferTestCase(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.sender = self.create_account(name="Aarav Mehta", email=SENDER_EMAIL, opening_balance_minor=1000)
        self.recipient = self.create_account(name="Fatima Noor", email=RECIPIENT_EMAIL, opening_balance_minor=0)
        self.third_party = self.create_account(
            name="Diego Santos", email=THIRD_PARTY_EMAIL, opening_balance_minor=500
        )

    def send(self, amount_minor, recipient_email=RECIPIENT_EMAIL, note=None):
        # These tests are about what a transfer does, so the outcome flag the view needs is
        # dropped here and the response is returned on its own.
        response, _ = transfer_services.execute_transfer(
            self.account_document(self.sender), recipient_email, amount_minor, note
        )

        return response

    def balance(self, account):
        return wallet_repository.find_wallet_by_user_id(account.id)["availableBalanceMinor"]

    def wallet_id(self, account):
        return wallet_repository.find_wallet_by_user_id(account.id)["_id"]

    def entries(self, account):
        return wallet_repository.list_entries(self.wallet_id(account), 10, 0)

    def transfer_entries(self, account):
        """The entries transfers produced, ignoring the opening credit."""
        return [entry for entry in self.entries(account) if entry["kind"].startswith("TRANSFER")]

    def legs_of(self, transfer_id):
        return list(LedgerEntry.objects(__raw__={"transactionId": transfer_id}).as_pymongo())

    def system_total(self):
        return sum(wallet["availableBalanceMinor"] for wallet in Wallet.objects.as_pymongo())

    def only_transfer(self):
        return Transfer.objects.as_pymongo()[0]

    def wallet_document(self, account):
        return Wallet.objects(__raw__={"_id": self.wallet_id(account)}).as_pymongo().first()

    @property
    def opening_entry_count(self):
        """Only the funded wallets have an opening credit, so there are three entries."""
        return sum(entry["kind"] == "OPENING" for entry in self.entries(self.sender)) + sum(
            entry["kind"] == "OPENING" for entry in self.entries(self.recipient)
        ) + sum(entry["kind"] == "OPENING" for entry in self.entries(self.third_party))

    def assert_every_ledger_agrees_with_its_balance(self):
        for account in (self.sender, self.recipient, self.third_party):
            with self.subTest(account=account.email):
                wallet_id = self.wallet_id(account)

                self.assertEqual(wallet_repository.derived_balance(wallet_id), self.balance(account))

    def assert_nothing_is_left_in_flight(self):
        self.assertEqual(Transfer.objects(__raw__={"state": "PENDING"}).count(), 0)

        for account in (self.sender, self.recipient, self.third_party):
            with self.subTest(account=account.email):
                self.assertEqual(self.wallet_document(account)["pendingMovements"], [])

    def abandon(self, transfer_id):
        """Age a transfer past the point at which recovery treats it as forgotten."""
        moment = datetime.now(timezone.utc) - timedelta(seconds=RECOVERY_AGE_SECONDS + 5)
        collection(Transfer).update_one({"_id": transfer_id}, {"$set": {"createdAt": moment}})

        return transfer_id


class SuccessfulTransferTest(TransferTestCase):
    def test_a_transfer_moves_the_amount_from_sender_to_recipient(self):
        result = self.send(400)

        self.assertEqual(result["transfer"]["state"], "COMPLETED")
        self.assertEqual(result["transfer"]["amountMinor"], 400)
        self.assertEqual(result["transfer"]["role"], "SENDER")
        self.assertEqual(result["transfer"]["recipientUserId"], str(self.recipient.id))
        self.assertEqual(self.balance(self.sender), 600)
        self.assertEqual(self.balance(self.recipient), 400)
        self.assertEqual(result["wallet"]["availableBalanceMinor"], 600)

    def test_a_transfer_moves_money_without_changing_the_total_in_the_system(self):
        before = self.system_total()

        self.send(400)

        self.assertEqual(self.system_total(), before)

    def test_a_transfer_records_one_ledger_entry_on_each_wallet(self):
        result = self.send(400)
        transaction_id = ObjectId(result["transfer"]["transactionId"])

        sent = self.transfer_entries(self.sender)
        received = self.transfer_entries(self.recipient)

        self.assertEqual([entry["kind"] for entry in sent], ["TRANSFER_OUT"])
        self.assertEqual([entry["direction"] for entry in sent], ["DEBIT"])
        self.assertEqual([entry["kind"] for entry in received], ["TRANSFER_IN"])
        self.assertEqual([entry["direction"] for entry in received], ["CREDIT"])

        for entry in sent + received:
            self.assertEqual(entry["transactionId"], transaction_id)
            self.assertEqual(entry["amountMinor"], 400)

    def test_each_ledger_entry_records_the_balance_its_own_wallet_had_afterwards(self):
        self.send(400)

        self.assertEqual([entry["balanceAfterMinor"] for entry in self.transfer_entries(self.sender)], [600])
        self.assertEqual([entry["balanceAfterMinor"] for entry in self.transfer_entries(self.recipient)], [400])

    def test_each_ledger_entry_belongs_to_the_owner_of_its_own_wallet(self):
        self.send(400)

        self.assertEqual([entry["userId"] for entry in self.transfer_entries(self.sender)], [self.sender.id])
        self.assertEqual([entry["userId"] for entry in self.transfer_entries(self.recipient)], [self.recipient.id])

    def test_the_opening_entry_is_preserved_alongside_the_transfer(self):
        self.send(400)

        self.assertEqual([entry["kind"] for entry in self.entries(self.sender)], ["TRANSFER_OUT", "OPENING"])

    def test_both_ledgers_agree_with_their_balances_after_a_transfer(self):
        self.send(400)

        self.assert_every_ledger_agrees_with_its_balance()

    def test_a_transfer_records_its_note_and_leaves_no_unfinished_movement(self):
        result = self.send(400, note="Dinner split")
        transfer_id = ObjectId(result["transfer"]["id"])

        self.assertEqual(result["transfer"]["note"], "Dinner split")
        self.assertIsNone(wallet_repository.find_movement(self.wallet_id(self.sender), transfer_id))
        self.assertIsNone(wallet_repository.find_movement(self.wallet_id(self.recipient), transfer_id))

    def test_the_history_of_each_party_names_the_other(self):
        self.send(400)

        sent = transfer_services.decorate_entries(
            [dict(entry) for entry in self.entries(self.sender)], self.sender.id
        )
        received = transfer_services.decorate_entries(
            [dict(entry) for entry in self.entries(self.recipient)], self.recipient.id
        )

        self.assertEqual(sent[0]["counterparty"]["name"], "Fatima Noor")
        self.assertEqual(sent[0]["counterparty"]["role"], "SENDER")
        self.assertEqual(received[0]["counterparty"]["name"], "Aarav Mehta")
        self.assertEqual(received[0]["counterparty"]["role"], "RECIPIENT")

    def test_opening_entries_are_left_without_a_counterparty(self):
        page = transfer_services.decorate_entries(
            [dict(entry) for entry in self.entries(self.third_party)], self.third_party.id
        )

        self.assertEqual([entry["kind"] for entry in page], ["OPENING"])
        self.assertNotIn("counterparty", page[0])

    def test_a_recipient_with_no_history_of_its_own_still_receives_correctly(self):
        self.send(250)

        self.assertEqual(self.balance(self.recipient), 250)
        self.assertEqual(wallet_repository.derived_balance(self.wallet_id(self.recipient)), 250)

    def test_the_same_wallet_can_send_to_several_recipients(self):
        self.send(300)
        self.send(200, recipient_email=THIRD_PARTY_EMAIL)

        self.assertEqual(self.balance(self.sender), 500)
        self.assertEqual(self.balance(self.recipient), 300)
        self.assertEqual(self.balance(self.third_party), 700)
        self.assertEqual([entry["balanceAfterMinor"] for entry in self.transfer_entries(self.sender)], [500, 700])
        self.assert_every_ledger_agrees_with_its_balance()

    def test_a_wallet_can_receive_and_then_send_the_money_on(self):
        self.send(600)

        transfer_services.execute_transfer(
            self.account_document(self.recipient), THIRD_PARTY_EMAIL, 600, None
        )

        self.assertEqual(self.balance(self.recipient), 0)
        self.assertEqual(self.balance(self.third_party), 1100)
        self.assert_every_ledger_agrees_with_its_balance()


class RejectedTransferTest(TransferTestCase):
    def test_a_sender_cannot_spend_money_it_does_not_have(self):
        with self.assertRaises(AppError) as raised:
            self.send(1001)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "INSUFFICIENT_FUNDS")
        self.assertEqual(self.balance(self.sender), 1000)
        self.assertEqual(self.balance(self.recipient), 0)
        self.assertEqual(LedgerEntry.objects.count(), self.opening_entry_count)

    def test_a_rejected_transfer_is_recorded_as_failed_and_moves_nothing(self):
        with self.assertRaises(AppError):
            self.send(5000)

        transfer = self.only_transfer()

        self.assertEqual(transfer["state"], "FAILED")
        self.assertEqual(transfer["failureCode"], "INSUFFICIENT_FUNDS")
        self.assertEqual(self.legs_of(transfer["_id"]), [])

    def test_spending_the_whole_balance_is_allowed(self):
        self.send(1000)

        self.assertEqual(self.balance(self.sender), 0)
        self.assertEqual(self.balance(self.recipient), 1000)

    def test_an_account_without_a_wallet_cannot_send(self):
        broke = self.create_account(email="grace.obi@wallet.demo", provision=False)

        with self.assertRaises(AppError) as raised:
            transfer_services.execute_transfer(self.account_document(broke), RECIPIENT_EMAIL, 100, None)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.code, "WALLET_NOT_FOUND")
        self.assertEqual(Transfer.objects.count(), 0)

    def test_an_unknown_recipient_is_refused_before_anything_is_written(self):
        with self.assertRaises(AppError) as raised:
            self.send(100, recipient_email="nobody@wallet.demo")

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.code, "RECIPIENT_NOT_FOUND")
        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(self.balance(self.sender), 1000)

    def test_a_recipient_without_a_wallet_is_refused(self):
        walletless = self.create_account(email="grace.obi@wallet.demo", provision=False)

        with self.assertRaises(AppError) as raised:
            self.send(100, recipient_email=walletless.email)

        self.assertEqual(raised.exception.code, "RECIPIENT_NOT_FOUND")
        self.assertEqual(Transfer.objects.count(), 0)

    def test_a_deactivated_recipient_is_refused(self):
        self.recipient.active = False
        self.recipient.save()

        with self.assertRaises(AppError) as raised:
            self.send(100)

        self.assertEqual(raised.exception.code, "RECIPIENT_NOT_FOUND")
        self.assertEqual(Transfer.objects.count(), 0)

    def test_sending_to_yourself_is_refused(self):
        with self.assertRaises(AppError) as raised:
            self.send(100, recipient_email=SENDER_EMAIL)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.code, "VALIDATION_ERROR")
        self.assertIn("recipientEmail", raised.exception.details["fieldErrors"])
        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(self.balance(self.sender), 1000)

    def test_a_rejected_transfer_does_not_stop_the_next_one(self):
        with self.assertRaises(AppError):
            self.send(5000)

        self.send(100)

        self.assertEqual(self.balance(self.sender), 900)
        self.assertEqual(self.balance(self.recipient), 100)


class ConcurrentTransferTest(TransferTestCase):
    def run_together(self, *calls):
        """Start every call at the same moment and collect what each one produced."""
        outcomes = [None] * len(calls)
        gate = threading.Barrier(len(calls))

        def worker(index, call):
            gate.wait()

            try:
                outcomes[index] = ("ok", call())
            except AppError as error:
                outcomes[index] = ("refused", error)

        threads = [threading.Thread(target=worker, args=(index, call)) for index, call in enumerate(calls)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        return outcomes

    def test_two_transfers_that_cannot_both_be_afforded_leave_the_wallet_solvent(self):
        outcomes = self.run_together(lambda: self.send(600), lambda: self.send(600))

        succeeded = [value for kind, value in outcomes if kind == "ok"]
        refused = [value for kind, value in outcomes if kind == "refused"]

        self.assertEqual(len(succeeded), 1)
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0].code, "INSUFFICIENT_FUNDS")
        self.assertEqual(self.balance(self.sender), 400)
        self.assertEqual(self.balance(self.recipient), 600)
        self.assertEqual(len(self.transfer_entries(self.sender)), 1)

    def test_concurrent_transfers_that_both_fit_are_both_recorded_correctly(self):
        outcomes = self.run_together(lambda: self.send(400), lambda: self.send(400))

        self.assertEqual([kind for kind, _ in outcomes], ["ok", "ok"])
        self.assertEqual(self.balance(self.sender), 200)
        self.assertEqual(self.balance(self.recipient), 800)

        # Each entry records the balance its own movement produced, so the debits form the
        # chain 1000 -> 600 -> 200 in whichever order they actually applied.
        running = sorted(entry["balanceAfterMinor"] for entry in self.transfer_entries(self.sender))

        self.assertEqual(running, [200, 600])
        self.assert_every_ledger_agrees_with_its_balance()

    def test_many_concurrent_transfers_never_overdraw_the_sender(self):
        outcomes = self.run_together(*[lambda: self.send(300) for _ in range(6)])

        succeeded = [kind for kind, _ in outcomes].count("ok")

        self.assertEqual(succeeded, 3)
        self.assertEqual(self.balance(self.sender), 100)
        self.assertEqual(self.balance(self.recipient), 900)
        self.assertGreaterEqual(self.balance(self.sender), 0)
        self.assert_every_ledger_agrees_with_its_balance()
        self.assertEqual(self.system_total(), 1500)

    def test_concurrent_transfers_in_opposite_directions_both_settle(self):
        def receive():
            return transfer_services.execute_transfer(
                self.account_document(self.third_party), SENDER_EMAIL, 50, None
            )

        outcomes = self.run_together(lambda: self.send(600), receive)

        self.assertEqual([kind for kind, _ in outcomes], ["ok", "ok"])
        self.assertEqual(self.balance(self.sender), 450)
        self.assertEqual(self.balance(self.recipient), 600)
        self.assertEqual(self.balance(self.third_party), 450)
        self.assert_every_ledger_agrees_with_its_balance()


class InterruptedTransferTest(TransferTestCase):
    def assert_settled(self, transfer_id, amount_minor):
        transfer = transfer_repository.find_transfer(transfer_id)

        self.assertEqual(transfer["state"], "COMPLETED")
        self.assertEqual(len(self.legs_of(transfer_id)), 2)
        self.assertEqual(self.system_total(), 1500)
        self.assert_every_ledger_agrees_with_its_balance()

    def test_a_transfer_interrupted_after_debiting_the_sender_is_completed_not_reversed(self):
        with interrupted_at(1):
            with self.assertRaises(RuntimeError):
                self.send(400)

        transfer = self.only_transfer()

        self.assertEqual(transfer["state"], "PENDING")
        self.assertEqual(self.balance(self.sender), 600)
        self.assertEqual(self.balance(self.recipient), 0)
        self.assertEqual(self.transfer_entries(self.sender), [])

        self.abandon(transfer["_id"])
        transfer_services.recover_in_flight()

        self.assert_settled(transfer["_id"], 400)
        self.assertEqual(self.balance(self.sender), 600)
        self.assertEqual(self.balance(self.recipient), 400)
        self.assert_nothing_is_left_in_flight()

    def test_a_transfer_interrupted_after_crediting_the_recipient_is_completed(self):
        with interrupted_at(2):
            with self.assertRaises(RuntimeError):
                self.send(400)

        transfer = self.only_transfer()

        self.assertEqual(transfer["state"], "PENDING")
        self.assertEqual(self.balance(self.sender), 600)
        self.assertEqual(self.balance(self.recipient), 400)
        self.assertEqual(self.transfer_entries(self.recipient), [])

        self.abandon(transfer["_id"])
        transfer_services.recover_in_flight()

        self.assert_settled(transfer["_id"], 400)
        self.assertEqual(self.balance(self.recipient), 400)
        self.assert_nothing_is_left_in_flight()

    def test_a_transfer_interrupted_before_completion_is_finished_without_moving_money_again(self):
        with interrupted_before_completion():
            with self.assertRaises(RuntimeError):
                self.send(400)

        transfer = self.only_transfer()

        self.assertEqual(transfer["state"], "PENDING")
        self.assertEqual(self.balance(self.sender), 600)
        self.assertEqual(self.balance(self.recipient), 400)
        self.assertEqual(len(self.legs_of(transfer["_id"])), 2)

        self.abandon(transfer["_id"])
        transfer_services.recover_in_flight()

        self.assert_settled(transfer["_id"], 400)
        self.assertEqual(self.balance(self.sender), 600)
        self.assertEqual(self.balance(self.recipient), 400)
        self.assert_nothing_is_left_in_flight()

    def test_recovery_can_be_repeated_without_changing_anything(self):
        with interrupted_at(1):
            with self.assertRaises(RuntimeError):
                self.send(400)

        transfer_id = self.abandon(self.only_transfer()["_id"])
        transfer_services.recover_in_flight()
        settled = (self.balance(self.sender), self.balance(self.recipient), LedgerEntry.objects.count())

        transfer_services.recover_in_flight()
        transfer_services.recover_in_flight()

        self.assertEqual((self.balance(self.sender), self.balance(self.recipient), LedgerEntry.objects.count()), settled)
        self.assert_settled(transfer_id, 400)

    def test_a_later_transfer_finishes_one_an_earlier_request_left_behind(self):
        with interrupted_at(1):
            with self.assertRaises(RuntimeError):
                self.send(400)

        abandoned = self.abandon(self.only_transfer()["_id"])

        self.send(100)

        self.assert_settled(abandoned, 400)
        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 2)
        self.assertEqual(self.balance(self.sender), 500)
        self.assertEqual(self.balance(self.recipient), 500)
        self.assert_nothing_is_left_in_flight()

    def test_an_interrupted_transfer_never_reports_success(self):
        with interrupted_at(1):
            with self.assertRaises(RuntimeError):
                self.send(400)

        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 0)

    def test_recovery_completes_a_transfer_that_had_not_taken_money_yet(self):
        transfer = self._abandoned_transfer(400)

        self.abandon(transfer["_id"])
        transfer_services.recover_in_flight()

        self.assert_settled(transfer["_id"], 400)
        self.assertEqual(self.balance(self.sender), 600)
        self.assertEqual(self.balance(self.recipient), 400)
        self.assert_nothing_is_left_in_flight()

    def test_recovery_fails_a_transfer_whose_funds_were_taken_while_it_waited(self):
        # Money this transfer was waiting to take has since been spent by a later one, so
        # the reservation can no longer be honoured. Failing it is correct: nothing is owed.
        abandoned = self._abandoned_transfer(900)
        self.send(900)

        self.abandon(abandoned["_id"])
        transfer_services.recover_in_flight()

        settled = transfer_repository.find_transfer(abandoned["_id"])

        self.assertEqual(settled["state"], "FAILED")
        self.assertEqual(settled["failureCode"], "INSUFFICIENT_FUNDS")
        self.assertEqual(self.legs_of(abandoned["_id"]), [])
        # Only the later transfer paid the recipient; the abandoned one must add nothing.
        self.assertEqual(self.balance(self.recipient), 900)
        self.assertEqual(self.system_total(), 1500)
        self.assert_every_ledger_agrees_with_its_balance()

    def test_recovery_leaves_a_transfer_that_a_request_may_still_be_running_alone(self):
        # Two requests must never work on the same transfer: whichever got there first
        # would release the movements the other is relying on to avoid applying them twice.
        # A transfer too young to be finished proves no request is still on it.
        transfer = self._abandoned_transfer(400)

        transfer_services.recover_in_flight()

        self.assertEqual(transfer_repository.find_transfer(transfer["_id"])["state"], "PENDING")
        self.assertEqual(self.balance(self.sender), 1000)
        self.assertEqual(self.balance(self.recipient), 0)
        self.assertEqual(self.legs_of(transfer["_id"]), [])

    def test_a_completed_transfer_is_never_driven_again(self):
        result = self.send(400)
        transfer_id = ObjectId(result["transfer"]["id"])

        transfer_services.complete_transfer(transfer_repository.find_transfer(transfer_id))

        self.assertEqual(len(self.transfer_entries(self.sender)), 1)
        self.assertEqual(self.balance(self.sender), 600)
        self.assertEqual(self.balance(self.recipient), 400)

    def _abandoned_transfer(self, amount_minor):
        """A transfer record left behind before it moved any money."""
        return transfer_repository.create_transfer(
            {
                "sender_user_id": self.sender.id,
                "sender_wallet_id": self.wallet_id(self.sender),
                "recipient_user_id": self.recipient.id,
                "recipient_wallet_id": self.wallet_id(self.recipient),
                "amount_minor": amount_minor,
            }
        )


class LedgerGuardTest(TransferTestCase):
    def test_the_database_refuses_a_second_entry_for_the_same_wallet_and_transaction(self):
        result = self.send(400)
        entry = self.transfer_entries(self.sender)[0]

        with self.assertRaises(NotUniqueError):
            LedgerEntry(
                wallet_id=entry["walletId"],
                user_id=entry["userId"],
                transaction_id=ObjectId(result["transfer"]["transactionId"]),
                kind=entry["kind"],
                direction=entry["direction"],
                amount_minor=entry["amountMinor"],
                balance_after_minor=entry["balanceAfterMinor"],
            ).save()

    def test_an_in_flight_balance_movement_is_not_applied_twice(self):
        with interrupted_at(1):
            with self.assertRaises(RuntimeError):
                self.send(400)

        transfer_id = self.only_transfer()["_id"]
        _, status = wallet_repository.apply_debit(self.wallet_id(self.sender), transfer_id, 400)

        self.assertEqual(status, "ALREADY_APPLIED")
        self.assertEqual(self.balance(self.sender), 600)
        self.assertEqual(
            wallet_repository.find_movement(self.wallet_id(self.sender), transfer_id)["balanceAfterMinor"], 600
        )

    def test_a_debit_larger_than_the_balance_is_refused_by_the_database(self):
        _, status = wallet_repository.apply_debit(self.wallet_id(self.sender), ObjectId(), 5000)

        self.assertEqual(status, "REFUSED")
        self.assertEqual(self.balance(self.sender), 1000)
        self.assertEqual(self.wallet_document(self.sender)["pendingMovements"], [])
