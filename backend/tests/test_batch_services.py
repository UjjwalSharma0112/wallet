"""R7 — batch processing, as the database records it rather than as a route answers.

A batch is an orchestration layer over the transfer engine, so almost everything worth
pinning here is about what the engine was asked to do and what it wrote: which balances
moved, which ledger entries exist, how many transfers there are, and how many of them a
repeated run added. The claims under test are the ones R7 adds on top of R2 and R3 — that
one item's key is derived rather than sent, that an item's outcome is recorded once, that a
batch is not all-or-nothing, and that a batch belongs to the account that made it.

The tests read stored documents directly. A response saying `PARTIALLY_COMPLETED` proves
nothing on its own; what matters is that four transfers exist and were paid, one did not, and
no balance moved for it.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bson import ObjectId
from mongoengine.errors import NotUniqueError

from apps.batch import repository, services
from apps.batch.constants import (
    BATCH_IN_PROGRESS_CODE,
    BATCH_KEY_PREFIX,
    COMPLETED_STATE,
    FAILED_STATE,
    ITEM_COMPLETED_STATE,
    ITEM_FAILED_STATE,
    PARTIALLY_COMPLETED_STATE,
    PENDING_STATE,
    PROCESSING_STATE,
)
from apps.batch.models import Batch
from apps.shared.errors import AppError
from apps.transfers import repository as transfer_repository
from apps.transfers import services as transfer_services
from apps.transfers.constants import (
    IDEMPOTENCY_KEY_CONTENDED_CODE,
    INSUFFICIENT_FUNDS_CODE,
    MAX_KEY_CLAIM_ATTEMPTS,
    PENDING_STATE as TRANSFER_PENDING,
)
from apps.transfers.models import Transfer
from apps.wallet import repository as wallet_repository
from apps.wallet.models import LedgerEntry, Wallet

from .base import MongoTestCase

OWNER_EMAIL = "aarav.mehta@wallet.demo"
STRANGER_EMAIL = "grace.obi@wallet.demo"
FRIEND_EMAIL = "rahul.singh@wallet.demo"
SECOND_EMAIL = "priya.nair@wallet.demo"
THIRD_EMAIL = "amit.rao@wallet.demo"
FOURTH_EMAIL = "neha.iyer@wallet.demo"
FIFTH_EMAIL = "vikram.das@wallet.demo"
TOPPED_UP_EMAIL = "kavya.reddy@wallet.demo"

OPENING = 1_000_000

# Captured before any test patches it. Several tests below stand in for a competing request by
# really inserting and really failing a transfer, and they must reach the unpatched writer to do
# it — going through the patch would make them recurse into their own refusal.
REAL_CREATE_TRANSFER = transfer_repository.create_transfer


class BatchTestCase(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.owner = self.create_account(name="Aarav Mehta", email=OWNER_EMAIL, opening_balance_minor=OPENING)
        self.stranger = self.create_account(name="Grace Obi", email=STRANGER_EMAIL, opening_balance_minor=500)
        self.friend = self.create_account(name="Rahul Singh", email=FRIEND_EMAIL, opening_balance_minor=0)
        self.second = self.create_account(name="Priya Nair", email=SECOND_EMAIL, opening_balance_minor=0)
        self.third = self.create_account(name="Amit Rao", email=THIRD_EMAIL, opening_balance_minor=0)
        self.fourth = self.create_account(name="Neha Iyer", email=FOURTH_EMAIL, opening_balance_minor=0)
        self.fifth = self.create_account(name="Vikram Das", email=FIFTH_EMAIL, opening_balance_minor=0)
        self.topped_up = self.create_account(name="Kavya Reddy", email=TOPPED_UP_EMAIL, opening_balance_minor=10 * OPENING)

    # Reading stored state rather than a response.

    def balance(self, account):
        return wallet_repository.find_wallet_by_user_id(account.id)["availableBalanceMinor"]

    def wallet_id(self, account):
        return wallet_repository.find_wallet_by_user_id(account.id)["_id"]

    def entries(self, account):
        return wallet_repository.list_entries(self.wallet_id(account), 50, 0)

    def transfer_entries(self, account):
        return [entry for entry in self.entries(account) if entry["kind"].startswith("TRANSFER")]

    def legs_of(self, transfer_id):
        return list(LedgerEntry.objects(__raw__={"transactionId": transfer_id}).as_pymongo())

    def system_total(self):
        return sum(wallet["availableBalanceMinor"] for wallet in Wallet.objects.as_pymongo())

    def stored_batch(self, batch_id):
        return repository.find_batch(batch_id)

    def transfers_for(self, account):
        return list(Transfer.objects(__raw__={"senderUserId": account.id}).as_pymongo())

    def item(self, batch, position):
        return next(entry for entry in self.stored_batch(batch)["items"] if entry["position"] == position)

    # Building and running.

    def items(self, *pairs, note=None):
        return [
            {"position": position, "recipient_email": email, "amount_minor": amount, "note": note}
            for position, (email, amount) in enumerate(pairs)
        ]

    def make_batch(self, *pairs, owner=None, note=None):
        owner = owner or self.owner

        return services.create_batch(self.account_document(owner), self.items(*pairs, note=note))

    def process(self, batch_id, owner=None):
        return services.process_batch(self.account_document(owner or self.owner), batch_id)

    def top_up(self, amount_minor):
        """Pay the owner in, through the engine, so the ledger stays honest about it."""
        transfer_services.execute_transfer(
            self.account_document(self.topped_up), OWNER_EMAIL, amount_minor, "top up"
        )


class CreationTestCase(BatchTestCase):
    def test_a_new_batch_is_pending_and_has_paid_nothing(self):
        batch = self.make_batch((FRIEND_EMAIL, 50000), (SECOND_EMAIL, 25000))

        self.assertEqual(batch["state"], PENDING_STATE)
        self.assertEqual(len(batch["items"]), 2)
        self.assertEqual(batch["summary"]["totalItems"], 2)
        self.assertEqual(batch["summary"]["pendingItems"], 2)
        self.assertEqual(batch["summary"]["completedAmountMinor"], 0)

        self.assertEqual(Transfer.objects.count(), 0)
        # An opening credit only exists for an account that was opened with money, and
        # three of this fixture's eight accounts were.
        self.assertEqual(LedgerEntry.objects.count(), 3)
        self.assertEqual(self.balance(self.owner), OPENING)

    def test_items_keep_the_position_they_were_given(self):
        batch = self.make_batch((FRIEND_EMAIL, 111), (SECOND_EMAIL, 222), (THIRD_EMAIL, 333))

        stored = self.stored_batch(batch["id"])

        self.assertEqual([entry["position"] for entry in stored["items"]], [0, 1, 2])
        self.assertEqual(
            [entry["amountMinor"] for entry in stored["items"]], [111, 222, 333]
        )

    def test_a_batch_records_no_figures_of_its_own(self):
        """The summary is read off the items, so there is no second place for it to disagree."""
        batch = self.make_batch((FRIEND_EMAIL, 50000), (SECOND_EMAIL, 25000))
        stored = self.stored_batch(batch["id"])

        for figure in ("completedItems", "failedItems", "completedAmountMinor", "failedAmountMinor"):
            self.assertNotIn(figure, stored)

        self.process(batch["id"])
        stored = self.stored_batch(batch["id"])

        for figure in ("completedItems", "failedItems", "completedAmountMinor", "failedAmountMinor"):
            self.assertNotIn(figure, stored)


class DerivedKeyTestCase(BatchTestCase):
    def test_one_item_always_spends_the_same_key(self):
        batch = self.make_batch((FRIEND_EMAIL, 1000), (SECOND_EMAIL, 2000))
        batch_id = batch["id"]

        self.assertEqual(services.batch_key(batch_id, 0), f"{BATCH_KEY_PREFIX}{batch_id}:0")
        self.assertEqual(services.batch_key(batch_id, 0), services.batch_key(batch_id, 0))

    def test_each_item_spends_a_key_of_its_own(self):
        batch = self.make_batch((FRIEND_EMAIL, 1000), (SECOND_EMAIL, 2000))

        self.assertNotEqual(services.batch_key(batch["id"], 0), services.batch_key(batch["id"], 1))

    def test_the_key_belongs_to_the_batch_that_derived_it(self):
        first = self.make_batch((FRIEND_EMAIL, 1000))
        second = self.make_batch((FRIEND_EMAIL, 1000))

        self.assertNotEqual(services.batch_key(first["id"], 0), services.batch_key(second["id"], 0))

    def test_every_settled_item_left_its_key_held_by_its_own_transfer(self):
        batch = self.make_batch((FRIEND_EMAIL, 50000), (SECOND_EMAIL, 25000))
        self.process(batch["id"])

        for position in (0, 1):
            claimed = transfer_repository.find_by_key(self.owner.id, services.batch_key(batch["id"], position))

            self.assertIsNotNone(claimed, f"item {position} left no claim on its key")
            self.assertEqual(claimed["state"], "COMPLETED")


class HappyPathTestCase(BatchTestCase):
    def test_five_items_all_succeed_and_move_money_exactly_once_each(self):
        batch = self.make_batch(
            (FRIEND_EMAIL, 10000),
            (SECOND_EMAIL, 20000),
            (THIRD_EMAIL, 30000),
            (FOURTH_EMAIL, 40000),
            (FIFTH_EMAIL, 50000),
        )

        result = self.process(batch["id"])

        self.assertEqual(result["batch"]["state"], COMPLETED_STATE)
        self.assertEqual(result["batch"]["summary"]["completedItems"], 5)
        self.assertEqual(result["batch"]["summary"]["failedItems"], 0)
        self.assertEqual(result["batch"]["summary"]["completedAmountMinor"], 150000)
        self.assertEqual(result["batch"]["summary"]["failedAmountMinor"], 0)

        self.assertEqual(self.balance(self.owner), OPENING - 150000)
        self.assertEqual(self.balance(self.friend), 10000)
        self.assertEqual(self.balance(self.second), 20000)
        self.assertEqual(self.balance(self.third), 30000)
        self.assertEqual(self.balance(self.fourth), 40000)
        self.assertEqual(self.balance(self.fifth), 50000)

    def test_each_settled_item_has_one_transfer_and_two_ledger_legs(self):
        batch = self.make_batch((FRIEND_EMAIL, 10000), (SECOND_EMAIL, 20000), (THIRD_EMAIL, 30000))
        self.process(batch["id"])

        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 3)

        for transfer in self.transfers_for(self.owner):
            with self.subTest(transfer=str(transfer["_id"])):
                legs = self.legs_of(transfer["_id"])

                self.assertEqual(len(legs), 2)
                self.assertEqual({leg["direction"] for leg in legs}, {"DEBIT", "CREDIT"})
                self.assertEqual({leg["amountMinor"] for leg in legs}, {transfer["amountMinor"]})

    def test_the_owner_ledger_shows_one_debit_per_item(self):
        batch = self.make_batch((FRIEND_EMAIL, 10000), (SECOND_EMAIL, 20000))
        self.process(batch["id"])

        debits = [entry for entry in self.transfer_entries(self.owner) if entry["direction"] == "DEBIT"]

        self.assertEqual(len(debits), 2)
        self.assertEqual(sorted(entry["amountMinor"] for entry in debits), [10000, 20000])

    def test_a_settled_item_names_the_transfer_that_paid_it(self):
        batch = self.make_batch((FRIEND_EMAIL, 10000), (SECOND_EMAIL, 20000))
        result = self.process(batch["id"])

        named = {item["position"]: item["transferId"] for item in result["batch"]["items"]}

        self.assertEqual(len(set(named.values())), 2)

        for position, transfer_id in named.items():
            with self.subTest(position=position):
                self.assertEqual(self.item(batch["id"], position)["transferId"], ObjectId(transfer_id))

    def test_every_item_paid_a_different_account(self):
        batch = self.make_batch((FRIEND_EMAIL, 10000), (SECOND_EMAIL, 20000))
        self.process(batch["id"])

        recipients = {transfer["recipientUserId"] for transfer in self.transfers_for(self.owner)}

        self.assertEqual(recipients, {self.friend.id, self.second.id})


class PartialFailureTestCase(BatchTestCase):
    def test_one_item_that_cannot_be_paid_leaves_the_others_paid(self):
        batch = self.make_batch(
            (FRIEND_EMAIL, 50000),
            (SECOND_EMAIL, 75000),
            (THIRD_EMAIL, 3_000_000),  # more than the owner holds
            (FOURTH_EMAIL, 45000),
        )

        result = self.process(batch["id"])

        self.assertEqual(result["batch"]["state"], PARTIALLY_COMPLETED_STATE)
        self.assertEqual(result["batch"]["summary"]["completedItems"], 3)
        self.assertEqual(result["batch"]["summary"]["failedItems"], 1)
        self.assertEqual(result["batch"]["summary"]["pendingItems"], 0)
        self.assertEqual(result["batch"]["summary"]["completedAmountMinor"], 170000)
        self.assertEqual(result["batch"]["summary"]["failedAmountMinor"], 3_000_000)

    def test_an_item_that_failed_is_not_left_half_paid(self):
        batch = self.make_batch((FRIEND_EMAIL, 50000), (THIRD_EMAIL, 3_000_000))
        self.process(batch["id"])

        failed = self.item(batch["id"], 1)

        self.assertEqual(failed["state"], ITEM_FAILED_STATE)
        self.assertEqual(failed["failureCode"], INSUFFICIENT_FUNDS_CODE)
        self.assertIsNone(failed.get("transferId"))

        # The engine's own refusal is what is recorded, verbatim. A batch keeps no table of its
        # own wording, so this row says exactly what the same payment would have said had it
        # been sent on its own — asserted by identity, not by a message that happens to match.
        self.assertEqual(failed["failureMessage"], transfer_services.INSUFFICIENT_FUNDS_MESSAGE)

        # Two rows for the sender: the one that was paid and the one that was refused.
        # Only the first ever moved anything.
        self.assertEqual(len(self.transfers_for(self.owner)), 2)
        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 1)
        self.assertEqual(self.balance(self.third), 0)
        self.assertEqual(self.entries(self.third), [])

    def test_the_failed_transfer_recorded_nothing_in_the_ledger(self):
        batch = self.make_batch((FRIEND_EMAIL, 50000), (THIRD_EMAIL, 3_000_000))
        self.process(batch["id"])

        failed_transfers = Transfer.objects(__raw__={"failureCode": INSUFFICIENT_FUNDS_CODE}).as_pymongo()

        self.assertEqual(len(failed_transfers), 1)
        self.assertEqual(self.legs_of(failed_transfers[0]["_id"]), [])

    def test_an_item_after_a_failed_one_still_runs(self):
        batch = self.make_batch((THIRD_EMAIL, 3_000_000), (FRIEND_EMAIL, 50000), (SECOND_EMAIL, 75000))
        result = self.process(batch["id"])

        self.assertEqual([item["state"] for item in result["batch"]["items"]],
                         [ITEM_FAILED_STATE, ITEM_COMPLETED_STATE, ITEM_COMPLETED_STATE])
        self.assertEqual(self.balance(self.friend), 50000)
        self.assertEqual(self.balance(self.second), 75000)

    def test_a_batch_where_every_item_failed_is_failed(self):
        batch = self.make_batch((THIRD_EMAIL, 3_000_000), (FOURTH_EMAIL, 4_000_000))
        result = self.process(batch["id"])

        self.assertEqual(result["batch"]["state"], FAILED_STATE)
        self.assertEqual(self.balance(self.owner), OPENING)
        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 0)


class RetryTestCase(BatchTestCase):
    def test_running_a_batch_twice_moves_money_once_per_settled_item(self):
        batch = self.make_batch(
            (FRIEND_EMAIL, 10000), (SECOND_EMAIL, 20000), (THIRD_EMAIL, 30000)
        )

        self.process(batch["id"])
        after_first = self.balance(self.owner)
        first_movements = len(self.transfer_entries(self.owner))

        self.process(batch["id"])

        self.assertEqual(self.balance(self.owner), after_first)
        self.assertEqual(len(self.transfer_entries(self.owner)), first_movements)
        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 3)

    def test_running_a_partly_failed_batch_again_does_not_repay_what_was_paid(self):
        batch = self.make_batch((FRIEND_EMAIL, 50000), (SECOND_EMAIL, 75000), (THIRD_EMAIL, 3_000_000))

        self.process(batch["id"])
        self.process(batch["id"])
        self.process(batch["id"])

        self.assertEqual(self.balance(self.owner), OPENING - 125000)
        self.assertEqual(len(self.transfer_entries(self.owner)), 2)
        self.assertEqual(self.balance(self.friend), 50000)
        self.assertEqual(self.balance(self.second), 75000)

    def test_an_item_that_failed_is_tried_again_with_the_same_key(self):
        owed = OPENING + 1000
        batch = self.make_batch((THIRD_EMAIL, owed))
        key = services.batch_key(batch["id"], 0)
        self.assertEqual(self.process(batch["id"])["batch"]["state"], FAILED_STATE)

        self.top_up(owed)
        result = self.process(batch["id"])

        self.assertEqual(result["batch"]["state"], COMPLETED_STATE)
        self.assertEqual(self.balance(self.third), owed)
        self.assertEqual(self.balance(self.owner), OPENING)

        # The owner has two transfers against the batch's one key: the one that failed and
        # released it, and the one that then spent it. Only one of them moved money, and the
        # top-up that made it possible is a third transfer by somebody else entirely.
        mine = self.transfers_for(self.owner)

        self.assertEqual(len(mine), 2)
        self.assertEqual([transfer["state"] for transfer in mine].count("COMPLETED"), 1)
        self.assertEqual([transfer["state"] for transfer in mine].count("FAILED"), 1)
        # The failed one gave the key back; the one that then spent it still holds it. That
        # handover is the whole of why a failed item is retryable at all.
        by_state = {transfer["state"]: transfer for transfer in mine}

        self.assertEqual(by_state["COMPLETED"]["idempotencyKey"], key)
        self.assertNotIn("idempotencyKey", by_state["FAILED"])
        self.assertEqual(len(self.transfer_entries(self.owner)), 2)  # the batch, plus the top-up

    def test_a_settled_item_is_never_written_a_second_time(self):
        batch = self.make_batch((FRIEND_EMAIL, 50000))
        self.process(batch["id"])
        settled = self.item(batch["id"], 0)

        self.process(batch["id"])

        self.assertEqual(self.item(batch["id"], 0), settled)

    def test_running_a_batch_that_paid_everything_moves_nothing_more(self):
        batch = self.make_batch((FRIEND_EMAIL, 50000), (SECOND_EMAIL, 25000))
        self.process(batch["id"])

        again = self.process(batch["id"])

        self.assertEqual(again["batch"]["state"], COMPLETED_STATE)
        self.assertEqual(self.balance(self.owner), OPENING - 75000)
        self.assertEqual(Transfer.objects.count(), 2)


class ConcurrencyTestCase(BatchTestCase):
    def abandoned_claim(self, batch_id, age_seconds=3600):
        """Leave a batch claimed as though a processing run were interrupted."""
        stale = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)

        Batch.objects(__raw__={"_id": ObjectId(batch_id)}).update(
            set__state=PROCESSING_STATE, set__processing_started_at=stale, set__updated_at=stale
        )

    def test_two_runs_taking_over_one_batch_move_money_once_per_item(self):
        from concurrent.futures import ThreadPoolExecutor

        batch = self.make_batch(
            (FRIEND_EMAIL, 10000), (SECOND_EMAIL, 20000), (THIRD_EMAIL, 30000)
        )
        # Both requests will find the claim abandoned, so both will try to take it over. The
        # derived keys — not the claim — are what has to stop this moving money twice.
        self.abandoned_claim(batch["id"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.process, batch["id"]) for _ in range(2)]
            outcomes = []

            for future in futures:
                try:
                    outcomes.append(future.result())
                except AppError as error:
                    outcomes.append(error.code)

        with self.subTest("one movement per item"):
            self.assertEqual(self.balance(self.owner), OPENING - 60000)
            self.assertEqual(self.balance(self.friend), 10000)
            self.assertEqual(self.balance(self.second), 20000)
            self.assertEqual(self.balance(self.third), 30000)

        with self.subTest("no duplicate transfer"):
            self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 3)
            self.assertEqual(len(self.transfers_for(self.owner)), 3)

        with self.subTest("no duplicate ledger legs"):
            # The owner is debited once per item; each recipient is credited once, by the one
            # item addressed to them. Any repetition here would be a second movement.
            self.assertEqual(len(self.transfer_entries(self.owner)), 3)
            self.assertEqual(len(self.transfer_entries(self.friend)), 1)
            self.assertEqual(len(self.transfer_entries(self.second)), 1)
            self.assertEqual(len(self.transfer_entries(self.third)), 1)

        with self.subTest("every ledger entry names a real transfer"):
            settled = {transfer["_id"] for transfer in self.transfers_for(self.owner)}
            entries = LedgerEntry.objects(__raw__={"kind": {"$in": ["TRANSFER_OUT", "TRANSFER_IN"]}}).as_pymongo()

            self.assertEqual(len(entries), 6)
            for entry in entries:
                self.assertIn(entry["transactionId"], settled)

        with self.subTest("batch state is coherent"):
            self.assertEqual(self.stored_batch(batch["id"])["state"], COMPLETED_STATE)

        # Which of the two runs gets to do the work is a matter of timing and is not the
        # guarantee. Both may take the abandoned claim over and both be answered, or one may
        # take it and find the other already claimed. Only the movement count is fixed, so
        # what is asserted is that every run was answered and that every answer was one a
        # caller can act on: a finished batch, or a refusal naming the run that holds the
        # claim. Asserting that exactly one of them is refused would be asserting the
        # interleaving rather than the invariant, and would fail whenever both runs happen
        # to read the claim as abandoned before either has written to it.
        with self.subTest("every run got an answer it could act on"):
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    self.assertEqual(outcome, BATCH_IN_PROGRESS_CODE)
                    continue

                self.assertEqual(outcome["batch"]["summary"]["completedItems"], 3)
                self.assertEqual(outcome["wallet"]["availableBalanceMinor"], OPENING - 60000)

            # At least one run must have done the work: the claim is taken over by one run
            # or the other, so there is no interleaving in which neither of them has it.
            self.assertGreaterEqual(len([o for o in outcomes if isinstance(o, dict)]), 1)

    def test_a_fresh_claim_is_not_taken_over(self):
        batch = self.make_batch((FRIEND_EMAIL, 10000))
        now = datetime.now(timezone.utc)

        Batch.objects(__raw__={"_id": ObjectId(batch["id"])}).update(
            set__state=PROCESSING_STATE, set__processing_started_at=now
        )

        with self.assertRaises(AppError) as refused:
            self.process(batch["id"])

        self.assertEqual(refused.exception.code, BATCH_IN_PROGRESS_CODE)
        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(self.balance(self.owner), OPENING)

    def test_a_batch_claimed_with_no_stamp_is_not_taken_over(self):
        batch = self.make_batch((FRIEND_EMAIL, 10000))

        Batch.objects(__raw__={"_id": ObjectId(batch["id"])}).update(
            set__state=PROCESSING_STATE, unset__processing_started_at=1
        )

        with self.assertRaises(AppError) as refused:
            self.process(batch["id"])

        self.assertEqual(refused.exception.code, BATCH_IN_PROGRESS_CODE)


class OwnershipTestCase(BatchTestCase):
    def test_a_stranger_cannot_process_a_batch(self):
        batch = self.make_batch((FRIEND_EMAIL, 50000))

        with self.assertRaises(AppError) as refused:
            self.process(batch["id"], owner=self.stranger)

        self.assertEqual(refused.exception.status_code, 403)
        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(self.balance(self.owner), OPENING)

    def test_a_stranger_is_told_the_same_thing_whether_or_not_the_batch_exists(self):
        batch = self.make_batch((FRIEND_EMAIL, 50000))
        stranger = self.account_document(self.stranger)

        with self.assertRaises(AppError) as refused:
            services.process_batch(stranger, batch["id"])

        with self.assertRaises(AppError) as missing:
            services.process_batch(stranger, ObjectId())

        self.assertEqual(refused.exception.status_code, 403)
        self.assertEqual(missing.exception.status_code, 404)
        self.assertNotIn(missing.exception.message, refused.exception.message)

    def test_a_batch_that_does_not_exist_is_not_found(self):
        with self.assertRaises(AppError) as refused:
            self.process(ObjectId())

        self.assertEqual(refused.exception.status_code, 404)

    def test_an_id_that_is_not_an_id_is_not_found_rather_than_raised(self):
        with self.assertRaises(AppError) as refused:
            self.process("not-an-id")

        self.assertEqual(refused.exception.status_code, 404)

    def test_a_batch_records_the_account_that_created_it(self):
        batch = self.make_batch((FRIEND_EMAIL, 50000))

        self.assertEqual(self.stored_batch(batch["id"])["ownerUserId"], self.owner.id)


class ConservationTestCase(BatchTestCase):
    def test_a_batch_that_moves_money_conserves_the_system_total(self):
        before = self.system_total()
        batch = self.make_batch(
            (FRIEND_EMAIL, 50000),
            (SECOND_EMAIL, 75000),
            (THIRD_EMAIL, 30000),
            (FOURTH_EMAIL, 3_000_000),
        )

        self.process(batch["id"])

        self.assertEqual(self.system_total(), before)

    def test_every_wallet_agrees_with_its_own_ledger_after_a_batch(self):
        batch = self.make_batch(
            (FRIEND_EMAIL, 50000), (SECOND_EMAIL, 75000), (THIRD_EMAIL, 3_000_000)
        )
        self.process(batch["id"])

        for account in (self.owner, self.friend, self.second, self.third, self.fourth):
            with self.subTest(account=account.email):
                self.assertEqual(wallet_repository.derived_balance(self.wallet_id(account)), self.balance(account))

    def test_no_wallet_is_left_holding_an_unfinished_movement(self):
        batch = self.make_batch(
            (FRIEND_EMAIL, 50000), (SECOND_EMAIL, 75000), (THIRD_EMAIL, 3_000_000)
        )
        self.process(batch["id"])

        for wallet in Wallet.objects.as_pymongo():
            with self.subTest(wallet=str(wallet["_id"])):
                self.assertEqual(wallet.get("pendingMovements", []), [])

    def test_no_wallet_is_left_negative(self):
        batch = self.make_batch((FRIEND_EMAIL, 900000), (SECOND_EMAIL, 900000))
        self.process(batch["id"])

        for wallet in Wallet.objects.as_pymongo():
            with self.subTest(wallet=str(wallet["_id"])):
                self.assertGreaterEqual(wallet["availableBalanceMinor"], 0)


class RecoveryTestCase(BatchTestCase):
    """The engine's own recovery, and the fact that a batch item inherits it.

    Every item is one call to the engine, and every engine call sweeps for transfers an
    interrupted request left behind before it moves anything. A batch therefore gets the same
    protection a single transfer gets, without this feature knowing that recovery exists — which
    is the point of an item being an ordinary transfer rather than a cheaper kind of payment.
    """

    def interrupted_transfer(self, amount_minor, age_seconds=None):
        """A transfer row as an interrupted request leaves it: written, nothing yet paid.

        This is the state between the engine writing down what it intended and applying it,
        and it is the only state recovery acts on. Left at its real age it is too young for
        anybody to still be working on it, so `age_seconds` is what decides which half of the
        rule is being tested.
        """
        values = {
            "sender_user_id": self.owner.id,
            "sender_wallet_id": self.wallet_id(self.owner),
            "recipient_user_id": self.friend.id,
            "recipient_wallet_id": self.wallet_id(self.friend),
            "amount_minor": amount_minor,
            "state": TRANSFER_PENDING,
            "note": "interrupted",
        }

        if age_seconds is not None:
            values["created_at"] = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)

        return transfer_repository.create_transfer(values)

    def test_an_interrupted_transfer_is_finished_before_a_batch_item_moves_money(self):
        owed = self.interrupted_transfer(30000, age_seconds=600)
        transfer_id = owed["_id"]

        self.assertEqual(Transfer.objects(__raw__={"_id": transfer_id}).as_pymongo().first()["state"], TRANSFER_PENDING)
        self.assertEqual(len(self.legs_of(transfer_id)), 0)

        batch = self.make_batch((SECOND_EMAIL, 40000))
        result = self.process(batch["id"])

        recovered = Transfer.objects(__raw__={"_id": transfer_id}).as_pymongo().first()

        self.assertEqual(recovered["state"], "COMPLETED")
        self.assertEqual(len(self.legs_of(transfer_id)), 2)
        self.assertEqual(self.balance(self.friend), 30000)
        self.assertEqual(self.balance(self.second), 40000)
        self.assertEqual(self.balance(self.owner), OPENING - 70000)
        self.assertEqual(result["batch"]["state"], COMPLETED_STATE)

    def test_an_interrupted_transfer_too_young_to_recover_is_left_alone(self):
        """Recovery only touches transfers no request can still be working on."""
        owed = self.interrupted_transfer(30000)

        batch = self.make_batch((SECOND_EMAIL, 40000))
        self.process(batch["id"])

        left = Transfer.objects(__raw__={"_id": owed["_id"]}).as_pymongo().first()

        self.assertEqual(left["state"], TRANSFER_PENDING)
        self.assertEqual(len(self.legs_of(owed["_id"])), 0)
        self.assertEqual(self.balance(self.friend), 0)

        # The batch paid only its own item, out of the balance the interrupted transfer never
        # took, which is what leaving it alone is for.
        self.assertEqual(self.balance(self.second), 40000)
        self.assertEqual(self.balance(self.owner), OPENING - 40000)

    def test_an_interrupted_transfer_does_not_stop_the_batch_that_triggered_its_recovery(self):
        self.interrupted_transfer(30000, age_seconds=600)

        batch = self.make_batch((SECOND_EMAIL, 40000))
        result = self.process(batch["id"])

        self.assertEqual(result["batch"]["summary"]["completedItems"], 1)
        self.assertEqual(result["batch"]["state"], COMPLETED_STATE)


class RefusedRunTestCase(BatchTestCase):
    def test_an_item_nobody_can_pay_ends_the_run_and_leaves_every_item_alone(self):
        batch = self.make_batch(
            (FRIEND_EMAIL, 50000),
            ("nobody@wallet.demo", 10000),
            (SECOND_EMAIL, 75000),
        )

        with self.assertRaises(AppError) as refused:
            self.process(batch["id"])

        self.assertEqual(refused.exception.status_code, 404)

        # The claim was handed back, so the batch is runnable again rather than left claimed
        # for ever by a run that stopped. Nothing failed, and there is still work to do, so
        # the batch is pending — while saying plainly that one payment is already paid.
        stored = self.stored_batch(batch["id"])

        self.assertEqual(stored["state"], PENDING_STATE)
        self.assertIsNone(stored.get("processingStartedAt"))
        self.assertEqual(
            [entry["state"] for entry in stored["items"]],
            [ITEM_COMPLETED_STATE, "PENDING", "PENDING"],
        )
        self.assertEqual(self.balance(self.owner), OPENING - 50000)
        self.assertEqual(self.balance(self.friend), 50000)

        # Running it again pays nothing a second time and stops at the same bad item.
        with self.assertRaises(AppError):
            self.process(batch["id"])

        self.assertEqual(self.item(batch["id"], 0)["state"], ITEM_COMPLETED_STATE)
        self.assertEqual(self.balance(self.owner), OPENING - 50000)
        self.assertEqual(Transfer.objects.count(), 1)

    def test_an_item_addressed_to_the_sender_ends_the_run(self):
        batch = self.make_batch((OWNER_EMAIL, 10000))

        with self.assertRaises(AppError) as refused:
            self.process(batch["id"])

        self.assertEqual(refused.exception.status_code, 400)
        self.assertEqual(self.stored_batch(batch["id"])["state"], PENDING_STATE)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_a_run_refused_on_its_first_item_leaves_the_batch_pending(self):
        batch = self.make_batch(("nobody@wallet.demo", 10000), (FRIEND_EMAIL, 50000))

        with self.assertRaises(AppError):
            self.process(batch["id"])

        self.assertEqual(self.stored_batch(batch["id"])["state"], PENDING_STATE)
        self.assertEqual(self.balance(self.owner), OPENING)


class EngineClaimTestCase(BatchTestCase):
    """The one change batch processing needed in the engine, pinned here.

    A batch leans on a failed transfer giving its key back, and on that key then being free to
    spend. That makes two requests spending the same derived key at the same moment the normal
    case rather than a corner, so the engine's answer to losing that race is pinned: it must
    make the attempt again rather than fall over.

    Two defects are pinned here, both of which answered a legitimate race with a server error.
    The first lost the insert and then handed `None` to the replay, raising `AttributeError`.
    The second was the last insert in the attempt budget, left unguarded: a competitor claiming
    the key after the final read raised `NotUniqueError` and answered 500. What matters in both
    is the shape of the guarantee rather than the specific exception — an idempotency race is a
    thing the engine answers, and the one thing it must never do is answer it by falling over.
    """

    def refuse_the_amount_first(self, key):
        """Spend the key on a transfer that fails, which is what gives the key back."""
        with self.assertRaises(AppError) as refused:
            transfer_services.execute_transfer(
                self.account_document(self.owner), FRIEND_EMAIL, 5_000_000, None, key
            )

        self.assertEqual(refused.exception.code, INSUFFICIENT_FUNDS_CODE)
        self.assertIsNone(
            transfer_repository.find_by_key(self.owner.id, key),
            "a failed transfer must not keep holding its key",
        )

    def send(self, key, amount=50000, recipient=FRIEND_EMAIL):
        return transfer_services.execute_transfer(
            self.account_document(self.owner), recipient, amount, None, key
        )

    def fail_the_key(self, values):
        """Play the part of a competitor that claims a key and then fails, giving it back.

        The insert really happens and the failure really releases the key, so the key is free by
        the time the caller is handed its refusal. A mock that simply refused would not test what
        is being claimed here — that a released key is genuinely spendable afterwards.
        """
        claimed = REAL_CREATE_TRANSFER(values)
        transfer_repository.mark_failed(claimed["_id"], "COMPETITOR_FAILED")

        self.assertIsNone(
            transfer_repository.find_by_key(self.owner.id, values["idempotency_key"]),
            "a failed competitor must have handed the key back",
        )

    def settled(self):
        return Transfer.objects(__raw__={"state": "COMPLETED"}).count()

    def test_a_key_released_by_a_failure_can_be_spent_again(self):
        key = services.batch_key(ObjectId(), 0)
        self.refuse_the_amount_first(key)

        payload, created = transfer_services.execute_transfer(
            self.account_document(self.owner), FRIEND_EMAIL, 50000, None, key
        )

        self.assertTrue(created)
        self.assertEqual(payload["transfer"]["state"], "COMPLETED")
        self.assertEqual(self.balance(self.owner), OPENING - 50000)
        self.assertEqual(self.balance(self.friend), 50000)

    def test_losing_the_insert_to_a_request_that_then_failed_makes_the_attempt_again(self):
        key = services.batch_key(ObjectId(), 0)
        self.refuse_the_amount_first(key)

        create_transfer = transfer_repository.create_transfer
        attempts = []

        def lose_the_first_insert(values):
            # One refusal, and only one, which is what losing the race to a request that then
            # failed looks like. By the time this is called the key is free again, and reading
            # it is what has to notice.
            attempts.append(values)

            if len(attempts) == 1:
                raise NotUniqueError("lost the insert to a request that then failed")

            return create_transfer(values)

        with patch("apps.transfers.services.repository.create_transfer", lose_the_first_insert):
            payload, created = transfer_services.execute_transfer(
                self.account_document(self.owner), FRIEND_EMAIL, 50000, None, key
            )

        self.assertEqual(len(attempts), 2, "the engine gave up on the key instead of trying again")
        self.assertTrue(created)
        self.assertEqual(payload["transfer"]["state"], "COMPLETED")
        self.assertEqual(self.balance(self.owner), OPENING - 50000)
        self.assertEqual(Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 1)
        self.assertEqual(len(self.transfer_entries(self.owner)), 1)

    def test_losing_the_insert_to_a_request_that_holds_the_key_answers_from_it(self):
        key = services.batch_key(ObjectId(), 0)
        create_transfer = transfer_repository.create_transfer
        attempts = []

        def lose_the_first_insert(values):
            # A competitor that won this insert and has not finished yet. This request must not
            # make a second transfer, and must not carry the competitor's further either.
            attempts.append(values)
            create_transfer(values)

            raise NotUniqueError("lost the insert to a request that still holds the key")

        with patch("apps.transfers.services.repository.create_transfer", lose_the_first_insert):
            payload, created = transfer_services.execute_transfer(
                self.account_document(self.owner), FRIEND_EMAIL, 50000, None, key
            )

        self.assertEqual(len(attempts), 1)
        self.assertFalse(created, "this call did not create the transfer and must not claim to")
        self.assertTrue(payload.get("idempotentReplay"))
        self.assertEqual(payload["transfer"]["state"], TRANSFER_PENDING)
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(self.balance(self.owner), OPENING)

    def test_a_race_that_outlasts_the_attempt_budget_still_answers_from_the_winner(self):
        """Refused on every insert in the budget, including the last, by a request still holding the key.

        The last insert is the one that used to be unguarded, so this is where a competitor that
        claims the key after the final read is pinned. The winner is the answer however late it
        arrived, and this call must still create nothing and move nothing.
        """
        key = services.batch_key(ObjectId(), 0)
        attempts = []

        def lose_every_insert(values):
            # A rival that wins each of these inserts. The earlier ones give the key straight back,
            # which is what keeps the read before the last insert finding nothing. The last one
            # keeps it, so the fallback insert is refused by a request that is still holding the
            # key — the one moment the unguarded fallback used to turn into a server error.
            attempts.append(values)

            if len(attempts) < MAX_KEY_CLAIM_ATTEMPTS + 1:
                self.fail_the_key(values)
            else:
                REAL_CREATE_TRANSFER(values)

            raise NotUniqueError("lost the insert to a request that still holds the key")

        with patch("apps.transfers.services.repository.create_transfer", lose_every_insert):
            payload, created = self.send(key)

        self.assertEqual(
            len(attempts),
            MAX_KEY_CLAIM_ATTEMPTS + 1,
            "the last insert must be inside the budget rather than one attempt past it",
        )
        self.assertFalse(created, "this call did not create the transfer and must not claim to")
        self.assertTrue(payload.get("idempotentReplay"))
        self.assertEqual(payload["transfer"]["state"], TRANSFER_PENDING)
        # Two of these left a failed record behind from when they gave the key back, and the third
        # is the winner this call answered from. Only the winner exists, and it is not settled.
        self.assertEqual(self.settled(), 0, "nothing settled, because this call settled nothing")
        self.assertEqual(Transfer.objects(__raw__={"state": "PENDING"}).count(), 1, "one holder")
        self.assertEqual(len(self.transfer_entries(self.owner)), 0, "no debit")
        self.assertEqual(self.balance(self.owner), OPENING)
        self.assertEqual(self.balance(self.friend), 0)

    def test_a_race_that_keeps_releasing_the_key_is_reported_rather_than_crashed(self):
        """Refused on every insert in the budget, and the key free again after each refusal.

        Every refusal proves a competitor was there; none of them left anything behind to answer
        from. This is the outcome a bounded budget cannot resolve, and it is the case that used
        to escape as a bare `NotUniqueError` and answer 500. It has to be answered as a refusal
        the caller may repeat, with nothing sent.
        """
        key = services.batch_key(ObjectId(), 0)
        attempts = []

        def lose_every_insert(values):
            # A competitor that claimed the key and then failed on it, every time. The key is
            # genuinely released between refusals, so this is a real race rather than a mock that
            # always says no, and there is nothing left to read back.
            attempts.append(values)
            self.fail_the_key(values)

            raise NotUniqueError("lost the insert to a request that failed on the key")

        with patch("apps.transfers.services.repository.create_transfer", lose_every_insert):
            with self.assertRaises(AppError) as refused:
                self.send(key)

        self.assertEqual(refused.exception.status_code, 409)
        self.assertEqual(refused.exception.code, IDEMPOTENCY_KEY_CONTENDED_CODE)
        self.assertEqual(len(attempts), MAX_KEY_CLAIM_ATTEMPTS + 1, "the budget is not a backstop")
        self.assertIsNone(transfer_repository.find_by_key(self.owner.id, key))
        self.assertEqual(self.balance(self.owner), OPENING)
        self.assertEqual(self.balance(self.friend), 0)
        self.assertEqual(len(self.transfer_entries(self.owner)), 0)

    def test_no_number_of_lost_races_answers_with_a_server_error(self):
        """A lost race has no third answer, at any level of contention.

        Each key below is raced a different number of times, from none to well past the budget,
        against both kinds of competitor: one that keeps the key, and one that gives it back.
        Every one of them must resolve to a transfer or to a refusal the caller may repeat, and
        none may surface a raw database error. What the database refuses stays the only thing
        that decides any of this.
        """
        rivals = [0, 1, MAX_KEY_CLAIM_ATTEMPTS, MAX_KEY_CLAIM_ATTEMPTS + 1, 8]
        budget = MAX_KEY_CLAIM_ATTEMPTS + 1

        for rival in rivals:
            for holds_the_key in (True, False):
                with self.subTest(refusals=rival, holds_the_key=holds_the_key):
                    key = services.batch_key(ObjectId(), 0)
                    attempts = []

                    def lose_n(values, rival=rival, holds_the_key=holds_the_key):
                        attempts.append(values)

                        if len(attempts) <= rival:
                            if holds_the_key:
                                # A competitor that keeps the key: its transfer is the answer.
                                REAL_CREATE_TRANSFER(values)
                            else:
                                # A competitor that gives the key back: nothing is the answer.
                                self.fail_the_key(values)

                            raise NotUniqueError(f"lost the insert, {rival} refusals")

                        return REAL_CREATE_TRANSFER(values)

                    payload = None
                    created = False

                    try:
                        with patch("apps.transfers.services.repository.create_transfer", lose_n):
                            payload, created = self.send(key)
                    except NotUniqueError as error:  # pragma: no cover - the defect this pins
                        self.fail(f"{rival} refusals answered with a database error: {error!r}")
                    except AppError as refusal:
                        # Only the terminal contention refusal is acceptable here, and only from
                        # a competitor that keeps handing the key back.
                        self.assertFalse(holds_the_key)
                        self.assertEqual(refusal.status_code, 409)
                        self.assertEqual(refusal.code, IDEMPOTENCY_KEY_CONTENDED_CODE)
                        self.assertLessEqual(len(attempts), budget)
                        self.assertIsNone(transfer_repository.find_by_key(self.owner.id, key))
                        continue

                    self.assertLessEqual(len(attempts), budget)

                    # Zero refusals means no competitor at all, which is the control and the only
                    # case where the shape of the rival says nothing about the outcome.
                    if rival == 0:
                        self.assertTrue(created)
                    elif holds_the_key:
                        self.assertFalse(
                            created, "a key already held is answered from, never claimed again"
                        )
                        self.assertTrue(payload.get("idempotentReplay"))
                    else:
                        self.assertTrue(created, "a released key is free and must be spendable")

        # Whatever the contention, every key either settled once or never settled at all.
        settled = self.settled()
        self.assertLessEqual(settled, len(rivals) * 2)
        self.assertEqual(
            Transfer.objects(__raw__={"state": "COMPLETED"}).count(),
            settled,
            "each raced key settles at most once",
        )
        self.assertEqual(len(self.transfer_entries(self.owner)), settled)
        self.assertEqual(self.balance(self.owner), OPENING - 50000 * settled)
        self.assertEqual(self.balance(self.friend), 50000 * settled)

    def test_a_raced_key_that_settles_moves_money_exactly_once(self):
        """Losing every racing insert, then reclaiming the released key, leaves one movement."""
        key = services.batch_key(ObjectId(), 0)
        create_transfer = transfer_repository.create_transfer
        attempts = []

        def lose_the_last_insert(values):
            # Refused on every racing insert, with the key genuinely released each time, so the
            # only thing standing between this request and a crash is the last insert being
            # allowed to spend a key that is free by then.
            attempts.append(values)

            if len(attempts) <= MAX_KEY_CLAIM_ATTEMPTS:
                self.fail_the_key(values)

                raise NotUniqueError("lost the insert to a request that failed on the key")

            return create_transfer(values)

        with patch("apps.transfers.services.repository.create_transfer", lose_the_last_insert):
            payload, created = self.send(key)

        self.assertTrue(created)
        self.assertEqual(payload["transfer"]["state"], "COMPLETED")
        self.assertEqual(len(attempts), MAX_KEY_CLAIM_ATTEMPTS + 1)
        self.assertEqual(
            Transfer.objects(__raw__={"state": "COMPLETED"}).count(), 1, "one settled transfer"
        )
        self.assertEqual(
            Transfer.objects(__raw__={"state": "FAILED"}).count(),
            MAX_KEY_CLAIM_ATTEMPTS,
            "the losing attempts left their own records and moved nothing",
        )
        self.assertEqual(len(self.transfer_entries(self.owner)), 1, "one debit")
        self.assertEqual(len(self.transfer_entries(self.friend)), 1, "one credit")
        self.assertEqual(self.balance(self.owner), OPENING - 50000)
        self.assertEqual(self.balance(self.friend), 50000)

        # The losing attempts each left a transfer of their own behind, and not one of them
        # moved anything, so the total across every wallet is what it was before.
        total = self.system_total()
        self.assertEqual(total, OPENING + 500 + 10 * OPENING, "no money was created or destroyed")

        # And a settled key still answers rather than moving a second time.
        payload, created = self.send(key)

        self.assertFalse(created)
        self.assertTrue(payload.get("idempotentReplay"))
        self.assertEqual(self.balance(self.owner), OPENING - 50000)
        self.assertEqual(len(self.transfer_entries(self.owner)), 1)
