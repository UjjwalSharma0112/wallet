"""Mutation testing for R7 batch processing and R11 analytics.

A test that passes only because the code is right is worth nothing until the code is wrong and
the test says so. Each mutation below makes one of the guarantees these features rely on false
— in the smallest way that makes it false — and the whole suite is then run. A mutation that
the suite still passes is not a safe mutation, it is a guarantee nothing is holding up.

Every mutation is reverted afterwards, and the suite is run once more to prove the revert.

Two guarantees have no mutation here, and it would be dishonest to invent one. The figures are
summed in the database rather than in the client, which is a fact about where the code is rather
than about what it computes; there is no JavaScript test runner in this repository and adding one
would mean adding a dependency. And a refused transfer cannot appear in the totals because the
pipeline reads the ledger and a refused transfer writes no entry — mutation 3 breaks that by
reading the wrong collection, which is the only way to break it here.

Run it from `backend/` with `../.venv/bin/python scripts/mutation_check.py`.
"""

import pathlib
import shutil
import subprocess
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
PYTHON = BACKEND.parent / ".venv" / "bin" / "python"

MUTATIONS = [
    {
        "name": "1. Drop the deterministic item key",
        "file": "apps/batch/services.py",
        "old": 'return f"{BATCH_KEY_PREFIX}{batch_id}:{position}"',
        "new": 'return f"{BATCH_KEY_PREFIX}{now_utc().isoformat()}"',
        "why": (
            "The key would then be minted per attempt instead of derived, so a failed item "
            "could not be tried again under the name that freed it, and two attempts at one "
            "item would be two different operations rather than one."
        ),
    },
    {
        "name": "2. Allow duplicate processing",
        "file": "apps/batch/repository.py",
        "old": 'conditions = {"_id": as_object_id(batch_id), "state": {"$in": PROCESSABLE_STATES}}',
        "new": 'conditions = {"_id": as_object_id(batch_id)}',
        "why": (
            "The claim would no longer be conditional on the batch being processable, so a "
            "batch somebody is already processing could be taken by a second run."
        ),
    },
    {
        "name": "3. Stop after the first failed item",
        "file": "apps/batch/services.py",
        "old": '            settle_item(claimed["_id"], item, failed=error)\n\n            continue',
        "new": '            settle_item(claimed["_id"], item, failed=error)\n\n            break',
        "why": (
            "One refusal would end the run, so the payments after it would never be attempted "
            "even though the batch could still pay them."
        ),
    },
    {
        "name": "4. Bypass ownership",
        "file": "apps/batch/services.py",
        "old": (
            '    if batch["ownerUserId"] != account["_id"]:\n'
            "        raise AppError(403, BATCH_ACTION_NOT_ALLOWED_CODE, BATCH_ACTION_NOT_ALLOWED_MESSAGE)"
        ),
        "new": "    pass",
        "why": (
            "Any signed-in account could run any other account's batch, spending its owner's "
            "money."
        ),
    },
    {
        "name": "5. Allow a negative balance",
        "file": "apps/wallet/repository.py",
        "old": "    if require_balance:\n        conditions[\"availableBalanceMinor\"] = {\"$gte\": abs(delta)}\n",
        "new": "",
        "why": (
            "The debit would no longer be refused when the wallet cannot cover it, so money "
            "would be created rather than moved and the total would stop being conserved."
        ),
    },
    {
        "name": "6. Count the opening credit as money received",
        "file": "apps/analytics/constants.py",
        "old": "RECEIVED_KINDS = [TRANSFER_IN_KIND, REFUND_IN_KIND]",
        "new": 'RECEIVED_KINDS = [TRANSFER_IN_KIND, REFUND_IN_KIND, "OPENING"]',
        "why": (
            "The opening credit funds a wallet rather than being paid into it by anybody, so "
            "counting it would report the funding as activity and net movement would no "
            "longer be the change the wallet's own ledger recorded."
        ),
    },
    {
        "name": "7. Ignore whose ledger is being summed",
        "file": "apps/analytics/repository.py",
        "old": '                "walletId": ObjectId(wallet_id),\n',
        "new": "",
        "why": (
            "Every wallet's movements would be added together, so an account would read the "
            "whole system's activity as its own — including money it never touched."
        ),
    },
    {
        "name": "8. Read the totals from transfer rows",
        "file": "apps/analytics/repository.py",
        "old": "from apps.wallet.models import LedgerEntry",
        "new": "from apps.transfers.models import Transfer as LedgerEntry",
        "why": (
            "A transfer row also exists for operations that were attempted and failed, so "
            "reading totals from it mixes a population that moved money with one that did "
            "not. In its crudest form — the collections having no fields in common — the "
            "figures collapse rather than come out subtly wrong, and the arithmetic tests "
            "catch that."
        ),
    },
    {
        "name": "9. Leave out the days nothing happened on",
        "file": "apps/analytics/services.py",
        "old": '"volume": [bucket_row(label, buckets.get(label)) for label in period_days(from_date, to_date)],',
        "new": '"volume": [bucket_row(label, buckets[label]) for label in buckets],',
        "why": (
            "The series would cover only the days that had entries, so a caller would have to "
            "infer the gaps and a chart could not tell an absent day from a day of no height."
        ),
    },
    {
        "name": "10. Take a total the caller supplied",
        "file": "apps/analytics/views.py",
        "old": (
            '    return Response({"data": services.account_analytics(str(request.account["_id"]), from_date, to_date)})'
        ),
        "new": (
            "    figures = services.account_analytics(str(request.account[\"_id\"]), from_date, to_date)\n\n"
            '    return Response({"data": {**figures, "sentMinor": int(request.query_params.get("sentMinor", figures["sentMinor"]))}})'
        ),
        "why": (
            "The response would become a function of the query rather than of the ledger, "
            "which is what a client-side total looks like once it reaches the server: any "
            "caller could name any figure."
        ),
    },
    {
        "name": "11. Put the last day of a period outside it",
        "file": "apps/analytics/repository.py",
        "old": "return datetime.combine(to_date + timedelta(days=1), MIDNIGHT, tzinfo=timezone.utc)",
        "new": "return datetime.combine(to_date, MIDNIGHT, tzinfo=timezone.utc)",
        "why": (
            "The inclusive endpoint would become exclusive, so the day a caller named as the "
            "end of a period would report nothing at all."
        ),
    },
    {
        "name": "12. Cut the day boundaries in the reader's timezone",
        "file": "apps/analytics/repository.py",
        "old": '"timezone": BUCKET_TIME_ZONE}',
        "new": '"timezone": "Asia/Kolkata"}',
        "why": (
            "A bucket would be a local day rather than a UTC one, so the same movement would "
            "be reported in a different bucket depending on the server, and a movement at "
            "midnight would fall in the previous day."
        ),
    },
]


def run_suite():
    result = subprocess.run(
        [str(PYTHON), "manage.py", "test"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )

    return result.returncode, result.stdout + result.stderr


def count_failures(output):
    failures = sum(1 for line in output.splitlines() if line.startswith(("FAIL:", "ERROR:")))

    return failures


def main():
    print("Baseline: the suite must pass before any mutation means anything.\n")

    code, output = run_suite()

    if code != 0:
        print("  The suite already fails on an unmutated tree. Fix that first.\n")
        print("\n".join(output.splitlines()[-40:]))

        return 1

    print("  Passes.\n")

    survivors = []

    for mutation in MUTATIONS:
        path = BACKEND / mutation["file"]
        backup = path.read_text()

        print(f"{mutation['name']}")
        print(f"  {mutation['why']}")
        print(f"  {mutation['file']}\n")

        if backup.count(mutation["old"]) != 1:
            print(f"  SKIPPED: the line to change is not in the file exactly once "
                  f"({backup.count(mutation['old'])} matches). A mutation that does not apply "
                  f"proves nothing.\n")
            survivors.append(mutation["name"])

            continue

        try:
            path.write_text(backup.replace(mutation["old"], mutation["new"], 1))
            code, output = run_suite()
        finally:
            path.write_text(backup)

        failures = count_failures(output)

        if code == 0 or failures == 0:
            print("  NOT CAUGHT. The suite still passed, so this guarantee is not tested.\n")
            survivors.append(mutation["name"])
        else:
            caught = [line for line in output.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
            print(f"  Caught by {failures} failing test(s). For example:")

            for line in caught[:3]:
                print(f"    {line}")

            print()

    code, output = run_suite()

    print(f"Reverted: the suite is {'still passing' if code == 0 else 'NOT passing'}.")

    if code != 0:
        survivors.append("the revert did not restore a passing suite")

    if survivors:
        print("\nUNSAFE — the suite did not catch:")
        for name in survivors:
            print(f"  {name}")

        return 1

    print(f"\nAll {len(MUTATIONS)} mutations were caught, and each was reverted.")

    return 0


if __name__ == "__main__":
    sys.exit(main())