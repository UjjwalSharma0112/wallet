from bson import ObjectId

from apps.shared.errors import AppError
from apps.wallet import repository
from apps.wallet.models import LedgerEntry, Wallet
from apps.wallet.services import create_wallet, get_wallet, list_transactions

from .base import MongoTestCase


class WalletProvisioningTest(MongoTestCase):
    def test_provisioning_funds_the_wallet_with_an_opening_credit(self):
        account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=245000)

        wallet = repository.find_wallet_by_user_id(account.id)

        self.assertIsNotNone(wallet)
        self.assertEqual(wallet["currency"], "INR")
        self.assertEqual(wallet["availableBalanceMinor"], 245000)

        entries = repository.list_entries(wallet["_id"], 10, 0)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["kind"], "OPENING")
        self.assertEqual(entries[0]["direction"], "CREDIT")
        self.assertEqual(entries[0]["amountMinor"], 245000)
        self.assertEqual(entries[0]["balanceAfterMinor"], 245000)
        self.assertEqual(entries[0]["userId"], account.id)

    def test_a_zero_opening_balance_provisions_an_empty_wallet(self):
        account = self.create_account(email="grace.obi@wallet.demo", opening_balance_minor=0)

        wallet = repository.find_wallet_by_user_id(account.id)

        self.assertEqual(wallet["availableBalanceMinor"], 0)
        self.assertEqual(repository.list_entries(wallet["_id"], 10, 0), [])
        self.assertEqual(repository.derived_balance(wallet["_id"]), 0)

    def test_provisioning_the_same_account_again_changes_nothing(self):
        account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=245000)

        with self.assertRaises(AppError) as raised:
            create_wallet(account.id, 999999)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "WALLET_EXISTS")
        self.assertEqual(Wallet.objects.count(), 1)
        self.assertEqual(LedgerEntry.objects.count(), 1)
        self.assertEqual(repository.find_wallet_by_user_id(account.id)["availableBalanceMinor"], 245000)


class WalletLookupTest(MongoTestCase):
    def test_an_account_reads_its_own_wallet(self):
        account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=245000)

        self.assertEqual(get_wallet(account.id)["availableBalanceMinor"], 245000)
        self.assertEqual(get_wallet(account.id)["userId"], str(account.id))

    def test_each_account_resolves_only_its_own_wallet(self):
        aarav = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=245000)
        fatima = self.create_account(email="fatima.noor@wallet.demo", opening_balance_minor=12850)

        self.assertNotEqual(get_wallet(aarav.id)["id"], get_wallet(fatima.id)["id"])
        self.assertEqual(get_wallet(fatima.id)["userId"], str(fatima.id))

    def test_an_account_without_a_wallet_is_reported_as_missing(self):
        account = self.create_account(email="grace.obi@wallet.demo", provision=False)

        with self.assertRaises(AppError) as raised:
            get_wallet(account.id)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.code, "WALLET_NOT_FOUND")


class TransactionListingTest(MongoTestCase):
    def record(self, account, direction, amount_minor):
        wallet = repository.find_wallet_by_user_id(account.id)

        return repository.create_entry(
            {
                "wallet_id": wallet["_id"],
                "user_id": account.id,
                "transaction_id": ObjectId(),
                "kind": "OPENING",
                "direction": direction,
                "amount_minor": amount_minor,
                "balance_after_minor": wallet["availableBalanceMinor"] + amount_minor,
            }
        )

    def setUp(self):
        super().setUp()

        self.account = self.create_account(email="aarav.mehta@wallet.demo", opening_balance_minor=1000)
        self.record(self.account, "DEBIT", 100)

    def test_entries_are_returned_newest_first(self):
        self.record(self.account, "CREDIT", 500)

        page = list_transactions(self.account.id, 10, 0)

        self.assertEqual([entry["amountMinor"] for entry in page["entries"]], [500, 100, 1000])
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["limit"], 10)
        self.assertEqual(page["offset"], 0)

    def test_paging_returns_disjoint_pages_of_the_same_history(self):
        self.record(self.account, "CREDIT", 500)
        self.record(self.account, "CREDIT", 700)

        first = list_transactions(self.account.id, 2, 0)
        second = list_transactions(self.account.id, 2, 2)

        self.assertEqual(len(first["entries"]), 2)
        self.assertEqual(len(second["entries"]), 2)
        self.assertEqual(first["total"], 4)
        self.assertFalse({entry["id"] for entry in first["entries"]} & {entry["id"] for entry in second["entries"]})

    def test_listing_only_returns_the_requesting_accounts_entries(self):
        other = self.create_account(email="fatima.noor@wallet.demo", opening_balance_minor=7500)

        page = list_transactions(other.id, 10, 0)
        entries = repository.list_entries(repository.find_wallet_by_user_id(other.id)["_id"], 10, 0)

        self.assertEqual([entry["amountMinor"] for entry in page["entries"]], [7500])
        self.assertTrue(all(entry["userId"] == other.id for entry in entries))
