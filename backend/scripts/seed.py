"""Reset the wallet application database to its seeded baseline.

Invoked by `bash setup.sh` on install and on every `bun start`. The reset is
deliberate and total: every collection in the application database is dropped, so ad
hoc validation data cannot survive a restart. Baseline contents are owned by
`seed_data`.
"""

import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wallet_backend.settings")

import django  # noqa: E402

django.setup()

import bcrypt  # noqa: E402
from bson import ObjectId  # noqa: E402
from mongoengine.connection import get_db  # noqa: E402

from apps.auth.models import UserAccount  # noqa: E402
from apps.auth.repository import find_active_by_id  # noqa: E402
from apps.money_requests.constants import APPROVED_STATE, DECLINED_STATE, PENDING_STATE, REQUEST_TTL_DAYS  # noqa: E402
from apps.money_requests.models import MoneyRequest  # noqa: E402
from apps.money_requests.repository import expire_for_account  # noqa: E402
from apps.money_requests.services import approve_request, decline_request  # noqa: E402
from apps.refunds.models import Refund  # noqa: E402
from apps.refunds.models import ensure_refund_indexes  # noqa: E402
from apps.refunds.services import create_refund  # noqa: E402
from apps.shared.mongo import collection, to_dict  # noqa: E402
from apps.transfers.constants import COMPLETED_STATE  # noqa: E402
from apps.transfers.models import Transfer  # noqa: E402
from apps.transfers.models import ensure_transfer_indexes  # noqa: E402
from apps.transfers.services import execute_transfer  # noqa: E402
from apps.wallet.models import LedgerEntry, Wallet  # noqa: E402
from apps.wallet.repository import derived_balance  # noqa: E402
from apps.wallet.services import create_wallet  # noqa: E402
from scripts import seed_data  # noqa: E402

SEPARATOR = "========================================"

SEEDED_MODELS = (UserAccount, Wallet, LedgerEntry, Transfer, MoneyRequest, Refund)


def connect():
    database = get_db()
    database.command("ping")

    return database


def reset(database):
    existing = sorted(database.list_collection_names())

    for name in existing:
        database.drop_collection(name)

    return existing


def baseline_report(database):
    return [(name, database[name].count_documents({})) for name in seed_data.BASELINE_COLLECTIONS]


def insert_accounts():
    print("\nSeeding sign-in accounts...")
    password_hash = bcrypt.hashpw(seed_data.DEMO_PASSWORD.encode(), bcrypt.gensalt(seed_data.PASSWORD_ROUNDS)).decode()
    accounts = [
        UserAccount(name=row["name"], email=row["email"], password_hash=password_hash, role=row["role"])
        for row in seed_data.ACCOUNT_ROWS
    ]
    UserAccount.objects.insert(accounts)

    print(f"  Created {len(accounts)} accounts")

    return accounts


def insert_wallets(accounts):
    print("\nProvisioning wallets...")
    wallets = [create_wallet(account.id, row["openingBalanceMinor"]) for account, row in zip(accounts, seed_data.ACCOUNT_ROWS)]

    print(f"  Created {len(wallets)} wallets")

    return wallets


def backdate_opening_entries(wallets):
    """Give the ledger a plausible history instead of one identical seed timestamp."""
    seeded_at = datetime.now(timezone.utc)

    for wallet, row in zip(wallets, seed_data.ACCOUNT_ROWS):
        moment = seeded_at - timedelta(days=row["openingDaysAgo"])
        collection(LedgerEntry).update_many({"walletId": wallet["_id"]}, {"$set": {"createdAt": moment, "updatedAt": moment}})

    print(f"  Recorded {LedgerEntry.objects.count()} opening ledger entries")


def validate_wallets(accounts):
    """Every wallet must agree with its own ledger and own all of its entries.

    Balances are read fresh rather than reused from provisioning, because seeded
    transfers have since moved money.
    """
    for account in accounts:
        email = account.email
        wallet = Wallet.objects(__raw__={"userId": account.id}).as_pymongo().first()

        if derived_balance(wallet["_id"]) != wallet["availableBalanceMinor"]:
            raise ValueError(f"Seed ledger does not reconcile with the stored balance for {email}.")

        for entry in LedgerEntry.objects(__raw__={"walletId": wallet["_id"]}).as_pymongo():
            if entry["userId"] != account.id:
                raise ValueError(f"Seed ledger entry is attached to the wrong owner for {email}.")


def validate_roles(accounts):
    """Every seeded account must carry the role it was seeded as, stored rather than assumed.

    A role is only meaningful once it is on the record: the authorization guard reads the
    stored document, and a document written without one is a different thing from a document
    that says what it holds. The roles are also read back through the repository rather than
    off the model instance, because that is the shape a request actually carries.
    """
    for account in accounts:
        stored = find_active_by_id(str(account.id))

        if stored is None:
            raise ValueError(f"Seed account {account.email} was not written.")

        if stored.get("role") != account.role:
            raise ValueError(f"Seed account {account.email} was not stored with the role it was seeded as.")


def backdate_transfer(transfer_id, moment):
    """Move a transfer and the ledger entries it wrote back to the moment it belongs to."""
    for model, query in (
        (Transfer, {"_id": transfer_id}),
        (LedgerEntry, {"transactionId": transfer_id}),
    ):
        collection(model).update_many(query, {"$set": {"createdAt": moment, "updatedAt": moment}})

    collection(Transfer).update_one({"_id": transfer_id}, {"$set": {"completedAt": moment}})


def insert_transfers(accounts):
    """Run the real transfer service so the seeded history is one a request could produce."""
    seeded_at = datetime.now(timezone.utc)
    # The service takes the same account document a request carries, so the documents
    # built for insertion are handed over in that shape.
    by_email = {account.email: to_dict(account) for account in accounts}
    transfers = []

    for row in seed_data.TRANSFER_ROWS:
        # The seed sends no idempotency key, so each seeded transfer is its own operation.
        result, _ = execute_transfer(by_email[row["senderEmail"]], row["recipientEmail"], row["amountMinor"], None)
        transfer_id = ObjectId(result["transfer"]["id"])
        moment = seeded_at - timedelta(days=row["daysAgo"])
        backdate_transfer(transfer_id, moment)
        transfers.append(transfer_id)

    print(f"  Sent {len(transfers)} transfers")

    return transfers


def validate_transfers(transfers):
    """A seeded transfer must be complete, recorded on both wallets, and value-preserving."""
    for transfer_id in transfers:
        transfer = Transfer.objects(__raw__={"_id": transfer_id}).as_pymongo().first()

        if transfer["state"] != COMPLETED_STATE:
            raise ValueError(f"Seed transfer {transfer_id} did not complete.")

        legs = LedgerEntry.objects(__raw__={"transactionId": transfer_id}).as_pymongo()

        if len(legs) != 2:
            raise ValueError(f"Seed transfer {transfer_id} did not record both ledger entries.")

        moved = sum(leg["amountMinor"] for leg in legs if leg["direction"] == "DEBIT")
        received = sum(leg["amountMinor"] for leg in legs if leg["direction"] == "CREDIT")

        if moved != received or moved != transfer["amountMinor"]:
            raise ValueError(f"Seed transfer {transfer_id} did not conserve value.")

        for leg in legs:
            expected_user = transfer["senderUserId"] if leg["direction"] == "DEBIT" else transfer["recipientUserId"]

            if leg["userId"] != expected_user:
                raise ValueError(f"Seed transfer {transfer_id} recorded a leg for the wrong owner.")


def insert_requests(accounts, transfers):
    """Seed requests through the real services, so the baseline cannot disagree with them.

    An approved request is approved for real and therefore pays through the transfer engine,
    so its transfer joins the validated history instead of being a figure written to look like
    a payment. A declined request is declined for real. An expired one is left for the lazy
    expiry sweep to find, which is the same sweep that expires one in the application. Each
    row is backdated afterwards, so the baseline reads as a sequence of events rather than as
    everything having happened at once.
    """
    seeded_at = datetime.now(timezone.utc)
    by_email = {account.email: to_dict(account) for account in accounts}
    seeded = []

    for row in seed_data.REQUEST_ROWS:
        requester = by_email[row["requesterEmail"]]
        approver = by_email[row["approverEmail"]]
        moment = seeded_at - timedelta(days=row["daysAgo"])
        request = MoneyRequest(
            requester_user_id=requester["_id"],
            approver_user_id=approver["_id"],
            amount_minor=row["amountMinor"],
            note=row["note"],
            state=PENDING_STATE,
            expires_at=moment + timedelta(days=REQUEST_TTL_DAYS),
        ).save()

        if row["state"] == APPROVED_STATE:
            result = approve_request(approver, str(request.id))
            transfer_id = ObjectId(result["transfer"]["id"])
            transfers.append(transfer_id)
            backdate_transfer(transfer_id, moment)
        elif row["state"] == DECLINED_STATE:
            decline_request(approver, str(request.id))
        else:
            # Only the expired row's window has closed, and only the account it is addressed
            # to can find it. That is exactly how a request expires in the application.
            expire_for_account(approver["_id"], seeded_at)

        backdate_request(request.id, moment)
        seeded.append((request.id, row["state"]))

    print(f"  Created {len(seeded)} money requests")

    return seeded


def backdate_request(request_id, moment):
    """Move a seeded request back into the history it belongs to.

    `resolvedAt` moves with it when the request was answered, so an answered request reads as
    answered when it was rather than when the seed happened to run.
    """
    stored = MoneyRequest.objects(__raw__={"_id": request_id}).as_pymongo().first()
    changes = {"createdAt": moment, "updatedAt": moment}

    if stored.get("resolvedAt"):
        changes["resolvedAt"] = moment

    collection(MoneyRequest).update_one({"_id": request_id}, {"$set": changes})


def insert_refunds(accounts, transfers):
    """Seed refunds through the real refund service, so they cannot disagree with it.

    A refund pays from the original transfer's recipient, which is whose wallet it debits,
    and each is backdated after like every other seeded event. Refund transfers join the
    validated transfer history rather than being figures written to look like payments.
    """
    seeded_at = datetime.now(timezone.utc)
    by_email = {account.email: to_dict(account) for account in accounts}
    seeded = []

    for row in seed_data.REFUND_ROWS:
        sender = by_email[row["originalSenderEmail"]]
        recipient = by_email[row["originalRecipientEmail"]]
        original = Transfer.objects(
            __raw__={"senderUserId": sender["_id"], "recipientUserId": recipient["_id"], "amountMinor": row["originalAmountMinor"]}
        ).as_pymongo().first()

        if original is None:
            raise ValueError(f"Seed refund row '{row['note']}' names no seeded transfer.")

        result, _ = create_refund(recipient, str(original["_id"]), row["refundAmountMinor"], row["note"], None)
        refund_transfer_id = ObjectId(result["transfer"]["id"])
        transfers.append(refund_transfer_id)
        moment = seeded_at - timedelta(days=row["daysAgo"])
        backdate_transfer(refund_transfer_id, moment)
        backdate_refund(result["refund"]["id"], moment)
        seeded.append(result["refund"]["id"])

    print(f"  Created {len(seeded)} refunds")

    return seeded


def backdate_refund(refund_id, moment):
    stored = Refund.objects(__raw__={"_id": ObjectId(refund_id)}).as_pymongo().first()
    changes = {"createdAt": moment, "updatedAt": moment}

    if stored.get("completedAt"):
        changes["completedAt"] = moment

    collection(Refund).update_one({"_id": ObjectId(refund_id)}, {"$set": changes})


def validate_refunds(seeded):
    """A seeded refund must be completed, paid by a refund transfer of the right original,
    and inside the original's refundable amount when all of its siblings are counted."""
    by_original = {}

    for refund_id in seeded:
        refund = Refund.objects(__raw__={"_id": ObjectId(refund_id)}).as_pymongo().first()

        if refund["state"] != COMPLETED_STATE:
            raise ValueError(f"Seed refund {refund_id} did not complete.")

        refund_transfer = Transfer.objects(__raw__={"_id": refund["transferId"]}).as_pymongo().first()

        if refund_transfer is None or refund_transfer["state"] != COMPLETED_STATE:
            raise ValueError(f"Seed refund {refund_id} is recorded without a completed transfer.")

        if refund_transfer.get("refundOf") != refund["originalTransferId"]:
            raise ValueError(f"Seed refund {refund_id} points at a transfer that did not pay it back.")

        if refund_transfer["amountMinor"] != refund["amountMinor"]:
            raise ValueError(f"Seed refund {refund_id} and its transfer disagree on amount.")

        legs = LedgerEntry.objects(__raw__={"transactionId": refund["transferId"]}).as_pymongo()

        if {(leg["direction"], leg["kind"]) for leg in legs} != {("DEBIT", "REFUND_OUT"), ("CREDIT", "REFUND_IN")}:
            raise ValueError(f"Seed refund {refund_id} did not record REFUND_OUT and REFUND_IN legs.")

        by_original.setdefault(refund["originalTransferId"], []).append(refund["amountMinor"])

    for original_id, amounts in by_original.items():
        original = Transfer.objects(__raw__={"_id": original_id}).as_pymongo().first()

        if sum(amounts) > original["amountMinor"]:
            raise ValueError(f"Seed refunds exceed the original transfer {original_id}.")

        if (original.get("refundedAmountMinor") or 0) != sum(amounts):
            raise ValueError(f"Seed refund counter on transfer {original_id} drifted from the completed refunds.")


def validate_requests(seeded):
    """A seeded request must be in the state it was seeded as, and must name the right transfer.

    Only an approved request may name a transfer at all, and it has to be the transfer that
    was really created for it: sent by the account being asked, sent to the account that
    asked, for the amount that was asked for. That is what stops a declined or expired request
    in the baseline from ever claiming a payment nobody made.
    """
    for request_id, expected_state in seeded:
        request = MoneyRequest.objects(__raw__={"_id": request_id}).as_pymongo().first()

        if request["state"] != expected_state:
            raise ValueError(f"Seed money request {request_id} is {request['state']}, expected {expected_state}.")

        if expected_state != APPROVED_STATE:
            if request.get("transferId"):
                raise ValueError(f"Seed money request {request_id} is {expected_state} but names a transfer.")

            continue

        transfer = Transfer.objects(__raw__={"_id": request["transferId"]}).as_pymongo().first()

        if transfer is None or transfer["state"] != COMPLETED_STATE:
            raise ValueError(f"Seed money request {request_id} is approved without a completed transfer.")

        sent, received = transfer["senderUserId"], transfer["recipientUserId"]

        if sent != request["approverUserId"] or received != request["requesterUserId"]:
            raise ValueError(f"Seed money request {request_id} was approved by a transfer that paid the wrong way round.")

        if transfer["amountMinor"] != request["amountMinor"]:
            raise ValueError(f"Seed money request {request_id} was approved by a transfer of a different amount.")


def validate_system_total():
    """Transfers move money between wallets, so the sum of all balances must hold still."""
    wallets = Wallet.objects.as_pymongo()
    total = sum(wallet["availableBalanceMinor"] for wallet in wallets)
    opening = sum(row["openingBalanceMinor"] for row in seed_data.ACCOUNT_ROWS)

    if total != opening:
        raise ValueError("Seed transfers changed the total money in the system.")


def ensure_indexes():
    for model in SEEDED_MODELS:
        model.ensure_indexes()

    # The idempotency index is partial and MongoEngine cannot declare one, so the transfers
    # model creates that one itself. Without it a repeated key would be honoured twice.
    ensure_transfer_indexes()
    ensure_refund_indexes()


def seed():
    print(SEPARATOR)
    print("Database Seeding")
    print(f"{SEPARATOR}\n")

    database = connect()
    print(f"Connected to MongoDB database '{database.name}'")

    dropped = reset(database)
    print(f"Cleared {len(dropped)} collection(s)" + (f": {', '.join(dropped)}" if dropped else ""))

    accounts = insert_accounts()
    wallets = insert_wallets(accounts)
    backdate_opening_entries(wallets)
    transfers = insert_transfers(accounts)
    requests = insert_requests(accounts, transfers)
    refunds = insert_refunds(accounts, transfers)
    validate_transfers(transfers)
    validate_requests(requests)
    validate_refunds(refunds)
    validate_wallets(accounts)
    validate_roles(accounts)
    validate_system_total()
    ensure_indexes()

    print("\nCollection counts:")

    for name, count in baseline_report(database):
        print(f"  {name}: {count}")

    print("\nDemo accounts:")

    for row in seed_data.ACCOUNT_ROWS:
        print(f"  Email: {row['email']} | Password: {seed_data.DEMO_PASSWORD} | Role: {row['role']}")

    print(f"\n{SEPARATOR}")
    print("Seeding completed successfully!")
    print(f"{SEPARATOR}\n")


def main():
    try:
        seed()
    except Exception as error:
        print(f"\nSeeding failed: {error}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
