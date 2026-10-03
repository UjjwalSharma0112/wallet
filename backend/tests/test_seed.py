import io
from contextlib import redirect_stdout

from apps.auth.models import UserAccount
from apps.wallet.models import LedgerEntry, Wallet
from apps.wallet.repository import derived_balance
from scripts import seed as seed_script
from scripts import seed_data

from .base import MongoTestCase


class SeedTest(MongoTestCase):
    def run_seed(self):
        with redirect_stdout(io.StringIO()):
            seed_script.seed()

    def test_the_baseline_matches_the_documented_collection_sizes(self):
        self.run_seed()

        self.assertEqual(UserAccount.objects.count(), len(seed_data.ACCOUNT_ROWS))
        self.assertEqual(Wallet.objects.count(), len(seed_data.ACCOUNT_ROWS))
        self.assertEqual(LedgerEntry.objects.count(), 3)

    def test_two_seed_runs_produce_the_same_baseline(self):
        self.run_seed()
        first = [(account.email, account.name) for account in UserAccount.objects.order_by("email")]
        first_balances = sorted(wallet["availableBalanceMinor"] for wallet in Wallet.objects.as_pymongo())

        self.run_seed()
        second = [(account.email, account.name) for account in UserAccount.objects.order_by("email")]
        second_balances = sorted(wallet["availableBalanceMinor"] for wallet in Wallet.objects.as_pymongo())

        self.assertEqual(first, second)
        self.assertEqual(first_balances, second_balances)
        self.assertEqual(UserAccount.objects.count(), len(seed_data.ACCOUNT_ROWS))

    def test_every_wallet_reconciles_with_its_own_ledger(self):
        self.run_seed()

        for wallet in Wallet.objects.as_pymongo():
            with self.subTest(wallet=str(wallet["_id"])):
                self.assertEqual(derived_balance(wallet["_id"]), wallet["availableBalanceMinor"])

    def test_every_ledger_entry_belongs_to_the_owner_of_its_wallet(self):
        self.run_seed()

        for entry in LedgerEntry.objects.as_pymongo():
            wallet = Wallet.objects(__raw__={"_id": entry["walletId"]}).as_pymongo().first()

            with self.subTest(entry=str(entry["_id"])):
                self.assertIsNotNone(wallet)
                self.assertEqual(entry["userId"], wallet["userId"])

    def test_accounts_and_wallets_are_one_to_one(self):
        self.run_seed()

        account_ids = {account.id for account in UserAccount.objects}
        wallet_owners = [wallet["userId"] for wallet in Wallet.objects.as_pymongo()]

        self.assertEqual(len(wallet_owners), len(set(wallet_owners)))
        self.assertEqual(set(wallet_owners), account_ids)

    def test_a_wallet_without_an_opening_balance_has_no_entries(self):
        self.run_seed()

        unopened = [row for row in seed_data.ACCOUNT_ROWS if row["openingBalanceMinor"] == 0]

        self.assertEqual(len(unopened), 1)

        for row in unopened:
            wallet = Wallet.objects(__raw__={"userId": UserAccount.objects(__raw__={"email": row["email"]}).first().id}).as_pymongo().first()

            self.assertEqual(wallet["availableBalanceMinor"], 0)
            self.assertEqual(LedgerEntry.objects(__raw__={"walletId": wallet["_id"]}).count(), 0)
