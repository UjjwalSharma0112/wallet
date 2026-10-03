"""R2 — the HTTP surface of a P2P transfer.

These tests pin the contract a client depends on: the status code for each outcome, the
error code and shape, and the fact that the caller's own wallet comes back with the
transfer so the interface never has to guess a balance.
"""

from bson import ObjectId

from apps.transfers.models import Transfer
from apps.wallet import repository

from .base import MongoTestCase

TRANSFERS_PATH = "/api/v1/transfers"
HISTORY_PATH = "/api/v1/wallet/transactions"

SENDER_EMAIL = "aarav.mehta@wallet.demo"
RECIPIENT_EMAIL = "fatima.noor@wallet.demo"


class TransferApiTest(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.sender = self.create_account(name="Aarav Mehta", email=SENDER_EMAIL, opening_balance_minor=245000)
        self.recipient = self.create_account(name="Fatima Noor", email=RECIPIENT_EMAIL, opening_balance_minor=12850)
        self.token = self.token_for(SENDER_EMAIL)

    def send(self, body, token=None):
        # An empty token signs the request out, so it is checked against None explicitly.
        return self.api_client(self.token if token is None else token).post(
            TRANSFERS_PATH, body, content_type="application/json"
        )

    def error_of(self, response):
        return response.json()["error"]

    def field_errors(self, response):
        return self.error_of(response)["details"]["fieldErrors"]

    def test_sending_money_returns_the_transfer_and_the_new_balance(self):
        response = self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 50000})

        self.assertEqual(response.status_code, 201)

        data = response.json()["data"]

        self.assertEqual(data["transfer"]["state"], "COMPLETED")
        self.assertEqual(data["transfer"]["amountMinor"], 50000)
        self.assertEqual(data["transfer"]["role"], "SENDER")
        self.assertEqual(data["transfer"]["senderUserId"], str(self.sender.id))
        self.assertEqual(data["transfer"]["recipientUserId"], str(self.recipient.id))
        self.assertEqual(data["wallet"]["availableBalanceMinor"], 195000)
        self.assertIsNotNone(data["transfer"]["completedAt"])

    def test_the_transfer_carries_the_note_it_was_given(self):
        response = self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 50000, "note": "Rent share"})

        self.assertEqual(response.json()["data"]["transfer"]["note"], "Rent share")

    def test_the_transfer_is_created_without_a_note_when_none_is_given(self):
        response = self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 50000})

        self.assertIsNone(response.json()["data"]["transfer"]["note"])

    def test_the_transfer_and_transaction_identifiers_are_the_same_value(self):
        transfer = self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 50000}).json()["data"]["transfer"]

        # One transfer is one ledger transaction, so a client can tie the two together
        # with a single identifier.
        self.assertEqual(transfer["id"], transfer["transactionId"])
        self.assertEqual(ObjectId(transfer["transactionId"]).binary, ObjectId(transfer["id"]).binary)

    def test_the_route_requires_authentication(self):
        response = self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 50000}, token="")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.error_of(response)["code"], "AUTH_REQUIRED")

    def test_a_rejected_token_is_refused(self):
        response = self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 50000}, token="not-a-token")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.error_of(response)["code"], "INVALID_TOKEN")

    def test_the_route_only_accepts_posts(self):
        response = self.api_client(self.token).get(TRANSFERS_PATH)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.error_of(response)["code"], "METHOD_NOT_ALLOWED")

    def test_spending_more_than_the_balance_is_refused(self):
        response = self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 245001})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_of(response)["code"], "INSUFFICIENT_FUNDS")
        self.assertEqual(self.balance(self.sender), 245000)
        self.assertEqual(self.balance(self.recipient), 12850)

    def test_sending_to_an_unknown_email_is_refused(self):
        response = self.send({"recipientEmail": "nobody@wallet.demo", "amountMinor": 100})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.error_of(response)["code"], "RECIPIENT_NOT_FOUND")

    def test_sending_to_yourself_is_a_field_error_on_the_recipient(self):
        response = self.send({"recipientEmail": SENDER_EMAIL, "amountMinor": 100})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["code"], "VALIDATION_ERROR")
        self.assertIn("recipientEmail", self.field_errors(response))
        self.assertEqual(self.balance(self.sender), 245000)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_an_account_without_a_wallet_is_reported_as_missing(self):
        broke = self.create_account(email="grace.obi@wallet.demo", provision=False)

        response = self.send(
            {"recipientEmail": broke.email, "amountMinor": 100}, token=self.token_for(broke.email)
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.error_of(response)["code"], "WALLET_NOT_FOUND")

    def balance(self, account):
        return repository.find_wallet_by_user_id(account.id)["availableBalanceMinor"]

    def report_every_rejected_body(self):
        """Each body a client could plausibly send, with the field it should be blamed on."""
        return [
            ({}, "amountMinor"),
            ({"amountMinor": 100}, "recipientEmail"),
            ({"recipientEmail": RECIPIENT_EMAIL}, "amountMinor"),
            ({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": None}, "amountMinor"),
            ({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": True}, "amountMinor"),
            ({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": False}, "amountMinor"),
            ({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 0}, "amountMinor"),
            ({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": -500}, "amountMinor"),
            ({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 12.5}, "amountMinor"),
            ({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": "many"}, "amountMinor"),
            ({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 10_000_001}, "amountMinor"),
            ({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 100, "note": 42}, "note"),
            ({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 100, "note": "x" * 141}, "note"),
            ({"recipientEmail": "not-an-email", "amountMinor": 100}, "recipientEmail"),
            ({"recipientEmail": "", "amountMinor": 100}, "recipientEmail"),
            ({"recipientEmail": None, "amountMinor": 100}, "recipientEmail"),
        ]

    def test_every_malformed_body_is_rejected_with_the_field_it_came_from(self):
        for body, field in self.report_every_rejected_body():
            with self.subTest(body=body):
                response = self.send(body)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.error_of(response)["code"], "VALIDATION_ERROR")
                self.assertIn(field, self.field_errors(response))
                self.assertEqual(self.error_of(response)["details"]["formErrors"], [])

    def test_a_rejected_body_never_moves_money(self):
        for body, _ in self.report_every_rejected_body():
            self.send(body)

        self.assertEqual(self.balance(self.sender), 245000)
        self.assertEqual(self.balance(self.recipient), 12850)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_a_boolean_amount_is_refused_rather_than_read_as_one_paise(self):
        response = self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": True})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.balance(self.recipient), 12850)

    def test_an_amount_written_as_a_string_is_read_as_that_number(self):
        # The shared number reader accepts a numeric string and rejects anything that is
        # not a whole number, so "50000" is the same request as 50000.
        response = self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": "50000"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["data"]["transfer"]["amountMinor"], 50000)

    def test_both_fields_are_reported_when_both_are_wrong(self):
        response = self.send({"recipientEmail": "nope", "amountMinor": -1})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(self.field_errors(response)), {"recipientEmail", "amountMinor"})

    def test_the_smallest_amount_the_api_accepts_is_one_paise(self):
        response = self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 1})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.balance(self.recipient), 12851)

    def test_the_largest_amount_the_api_accepts_is_its_documented_cap(self):
        response = self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 10_000_000})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_of(response)["code"], "INSUFFICIENT_FUNDS")

    def test_the_same_request_sent_twice_without_a_key_makes_two_transfers(self):
        # The idempotency key is optional, so a client that does not send one is left exactly
        # as R2 left it: each POST is a separate instruction and both are honoured. R3 covers
        # the client that does send a key, in tests/test_idempotency.py.
        body = {"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 1000}

        first = self.send(body)
        second = self.send(body)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(
            first.json()["data"]["transfer"]["id"], second.json()["data"]["transfer"]["id"]
        )
        self.assertEqual(self.balance(self.sender), 243000)
        self.assertEqual(self.balance(self.recipient), 14850)
        self.assertEqual(Transfer.objects.count(), 2)


class TransferHistoryApiTest(MongoTestCase):
    def setUp(self):
        super().setUp()

        self.sender = self.create_account(name="Aarav Mehta", email=SENDER_EMAIL, opening_balance_minor=245000)
        self.recipient = self.create_account(name="Fatima Noor", email=RECIPIENT_EMAIL, opening_balance_minor=12850)
        self.sender_token = self.token_for(SENDER_EMAIL)
        self.recipient_token = self.token_for(RECIPIENT_EMAIL)

    def send(self, body, token=None):
        return self.api_client(token or self.sender_token).post(
            TRANSFERS_PATH, body, content_type="application/json"
        )

    def history(self, token):
        return self.api_client(token).get(HISTORY_PATH).json()["data"]["entries"]

    def test_the_senders_history_names_the_recipient_and_their_role(self):
        self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 50000, "note": "Rent share"})

        entry = self.history(self.sender_token)[0]

        self.assertEqual(entry["kind"], "TRANSFER_OUT")
        self.assertEqual(entry["direction"], "DEBIT")
        self.assertEqual(entry["amountMinor"], 50000)
        self.assertEqual(entry["balanceAfterMinor"], 195000)
        self.assertEqual(
            entry["counterparty"],
            {
                "userId": str(self.recipient.id),
                "name": "Fatima Noor",
                "role": "SENDER",
                "note": "Rent share",
            },
        )

    def test_the_recipients_history_names_the_sender_and_their_role(self):
        self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 50000})

        entry = self.history(self.recipient_token)[0]

        self.assertEqual(entry["kind"], "TRANSFER_IN")
        self.assertEqual(entry["direction"], "CREDIT")
        self.assertEqual(entry["amountMinor"], 50000)
        self.assertEqual(entry["balanceAfterMinor"], 62850)
        self.assertEqual(entry["counterparty"]["name"], "Aarav Mehta")
        self.assertEqual(entry["counterparty"]["role"], "RECIPIENT")

    def test_both_parties_see_the_same_entry_under_the_same_transaction(self):
        transfer = self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 50000}).json()["data"]["transfer"]

        sent = self.history(self.sender_token)[0]
        received = self.history(self.recipient_token)[0]

        self.assertEqual(sent["transactionId"], transfer["transactionId"])
        self.assertEqual(received["transactionId"], transfer["transactionId"])
        self.assertEqual(sent["amountMinor"], received["amountMinor"])

    def test_an_opening_entry_is_shown_without_a_counterparty(self):
        entry = self.history(self.sender_token)[0]

        self.assertEqual(entry["kind"], "OPENING")
        self.assertNotIn("counterparty", entry)

    def test_history_is_ordered_newest_first_across_several_transfers(self):
        self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 1000})
        self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 2000})
        self.send({"recipientEmail": RECIPIENT_EMAIL, "amountMinor": 3000})

        entries = self.history(self.sender_token)

        self.assertEqual([entry["amountMinor"] for entry in entries[:3]], [3000, 2000, 1000])
        self.assertEqual([entry["balanceAfterMinor"] for entry in entries[:3]], [239000, 242000, 244000])

    def test_a_transfer_between_other_accounts_never_appears_in_a_third_party_history(self):
        self.create_account(name="Diego Santos", email="diego.santos@wallet.demo", opening_balance_minor=965000)
        diego_token = self.token_for("diego.santos@wallet.demo")

        self.api_client(diego_token).post(
            TRANSFERS_PATH,
            {"recipientEmail": "aarav.mehta@wallet.demo", "amountMinor": 150000},
            content_type="application/json",
        )

        aarav_entries = self.history(self.sender_token)

        self.assertEqual(aarav_entries[0]["kind"], "TRANSFER_IN")
        self.assertEqual(aarav_entries[0]["counterparty"]["name"], "Diego Santos")
        self.assertEqual(len(aarav_entries), 2)
