"""R11 — what an account's own activity adds up to.

The figures here are asserted in paise, not merely for being present. A metric that is wrong
but non-zero satisfies a shape test, so every expectation below is arithmetic a reader could
do by hand from the movements the test itself made — and the movements are made by the real
services, so the ledger being summed is the ledger the product wrote.
"""

from datetime import date, datetime, timedelta, timezone

from bson import ObjectId

from apps.analytics import services as analytics_services
from apps.analytics.constants import ACTIVITY_KINDS, MAX_PERIOD_DAYS, RECEIVED_KINDS, SENT_KINDS
from apps.shared.errors import AppError
from apps.shared.mongo import collection
from apps.refunds import services as refund_services
from apps.transfers import services as transfer_services
from apps.transfers.models import Transfer
from apps.wallet import repository as wallet_repository
from apps.wallet.constants import ENTRY_KINDS
from apps.wallet.models import LedgerEntry, Wallet

from .base import MongoTestCase

ANALYTICS_PATH = "/api/v1/analytics"
HISTORY_PATH = "/api/v1/wallet/transactions"
SENDER_EMAIL = "aarav.mehta@wallet.demo"
RECIPIENT_EMAIL = "diego.santos@wallet.demo"
STRANGER_EMAIL = "stranger@wallet.demo"
SUPPLIED_FIGURES = ("sentMinor", "receivedMinor", "netMovementMinor", "transactionCount", "averageAmountMinor", "bucketSize")

# A fixed day to place movements on. Every period a test asks for is named explicitly, so the
# wall clock is never part of what a test expects, and movements are stamped rather than
# waited for — the alternative would be tests that cannot fail for the right reason.
ANCHOR = date(2026, 3, 10)

# Aarav opens with ₹10,000 and pays Diego ₹1,000 then ₹3,000. Diego pays ₹2,000 back and
# refunds ₹300 of the first payment. Aarav therefore sends ₹4,000, receives ₹2,300, and
# ends the period ₹1,300 better off than it started. Diego sends ₹2,300, receives ₹4,000,
# and ends ₹1,700 ahead: the same money, counted from the other wallet.
FIRST_PAYMENT = 100_000
SECOND_PAYMENT = 300_000
DIEGO_PAYS_BACK = 200_000
REFUNDED = 30_000
AARAV_SENT = FIRST_PAYMENT + SECOND_PAYMENT
AARAV_RECEIVED = DIEGO_PAYS_BACK + REFUNDED
AARAV_ENDS_AT = 830_000

SUBJECT_PARAMETERS = ("walletId", "userId", "accountId", "wallet", "user", "account", "email", "subject", "for")


def instant(day_offset, hour=12, minute=0, second=0, microsecond=0):
    """A UTC instant on the day `day_offset` days after the anchor."""
    day = ANCHOR + timedelta(days=day_offset)

    return datetime(day.year, day.month, day.day, hour, minute, second, microsecond, tzinfo=timezone.utc)


class LedgerFixtureTestCase(MongoTestCase):
    """Two accounts, and movements placed on days a test can name.

    Stamping is used because a period's figures are only interesting when the movements fall
    on different days, and the clock cannot be asked to place four transfers on four chosen
    dates. The transfers themselves are real ones: they move balances, they write both legs,
    and the refund goes through the refund service. Only the moment recorded against them is
    moved, so every total below is a sum of entries the product actually wrote.
    """

    def setUp(self):
        super().setUp()

        self.accounts = {
            SENDER_EMAIL: self.create_account(name="Aarav Mehta", email=SENDER_EMAIL, opening_balance_minor=1_000_000),
            RECIPIENT_EMAIL: self.create_account(name="Diego Santos", email=RECIPIENT_EMAIL, opening_balance_minor=0),
        }
        self.aarav = self.accounts[SENDER_EMAIL]
        self.diego = self.accounts[RECIPIENT_EMAIL]
        self.token = self.token_for(SENDER_EMAIL)

    def stamp(self, transaction_id, moment):
        """Record the movements of one transaction as having happened at `moment`."""
        collection(LedgerEntry).update_many(
            {"transactionId": ObjectId(transaction_id)}, {"$set": {"createdAt": moment, "updatedAt": moment}}
        )

    def send(self, sender_email, recipient_email, amount_minor, moment, note="rent share"):
        account = self.account_document(self.accounts[sender_email])
        payload, _ = transfer_services.execute_transfer(account, recipient_email, amount_minor, note)
        self.stamp(payload["transfer"]["transactionId"], moment)

        return payload["transfer"]

    def refund(self, original_transfer_id, amount_minor, moment, note="changed my mind"):
        """Return part of a completed payment, as the account that received it may."""
        account = self.account_document(self.diego)
        payload, _ = refund_services.create_refund(account, original_transfer_id, amount_minor, note, "refund-1")
        self.stamp(payload["refund"]["transferId"], moment)

        return payload["refund"]

    def build_ledger(self):
        """The four movements every calculation test measures."""
        first = self.send(SENDER_EMAIL, RECIPIENT_EMAIL, FIRST_PAYMENT, instant(0))
        self.send(SENDER_EMAIL, RECIPIENT_EMAIL, SECOND_PAYMENT, instant(1))
        self.send(RECIPIENT_EMAIL, SENDER_EMAIL, DIEGO_PAYS_BACK, instant(2))
        self.refund(first["transactionId"], REFUNDED, instant(3))

        return first

    def period_start(self):
        return ANCHOR - timedelta(days=1)

    def period_end(self):
        return ANCHOR + timedelta(days=4)

    def query(self, from_date=None, to_date=None, extra=""):
        named = "".join(
            f"&{field}={day.isoformat()}" for field, day in (("from", from_date), ("to", to_date)) if day is not None
        )

        return f"{ANALYTICS_PATH}?{named.lstrip('&')}{extra}" if named else f"{ANALYTICS_PATH}{extra}"

    def read(self, from_date=None, to_date=None, extra="", email=SENDER_EMAIL):
        response = self.api_client(self.token_for(email)).get(self.query(from_date, to_date, extra))

        self.assertEqual(response.status_code, 200, response.content)

        return response.json()["data"]

    def read_period(self, extra="", email=SENDER_EMAIL):
        return self.read(self.period_start(), self.period_end(), extra, email)

    def balance_of(self, account):
        return wallet_repository.find_wallet_by_user_id(account.id)["availableBalanceMinor"]

    def derived_of(self, account):
        return wallet_repository.derived_balance(wallet_repository.find_wallet_by_user_id(account.id)["_id"])


class AnalyticsContractTest(LedgerFixtureTestCase):
    def test_the_route_answers_the_calling_account_with_its_own_figures(self):
        self.build_ledger()

        response = self.api_client(self.token).get(self.query(self.period_start(), self.period_end()))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"data"})
        self.assertEqual(response.json()["data"]["sentMinor"], AARAV_SENT)
        self.assertEqual(response.json()["data"]["receivedMinor"], AARAV_RECEIVED)

    def test_the_response_says_exactly_what_it_carries_and_nothing_else(self):
        figures = self.read_period()

        self.assertEqual(
            set(figures),
            {
                "currency",
                "from",
                "to",
                "bucketSize",
                "timeZone",
                "sentMinor",
                "receivedMinor",
                "netMovementMinor",
                "transactionCount",
                "averageAmountMinor",
                "volume",
            },
        )
        self.assertEqual(figures["currency"], "INR")
        self.assertEqual(figures["bucketSize"], "DAY")
        self.assertEqual(figures["timeZone"], "UTC")

        for bucket in figures["volume"]:
            self.assertEqual(set(bucket), {"bucket", "sentMinor", "receivedMinor", "transactionCount"})

    def test_the_route_requires_authentication(self):
        response = self.api_client().get(ANALYTICS_PATH)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")

    def test_the_route_cannot_be_written_to(self):
        response = self.api_client(self.token).post(ANALYTICS_PATH, {}, content_type="application/json")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["error"]["code"], "METHOD_NOT_ALLOWED")

    def test_no_period_at_all_reads_the_default_window_ending_today(self):
        self.build_ledger()

        response = self.api_client(self.token).get(ANALYTICS_PATH)

        self.assertEqual(response.status_code, 200)

        figures = response.json()["data"]
        today = datetime.now(timezone.utc).date()

        self.assertEqual(figures["to"], today.isoformat())
        self.assertEqual(figures["from"], (today - timedelta(days=29)).isoformat())
        self.assertEqual(len(figures["volume"]), 30)


class PeriodSelectionTest(LedgerFixtureTestCase):
    def test_a_day_is_a_period_of_one_bucket(self):
        figures = self.read(ANCHOR, ANCHOR)

        self.assertEqual(len(figures["volume"]), 1)
        self.assertEqual(figures["volume"][0]["bucket"], ANCHOR.isoformat())

    def test_both_endpoints_of_a_range_belong_to_it(self):
        figures = self.read(ANCHOR, ANCHOR + timedelta(days=3))

        self.assertEqual(len(figures["volume"]), 4)
        self.assertEqual(figures["volume"][0]["bucket"], ANCHOR.isoformat())
        self.assertEqual(figures["volume"][-1]["bucket"], (ANCHOR + timedelta(days=3)).isoformat())

    def test_one_named_endpoint_anchors_the_default_window_on_that_day(self):
        """A single date reads as a window, and works for a day that is not today."""
        forwards = self.read(from_date=ANCHOR)
        backwards = self.read(to_date=ANCHOR)

        self.assertEqual(forwards["from"], ANCHOR.isoformat())
        self.assertEqual(forwards["to"], (ANCHOR + timedelta(days=29)).isoformat())
        self.assertEqual(backwards["from"], (ANCHOR - timedelta(days=29)).isoformat())
        self.assertEqual(backwards["to"], ANCHOR.isoformat())
        self.assertEqual(len(forwards["volume"]), 30)
        self.assertEqual(len(backwards["volume"]), 30)
        self.assertEqual(forwards["volume"][0]["bucket"], ANCHOR.isoformat())
        self.assertEqual(backwards["volume"][-1]["bucket"], ANCHOR.isoformat())

    def test_naming_both_endpoints_names_the_period_exactly(self):
        figures = self.read(ANCHOR, ANCHOR + timedelta(days=1))

        self.assertEqual(figures["from"], ANCHOR.isoformat())
        self.assertEqual(figures["to"], (ANCHOR + timedelta(days=1)).isoformat())
        self.assertEqual(len(figures["volume"]), 2)

    def test_a_date_that_is_not_a_date_is_refused(self):
        for field, value in (("from", "10-03-2026"), ("to", "2026-3-10"), ("from", "yesterday"), ("to", "")):
            response = self.api_client(self.token).get(f"{ANALYTICS_PATH}?{field}={value}")

            self.assertEqual(response.status_code, 400, response.content)
            self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
            self.assertEqual(
                response.json()["error"]["details"]["fieldErrors"][field],
                ["Invalid date: expected a calendar date as YYYY-MM-DD."],
            )

    def test_a_reversed_range_is_refused(self):
        response = self.api_client(self.token).get(self.query(ANCHOR + timedelta(days=3), ANCHOR))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"]["details"]["formErrors"],
            ["Invalid period: from must be on or before to, and both endpoints are inclusive."],
        )

    def test_a_range_longer_than_the_bound_is_refused_and_one_at_the_bound_is_not(self):
        allowed = self.api_client(self.token).get(self.query(date(2024, 1, 1), date(2024, 12, 31)))

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(len(allowed.json()["data"]["volume"]), MAX_PERIOD_DAYS)

        refused = self.api_client(self.token).get(self.query(date(2024, 1, 1), date(2025, 1, 1)))

        self.assertEqual(refused.status_code, 400)
        self.assertEqual(
            refused.json()["error"]["details"]["formErrors"],
            [f"Too long: expected a period to cover at most {MAX_PERIOD_DAYS} days."],
        )


class TimeBoundaryTest(LedgerFixtureTestCase):
    """Where a movement belongs when it falls on the seam of a period or of a day.

    These are stamped to exact instants because the seams are exact: a movement at
    23:59:59.999 and one at the next midnight are different movements a millisecond apart,
    and no test can arrange for the clock to produce them by waiting.
    """

    def test_a_movement_at_the_first_instant_of_the_period_belongs_to_it(self):
        self.send(SENDER_EMAIL, RECIPIENT_EMAIL, 1_000, instant(0, 0, 0, 0, 0))

        figures = self.read(ANCHOR, ANCHOR + timedelta(days=1))

        self.assertEqual(figures["sentMinor"], 1_000)
        self.assertEqual(figures["volume"][0]["sentMinor"], 1_000)

    def test_a_movement_at_the_last_instant_of_the_period_belongs_to_it(self):
        self.send(SENDER_EMAIL, RECIPIENT_EMAIL, 1_000, instant(1, 23, 59, 59, 999))

        figures = self.read(ANCHOR, ANCHOR + timedelta(days=1))

        self.assertEqual(figures["sentMinor"], 1_000)
        self.assertEqual(figures["volume"][-1]["bucket"], (ANCHOR + timedelta(days=1)).isoformat())
        self.assertEqual(figures["volume"][-1]["sentMinor"], 1_000)

    def test_a_movement_at_the_first_instant_past_the_period_does_not(self):
        self.send(SENDER_EMAIL, RECIPIENT_EMAIL, 1_000, instant(2, 0, 0, 0, 0))

        figures = self.read(ANCHOR, ANCHOR + timedelta(days=1))

        self.assertEqual(figures["sentMinor"], 0)
        self.assertEqual(figures["transactionCount"], 0)
        self.assertEqual(figures["volume"][-1]["sentMinor"], 0)

    def test_a_movement_at_the_last_instant_before_the_period_does_not(self):
        self.send(SENDER_EMAIL, RECIPIENT_EMAIL, 1_000, instant(-1, 23, 59, 59, 999))

        figures = self.read(ANCHOR, ANCHOR + timedelta(days=1))

        self.assertEqual(figures["sentMinor"], 0)

    def test_movements_a_millisecond_apart_straddling_midnight_land_in_different_buckets(self):
        """The rule is a UTC day boundary, so the answer does not depend on the reader."""
        self.send(SENDER_EMAIL, RECIPIENT_EMAIL, 1_000, instant(0, 23, 59, 59, 999))
        self.send(SENDER_EMAIL, RECIPIENT_EMAIL, 2_000, instant(1, 0, 0, 0, 1))

        first_day = self.read(ANCHOR, ANCHOR)
        second_day = self.read(ANCHOR + timedelta(days=1), ANCHOR + timedelta(days=1))

        self.assertEqual((first_day["sentMinor"], first_day["transactionCount"]), (1_000, 1))
        self.assertEqual((second_day["sentMinor"], second_day["transactionCount"]), (2_000, 1))
        self.assertEqual(first_day["volume"][0]["sentMinor"], 1_000)
        self.assertEqual(second_day["volume"][0]["sentMinor"], 2_000)

    def test_a_reader_cannot_move_the_day_edges_by_declaring_a_timezone(self):
        self.send(SENDER_EMAIL, RECIPIENT_EMAIL, 1_000, instant(0, 23, 30))

        figures = self.read(ANCHOR, ANCHOR, extra="&timezone=Asia%2FKolkata&tz=IST&offset=19800")

        self.assertEqual(figures["timeZone"], "UTC")
        self.assertEqual(figures["volume"][0]["sentMinor"], 1_000)


class BucketTest(LedgerFixtureTestCase):
    def test_a_day_nothing_happened_on_is_reported_as_a_day_of_zeros(self):
        self.build_ledger()

        figures = self.read_period()

        self.assertEqual(
            figures["volume"],
            [
                {"bucket": (ANCHOR - timedelta(days=1)).isoformat(), "sentMinor": 0, "receivedMinor": 0, "transactionCount": 0},
                {"bucket": ANCHOR.isoformat(), "sentMinor": FIRST_PAYMENT, "receivedMinor": 0, "transactionCount": 1},
                {"bucket": (ANCHOR + timedelta(days=1)).isoformat(), "sentMinor": SECOND_PAYMENT, "receivedMinor": 0, "transactionCount": 1},
                {"bucket": (ANCHOR + timedelta(days=2)).isoformat(), "sentMinor": 0, "receivedMinor": DIEGO_PAYS_BACK, "transactionCount": 1},
                {"bucket": (ANCHOR + timedelta(days=3)).isoformat(), "sentMinor": 0, "receivedMinor": REFUNDED, "transactionCount": 1},
                {"bucket": (ANCHOR + timedelta(days=4)).isoformat(), "sentMinor": 0, "receivedMinor": 0, "transactionCount": 0},
            ],
        )

    def test_the_buckets_add_up_to_the_totals(self):
        self.build_ledger()

        figures = self.read_period()

        self.assertEqual(sum(bucket["sentMinor"] for bucket in figures["volume"]), figures["sentMinor"])
        self.assertEqual(sum(bucket["receivedMinor"] for bucket in figures["volume"]), figures["receivedMinor"])
        self.assertEqual(sum(bucket["transactionCount"] for bucket in figures["volume"]), figures["transactionCount"])

    def test_a_period_with_no_activity_reports_zeros_across_the_whole_range(self):
        self.build_ledger()

        figures = self.read(ANCHOR + timedelta(days=30), ANCHOR + timedelta(days=34))

        self.assertEqual(figures["sentMinor"], 0)
        self.assertEqual(figures["receivedMinor"], 0)
        self.assertEqual(figures["netMovementMinor"], 0)
        self.assertEqual(figures["transactionCount"], 0)
        self.assertEqual(figures["averageAmountMinor"], 0)
        self.assertEqual(len(figures["volume"]), 5)
        self.assertTrue(all(bucket["transactionCount"] == 0 for bucket in figures["volume"]))


class MetricTest(LedgerFixtureTestCase):
    def test_the_figures_are_the_arithmetic_of_the_movements_the_test_made(self):
        self.build_ledger()

        figures = self.read_period()

        self.assertEqual(figures["sentMinor"], AARAV_SENT)
        self.assertEqual(figures["receivedMinor"], AARAV_RECEIVED)
        self.assertEqual(figures["netMovementMinor"], AARAV_RECEIVED - AARAV_SENT)
        self.assertEqual(figures["transactionCount"], 4)
        self.assertEqual(figures["averageAmountMinor"], (AARAV_SENT + AARAV_RECEIVED) // 4)

    def test_net_movement_is_the_change_the_wallet_itself_recorded(self):
        """The strongest check there is on the totals: money conserved, not merely summed.

        Every movement the ledger describes moved the balance by that amount, so a period's
        net movement must equal what the balance became minus what it was — unless a figure
        counted something that never moved money.
        """
        self.build_ledger()

        figures = self.read_period()

        self.assertEqual(figures["netMovementMinor"], self.balance_of(self.aarav) - 1_000_000)
        self.assertEqual(self.balance_of(self.aarav), AARAV_ENDS_AT)
        self.assertEqual(self.derived_of(self.aarav), AARAV_ENDS_AT)

    def test_the_opening_credit_funds_the_wallet_without_being_money_received(self):
        self.build_ledger()

        with_opening = self.read(ANCHOR - timedelta(days=40), self.period_end())
        without_opening = self.read_period()

        self.assertEqual(with_opening["receivedMinor"], 230_000)
        self.assertEqual(with_opening["transactionCount"], 4)
        self.assertEqual(with_opening["receivedMinor"], without_opening["receivedMinor"])
        self.assertEqual(with_opening["volume"][0], {
            "bucket": (ANCHOR - timedelta(days=40)).isoformat(),
            "sentMinor": 0,
            "receivedMinor": 0,
            "transactionCount": 0,
        })
        self.assertEqual(wallet_repository.totals_by_direction(
            wallet_repository.find_wallet_by_user_id(self.aarav.id)["_id"]
        )["CREDIT"], 1_230_000)
        self.assertEqual(self.balance_of(self.aarav), AARAV_ENDS_AT)

    def test_a_refund_counts_on_the_side_it_arrived_on(self):
        self.build_ledger()

        figures = self.read_period()
        bucket = next(row for row in figures["volume"] if row["bucket"] == (ANCHOR + timedelta(days=3)).isoformat())

        self.assertEqual(bucket["receivedMinor"], REFUNDED)
        self.assertEqual(figures["receivedMinor"], DIEGO_PAYS_BACK + REFUNDED)

    def test_a_transfer_that_moved_no_money_is_not_an_activity(self):
        """A refused transfer is a `Transfer` row and no ledger entry, and must not appear.

        This is the population distinction the requirement warns about. A total read from
        transfers would count an operation that paid nobody, and nothing about the answer
        would look wrong.
        """
        broke = self.create_account(name="Grace Obi", email="grace.obi@wallet.demo", opening_balance_minor=0)

        with self.assertRaises(AppError) as raised:
            transfer_services.execute_transfer(self.account_document(broke), SENDER_EMAIL, 50_000, "cannot be covered")

        self.assertEqual(raised.exception.code, "INSUFFICIENT_FUNDS")

        refused = Transfer.objects(__raw__={"senderUserId": broke.id}).as_pymongo()

        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["state"], "FAILED")
        self.assertEqual(LedgerEntry.objects(__raw__={"userId": broke.id}).count(), 0)

        figures = self.read(self.period_start(), self.period_end(), email="grace.obi@wallet.demo")

        self.assertEqual(figures["sentMinor"], 0)
        self.assertEqual(figures["receivedMinor"], 0)
        self.assertEqual(figures["transactionCount"], 0)
        self.assertEqual(figures["averageAmountMinor"], 0)

    def test_an_average_that_would_not_be_whole_paise_is_reported_below_it(self):
        self.send(SENDER_EMAIL, RECIPIENT_EMAIL, 100_001, instant(0))
        self.send(SENDER_EMAIL, RECIPIENT_EMAIL, 2, instant(1))

        figures = self.read(ANCHOR, ANCHOR + timedelta(days=1))

        self.assertEqual(figures["transactionCount"], 2)
        self.assertEqual(figures["averageAmountMinor"], (100_001 + 2) // 2)
        self.assertIsInstance(figures["averageAmountMinor"], int)

    def test_the_two_sides_of_the_same_money_are_mirrored_not_identical(self):
        self.build_ledger()

        aarav = self.read_period()
        diego = self.read_period(email=RECIPIENT_EMAIL)

        self.assertEqual(diego["sentMinor"], AARAV_RECEIVED)
        self.assertEqual(diego["receivedMinor"], AARAV_SENT)
        self.assertEqual(diego["netMovementMinor"], -aarav["netMovementMinor"])
        self.assertEqual(diego["transactionCount"], aarav["transactionCount"])
        self.assertEqual(diego["averageAmountMinor"], aarav["averageAmountMinor"])
        self.assertEqual(self.balance_of(self.diego), AARAV_SENT - AARAV_RECEIVED)
        self.assertEqual(self.derived_of(self.diego), AARAV_SENT - AARAV_RECEIVED)


class LedgerKindTest(MongoTestCase):
    def test_the_kinds_the_totals_ask_about_are_all_of_them_but_the_opening(self):
        """The exclusion of `OPENING` has to be the only exclusion, or a kind goes missing."""
        self.assertEqual(sorted(ACTIVITY_KINDS), sorted(kind for kind in ENTRY_KINDS if kind != "OPENING"))
        self.assertEqual(sorted(SENT_KINDS + RECEIVED_KINDS), sorted(ACTIVITY_KINDS))
        self.assertFalse(set(SENT_KINDS) & set(RECEIVED_KINDS))


class OwnershipTest(LedgerFixtureTestCase):
    def test_a_signed_in_account_reads_its_own_figures_and_nobody_elses(self):
        self.build_ledger()
        stranger = self.create_account(name="Stranger", email=STRANGER_EMAIL, opening_balance_minor=500)

        stranger_figures = self.read_period(email=STRANGER_EMAIL)

        self.assertEqual(stranger_figures["sentMinor"], 0)
        self.assertEqual(stranger_figures["receivedMinor"], 0)
        self.assertEqual(stranger_figures["transactionCount"], 0)
        self.assertEqual(stranger_figures["currency"], "INR")
        self.assertEqual(self.balance_of(stranger), 500)

    def test_naming_another_account_or_wallet_changes_nothing(self):
        """There is no parameter that could point the figures elsewhere, and none is honoured.

        The subject is taken from the authenticated account, so a caller that guesses a
        parameter name cannot reach somebody else's money. Asserted as a set of spellings so
        this cannot quietly become a test of one of them.
        """
        self.build_ledger()

        expected = self.read_period()

        for field in SUBJECT_PARAMETERS:
            self.assertEqual(self.read_period(extra=f"&{field}={self.diego.id}"), expected, f"{field} was honoured")

    def test_the_figures_do_not_depend_on_how_much_history_the_reader_has_loaded(self):
        """Analytics is not a total the client worked out from the rows it happened to hold."""
        self.build_ledger()

        cold = self.read_period()
        client = self.api_client(self.token)
        offset = 0

        while True:
            page = client.get(f"{HISTORY_PATH}?limit=25&offset={offset}").json()["data"]

            if not page["entries"]:
                break

            offset += len(page["entries"])

        self.assertEqual(offset, 5, "the whole ledger was not paged, so the comparison proved nothing")
        self.assertEqual(self.read_period(), cold)


class ReadOnlyTest(LedgerFixtureTestCase):
    def test_reading_analytics_writes_nothing_at_all(self):
        """INV-14: an analytics query is read-only with respect to financial state."""
        self.build_ledger()

        before = (
            Wallet.objects.count(),
            LedgerEntry.objects.count(),
            Transfer.objects.count(),
            tuple(sorted(str(row["_id"]) for row in Wallet.objects(__raw__={}).as_pymongo())),
        )

        self.read_period()
        self.read_period(email=RECIPIENT_EMAIL)
        self.read(ANCHOR, ANCHOR)
        self.read(ANCHOR + timedelta(days=30), ANCHOR + timedelta(days=34))

        after = (
            Wallet.objects.count(),
            LedgerEntry.objects.count(),
            Transfer.objects.count(),
            tuple(sorted(str(row["_id"]) for row in Wallet.objects(__raw__={}).as_pymongo())),
        )

        self.assertEqual(after, before)
        self.assertEqual(before[:3], (2, 9, 4))
        self.assertEqual(self.balance_of(self.aarav), AARAV_ENDS_AT)
        self.assertEqual(self.derived_of(self.aarav), AARAV_ENDS_AT)
        self.assertEqual(self.read_period()["sentMinor"], AARAV_SENT)

    def test_no_figure_can_be_handed_to_the_caller_from_the_request(self):
        """The numbers are the server's reading of the ledger, not anything a client asserts.

        This is what would fail if the figures were assembled where the caller can reach
        them: the response would become a function of the query rather than of the ledger,
        and a page could be made to report any total it liked.
        """
        self.build_ledger()

        expected = self.read_period()

        for figure in SUPPLIED_FIGURES:
            supplied = self.read_period(extra=f"&{figure}=999999")

            self.assertEqual(supplied, expected, f"{figure} was taken from the request")

    def test_the_service_takes_the_account_and_the_period_and_nothing_else(self):
        """A service that also took a subject could not be held to the boundary above."""
        figures = analytics_services.account_analytics(self.aarav.id, ANCHOR, ANCHOR)

        self.assertEqual(figures["from"], ANCHOR.isoformat())
        self.assertEqual(figures["to"], ANCHOR.isoformat())
        self.assertEqual(len(figures["volume"]), 1)