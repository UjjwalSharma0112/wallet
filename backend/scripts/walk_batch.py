"""A live walk through R7 against the seeded database, over the real HTTP stack.

Everything else is asserted in tests against a throwaway database. This is the other half: the
seeded data a person is actually handed, the three real routes, and the balances they leave
behind. It changes the seeded database, so `bash setup.sh --seed` must be run after it.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wallet_backend.settings")

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402

from apps.transfers.models import Transfer  # noqa: E402
from apps.auth.models import UserAccount  # noqa: E402
from apps.wallet.models import LedgerEntry, Wallet  # noqa: E402

BASE = "/api/v1"
AARAV = "aarav.mehta@wallet.demo"
GRACE = "grace.obi@wallet.demo"
PASSWORD = "Wallet@2026"


def show(label, value):
    print(f"  {label}: {value}")


def money(minor):
    return f"Rs {minor / 100:,.2f}"


def wallet_of(email):
    return Wallet.objects(user_id=UserAccount.objects(email=email).first().id).first()


def sign_in(client, email):
    response = client.post(
        f"{BASE}/auth/login", json.dumps({"email": email, "password": PASSWORD}), content_type="application/json"
    )

    assert response.status_code == 200, response.content
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {response.json()['data']['token']}"


def main():
    aarav = Client()
    sign_in(aarav, AARAV)

    before = wallet_of(AARAV)
    print("\n1. The seeded batch, as its owner sees it")
    listing = aarav.get(f"{BASE}/batches").json()["data"]
    show("batches owned by Aarav", listing["total"])
    seeded = listing["batches"][0]
    show("its state", seeded["state"])
    show("its items", [f"{item['recipientEmail']} {money(item['amountMinor'])}" for item in seeded["items"]])
    show("it has paid so far", money(seeded["summary"]["completedAmountMinor"]))

    print("\n2. Nobody else can see it")
    grace = Client()
    sign_in(grace, GRACE)
    show("batches owned by Grace", grace.get(f"{BASE}/batches").json()["data"]["total"])
    show("Grace running Aarav's batch", grace.post(f"{BASE}/batches/{seeded['id']}/processing").status_code)

    print("\n3. Aarav runs it, and the answer is the whole outcome")
    before_total = sum(w["availableBalanceMinor"] for w in Wallet.objects.as_pymongo())
    answer = aarav.post(f"{BASE}/batches/{seeded['id']}/processing").json()["data"]
    show("batch state", answer["batch"]["state"])
    show("summary", answer["batch"]["summary"])
    show("wallet after", money(answer["wallet"]["availableBalanceMinor"]))
    settled = wallet_of(AARAV)
    sent = answer["batch"]["summary"]["completedAmountMinor"]
    agreed = answer["wallet"]["availableBalanceMinor"] == settled.available_balance_minor
    spent = before.available_balance_minor - settled.available_balance_minor

    show("the answer agrees with the wallet now in the database", agreed)
    show("it is less by exactly what the batch sent", spent == sent)
    show("Aarav before the run", money(before.available_balance_minor))

    for item in answer["batch"]["items"]:
        print(f"    {item['recipientEmail']:<28} {money(item['amountMinor']):>14}  {item['state']}")

    print("\n4. Running it again moves nothing")
    after_total = sum(w["availableBalanceMinor"] for w in Wallet.objects.as_pymongo())
    transfers_before = Transfer.objects.count()
    again = aarav.post(f"{BASE}/batches/{seeded['id']}/processing").json()["data"]
    show("summary is unchanged", again["batch"]["summary"] == answer["batch"]["summary"])
    show("balance is unchanged", again["wallet"]["availableBalanceMinor"] == answer["wallet"]["availableBalanceMinor"])
    show("not one transfer was added", Transfer.objects.count() == transfers_before)
    show("transfers in the database, unchanged", transfers_before)

    print("\n5. Conservation, from the ledger rather than the balances")
    debits = LedgerEntry.objects(__raw__={"kind": "TRANSFER_OUT"}).count()
    credits = LedgerEntry.objects(__raw__={"kind": "TRANSFER_IN"}).count()
    show("transfers out / in", f"{debits} / {credits}")
    show("every transfer has both legs", debits == credits)
    show("total money before / after", f"{before_total} / {after_total}")
    show("total conserved", before_total == after_total)

    print("\n6. A fresh batch that cannot be paid in full")
    # An amount this account demonstrably cannot pay, whatever its balance happens to be.
    unaffordable = wallet_of(AARAV).available_balance_minor + 10_000_00
    created = aarav.post(
        f"{BASE}/batches",
        json.dumps(
            {
                "items": [
                    {"recipientEmail": "grace.obi@wallet.demo", "amountMinor": 1000},
                    {"recipientEmail": "diego.santos@wallet.demo", "amountMinor": unaffordable},
                    {"recipientEmail": "fatima.noor@wallet.demo", "amountMinor": 2500},
                ]
            }
        ),
        content_type="application/json",
    )
    show("created", created.status_code)
    partial = aarav.post(f"{BASE}/batches/{created.json()['data']['id']}/processing").json()["data"]
    show("state", partial["batch"]["state"])
    show("summary", partial["batch"]["summary"])

    for item in partial["batch"]["items"]:
        reason = item["failureMessage"] or ""
        print(f"    {item['recipientEmail']:<28} {money(item['amountMinor']):>14}  {item['state']:<10} {reason}")

    show("the item after the failure was paid", partial["batch"]["items"][2]["state"] == "COMPLETED")

    print("\n7. A batch that pays something is not stored if the list is wrong")
    bad = aarav.post(
        f"{BASE}/batches",
        json.dumps({"items": [{"recipientEmail": "not-an-address", "amountMinor": 500}, {"amountMinor": 100}]}),
        content_type="application/json",
    )
    show("status", bad.status_code)
    show("reported against", sorted(bad.json()["error"]["details"]["fieldErrors"]))

    print("\nRe-run `bash setup.sh --seed` to put the baseline back.\n")


if __name__ == "__main__":
    main()