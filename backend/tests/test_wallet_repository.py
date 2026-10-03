"""Data-integrity tests for the wallet collections.

These exercise the storage layer directly, because the invariants about balance and
ownership are properties of the stored data, not of any one request.
"""

from bson import ObjectId
from mongoengine.errors import NotUniqueError, ValidationError

from apps.wallet import repository
from apps.wallet.models import LedgerEntry, Wallet

from .base import MongoTestCase


class WalletIntegrityTest(MongoTestCase):
    def record(self, wallet, direction, amount_minor, balance_after_minor):
        return repository.create_entry(
            {
                "wallet_id": wallet["_id"],
                "user_id": wallet["userId"],
                "transaction_id": ObjectId(),
                "kind": "OPENING",
                "direction": direction,
                "amount_minor": amount_minor,
                "balance_after_minor": balance_after_minor,
            }
        )

    def test_the_stored_balance_matches_the_balance_the_ledger_derives(self):
        account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=245000)

        wallet = repository.find_wallet_by_user_id(account.id)

        self.assertEqual(repository.derived_balance(wallet["_id"]), wallet["availableBalanceMinor"])

    def test_the_newest_entry_records_the_current_balance(self):
        account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=245000)

        wallet = repository.find_wallet_by_user_id(account.id)

        self.assertEqual(repository.latest_entry(wallet["_id"])["balanceAfterMinor"], wallet["availableBalanceMinor"])

    def test_credits_less_debits_equal_the_stored_balance(self):
        account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=1000)

        wallet = repository.find_wallet_by_user_id(account.id)
        self.record(wallet, "DEBIT", 400, 600)
        self.record(wallet, "CREDIT", 250, 850)
        stored = 850
        Wallet.objects(__raw__={"_id": wallet["_id"]}).update(set__available_balance_minor=stored)

        totals = repository.totals_by_direction(wallet["_id"])

        self.assertEqual(totals["CREDIT"] - totals["DEBIT"], stored)
        self.assertEqual(repository.derived_balance(wallet["_id"]), stored)

    def test_a_ledger_that_disagrees_with_the_stored_balance_is_detectable(self):
        account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=1000)

        wallet = repository.find_wallet_by_user_id(account.id)
        self.record(wallet, "DEBIT", 400, 600)

        self.assertNotEqual(repository.derived_balance(wallet["_id"]), wallet["availableBalanceMinor"])

    def test_a_negative_balance_is_rejected(self):
        account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=1000)

        with self.assertRaises(ValidationError):
            Wallet(user_id=account.id, available_balance_minor=-1).save()

    def test_a_non_positive_entry_amount_is_rejected(self):
        account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=1000)

        wallet = repository.find_wallet_by_user_id(account.id)

        for amount in (0, -100):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                self.record(wallet, "CREDIT", amount, 1000)

    def test_an_account_cannot_hold_two_wallets(self):
        account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=1000)

        with self.assertRaises(NotUniqueError):
            Wallet(user_id=account.id, available_balance_minor=500).save()

        self.assertEqual(Wallet.objects.count(), 1)

    def test_entries_are_never_rewritten_when_read_again(self):
        account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=245000)

        wallet = repository.find_wallet_by_user_id(account.id)
        first_read = repository.list_entries(wallet["_id"], 10, 0)
        second_read = repository.list_entries(wallet["_id"], 10, 0)

        self.assertEqual(
            [{key: str(value) for key, value in entry.items()} for entry in first_read],
            [{key: str(value) for key, value in entry.items()} for entry in second_read],
        )
        self.assertEqual(LedgerEntry.objects.count(), 1)
