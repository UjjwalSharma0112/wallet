import io
from contextlib import redirect_stdout

from apps.auth.models import UserAccount
from apps.money_requests.constants import APPROVED_STATE
from apps.money_requests.models import MoneyRequest
from apps.transfers.models import Transfer
from apps.wallet.models import LedgerEntry, Wallet
from apps.wallet.repository import derived_balance
from scripts import seed as seed_script
from scripts import seed_data

from .base import MongoTestCase

OPENING_ENTRY_COUNT = len([row for row in seed_data.ACCOUNT_ROWS if row["openingBalanceMinor"] > 0])

# An approved request is paid through the transfer engine rather than written as a figure, so
# its transfer is part of the seeded history too and the counts below have to include it.
APPROVED_REQUEST_COUNT = len([row for row in seed_data.REQUEST_ROWS if row["state"] == APPROVED_STATE])
SEEDED_TRANSFER_COUNT = len(seed_data.TRANSFER_ROWS) + APPROVED_REQUEST_COUNT


class SeedTest(MongoTestCase):
    def run_seed(self):
        with redirect_stdout(io.StringIO()):
            seed_script.seed()

    def test_the_baseline_matches_the_documented_collection_sizes(self):
        self.run_seed()

        self.assertEqual(UserAccount.objects.count(), len(seed_data.ACCOUNT_ROWS))
        self.assertEqual(Wallet.objects.count(), len(seed_data.ACCOUNT_ROWS))
        self.assertEqual(Transfer.objects.count(), SEEDED_TRANSFER_COUNT)
        self.assertEqual(LedgerEntry.objects.count(), OPENING_ENTRY_COUNT + 2 * SEEDED_TRANSFER_COUNT)
        self.assertEqual(MoneyRequest.objects.count(), len(seed_data.REQUEST_ROWS))

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
            account = UserAccount.objects(__raw__={"email": row["email"]}).first()
            wallet = Wallet.objects(__raw__={"userId": account.id}).as_pymongo().first()

            self.assertEqual(wallet["availableBalanceMinor"], 0)
            self.assertEqual(LedgerEntry.objects(__raw__={"walletId": wallet["_id"]}).count(), 0)

    def test_every_seeded_transfer_is_complete_and_recorded_on_both_wallets(self):
        self.run_seed()

        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), SEEDED_TRANSFER_COUNT)

        for transfer in Transfer.objects.as_pymongo():
            with self.subTest(transfer=str(transfer["_id"])):
                legs = LedgerEntry.objects(__raw__={"transactionId": transfer["_id"]}).as_pymongo()

                self.assertEqual(len(legs), 2)
                self.assertEqual(
                    {leg["walletId"] for leg in legs}, {transfer["senderWalletId"], transfer["recipientWalletId"]}
                )
                self.assertEqual({leg["amountMinor"] for leg in legs}, {transfer["amountMinor"]})

    def test_only_an_approved_seeded_request_names_a_transfer(self):
        # A declined or expired request that claimed a payment in the baseline would be a
        # feature that appears to work and does not, so the link is checked from both ends.
        self.run_seed()

        named = set(MoneyRequest.objects(__raw__={"transferId": {"$exists": True}}).distinct("state"))

        self.assertEqual(named, {APPROVED_STATE})
        self.assertEqual(MoneyRequest.objects(__raw__={"state": APPROVED_STATE}).count(), APPROVED_REQUEST_COUNT)

    def test_every_seeded_request_names_two_accounts_that_exist(self):
        self.run_seed()

        account_ids = {account.id for account in UserAccount.objects}

        for request in MoneyRequest.objects.as_pymongo():
            with self.subTest(request=str(request["_id"])):
                self.assertIn(request["requesterUserId"], account_ids)
                self.assertIn(request["approverUserId"], account_ids)
                self.assertNotEqual(request["requesterUserId"], request["approverUserId"])

    def test_seeded_transfers_do_not_change_the_total_money_in_the_system(self):
        self.run_seed()

        total = sum(wallet["availableBalanceMinor"] for wallet in Wallet.objects.as_pymongo())
        opening = sum(row["openingBalanceMinor"] for row in seed_data.ACCOUNT_ROWS)

        self.assertEqual(total, opening)

    def test_no_wallet_is_left_holding_an_unfinished_movement(self):
        self.run_seed()

        for wallet in Wallet.objects.as_pymongo():
            with self.subTest(wallet=str(wallet["_id"])):
                self.assertEqual(wallet.get("pendingMovements", []), [])
