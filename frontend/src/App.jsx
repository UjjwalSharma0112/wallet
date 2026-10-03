import { useCallback, useEffect, useRef, useState } from "react";
import { authApi } from "./features/auth/auth.api.js";
import { SignInForm } from "./features/auth/SignInForm.jsx";
import { HomePage } from "./features/home/HomePage.jsx";
import { MoneyRequestForm } from "./features/money_requests/MoneyRequestForm.jsx";
import { MoneyRequestList } from "./features/money_requests/MoneyRequestList.jsx";
import { moneyRequestsApi } from "./features/money_requests/money-requests.api.js";
import { RefundsSection } from "./features/refunds/RefundsSection.jsx";
import { refundsApi } from "./features/refunds/refunds.api.js";
import { TransferForm } from "./features/transfers/TransferForm.jsx";
import { transfersApi } from "./features/transfers/transfers.api.js";
import { walletApi } from "./features/wallet/wallet.api.js";
import { WalletHistoryList } from "./features/wallet/WalletHistoryList.jsx";
import { formatMinor } from "./features/wallet/wallet-format.js";
import { hasSessionToken, setSessionToken } from "./shared/api/client.js";
import { BackButton } from "./shared/ui/BackButton.jsx";

/**
 * The pages this app can be on, and the two ways into Activity.
 *
 * Nothing here fetches. Every page of data is read once when the session is restored and
 * refreshed by the handlers below, so moving between pages only decides what is drawn over
 * data already held — which is why Home can show a balance and a preview without asking for
 * either again.
 */
const NAV_PAGES = [
	{ id: "home", label: "Home" },
	{ id: "activity", label: "Activity" },
];

const ACTIVITY_FILTERS = [
	{ id: "all", label: "All" },
	{ id: "sent", label: "Sent" },
	{ id: "received", label: "Received" },
	{ id: "refunds", label: "Refunds" },
];

const PAGE_SIZE = 25;
const EMPTY_HISTORY = { entries: [], total: 0, offset: 0 };
const EMPTY_TRANSFER = { error: "", fieldErrors: {}, notice: "", resetKey: 0 };
const EMPTY_REQUESTS = { requests: [], total: 0, offset: 0 };
const EMPTY_FILTERS = { role: "", state: "" };
const EMPTY_REFUNDS = { refunds: [], total: 0, offset: 0 };

/** Decide which field, if any, a refusal from the service belongs to. */
function fieldErrorsFrom(error) {
	if (error.code === "RECIPIENT_NOT_FOUND") return { recipientEmail: error.message };
	if (error.code === "VALIDATION_ERROR" && error.details?.fieldErrors) return error.details.fieldErrors;

	return {};
}

/**
 * The same mapping for the ask form, whose address field is named for who is being asked.
 *
 * The service reports an unknown account against the body key it was sent in, so the message
 * is moved onto the field this form actually has rather than being shown twice.
 */
function requestFieldErrorsFrom(error) {
	if (error.code === "RECIPIENT_NOT_FOUND") return { approverEmail: error.message };
	if (error.code === "VALIDATION_ERROR" && error.details?.fieldErrors) {
		return {
			...error.details.fieldErrors,
			...(error.details.fieldErrors.recipientEmail ? { approverEmail: error.details.fieldErrors.recipientEmail } : {}),
		};
	}

	return {};
}

/**
 * A refund refusal's field errors are kept as the service named them, so the amount field
 * of the refund form can show what the server said about that amount.
 */
function refundFieldErrorsFrom(error) {
	if (error.code === "VALIDATION_ERROR" && error.details?.fieldErrors) return error.details.fieldErrors;

	return {};
}

export default function App() {
	const [stage, setStage] = useState("booting");
	const [account, setAccount] = useState(null);
	// Which page is on screen. A load lands on Home, which is the dashboard rather than any
	// one job: Send and Ask are reached from its two actions, and Activity from its own link.
	const [page, setPage] = useState("home");
	// Which part of Activity is being read. Separate from the page itself, so leaving and
	// coming back to Activity returns to the same list rather than resetting it.
	const [activityFilter, setActivityFilter] = useState("all");
	const [signIn, setSignIn] = useState({ error: "", loading: false });
	const [wallet, setWallet] = useState(null);
	const [walletLoading, setWalletLoading] = useState(false);
	const [walletError, setWalletError] = useState("");
	const [history, setHistory] = useState(EMPTY_HISTORY);
	const [historyLoading, setHistoryLoading] = useState(false);
	const [historyError, setHistoryError] = useState("");
	const [transfer, setTransfer] = useState(EMPTY_TRANSFER);
	const [transferLoading, setTransferLoading] = useState(false);
	const [requests, setRequests] = useState(EMPTY_REQUESTS);
	const [requestFilters, setRequestFilters] = useState(EMPTY_FILTERS);
	const [requestsLoading, setRequestsLoading] = useState(false);
	const [requestsError, setRequestsError] = useState("");
	const [ask, setAsk] = useState(EMPTY_TRANSFER);
	const [askLoading, setAskLoading] = useState(false);
	const [requestActions, setRequestActions] = useState({});
	const [refunds, setRefunds] = useState(EMPTY_REFUNDS);
	const [refundsLoading, setRefundsLoading] = useState(false);
	const [refundsError, setRefundsError] = useState("");
	const [refundTarget, setRefundTarget] = useState(null);
	const [refund, setRefund] = useState(EMPTY_TRANSFER);
	const [refundLoading, setRefundLoading] = useState(false);
	// The filters the list is showing, held as a ref as well as in state. The list needs the
	// state to mark the pressed chips, but the callbacks that read requests must not be
	// rebuilt every time a chip changes, or the boot effect that reads the session would run
	// again on every filter. The ref is the value a callback reads and the state is what is
	// drawn; the two are only ever written together.
	const filtersOnScreen = useRef(EMPTY_FILTERS);

	function resetWallet() {
		setWallet(null);
		setWalletError("");
		setHistory(EMPTY_HISTORY);
		setHistoryError("");
		setTransfer(EMPTY_TRANSFER);
		setTransferLoading(false);
		setRequests(EMPTY_REQUESTS);
		setRequestFilters(EMPTY_FILTERS);
		filtersOnScreen.current = EMPTY_FILTERS;
		setRequestsError("");
		setAsk(EMPTY_TRANSFER);
		setAskLoading(false);
		setRequestActions({});
		setRefunds(EMPTY_REFUNDS);
		setRefundsError("");
		setRefundTarget(null);
		setRefund(EMPTY_TRANSFER);
		setRefundLoading(false);
	}

	/** Read one page of the caller's own refunds, newest first. */
	const loadRefunds = useCallback(async (offset = 0) => {
		setRefundsLoading(true);
		setRefundsError("");

		try {
			const page = await refundsApi.list({ limit: PAGE_SIZE, offset });

			setRefunds((current) =>
				offset
					? { refunds: [...current.refunds, ...page.refunds], total: page.total, offset: page.offset }
					: { refunds: page.refunds, total: page.total, offset: page.offset },
			);
		} catch (error) {
			setRefundsError(error.message);
		} finally {
			setRefundsLoading(false);
		}
	}, []);

	/**
	 * Read one page of requests, narrowed by the filters currently on screen.
	 *
	 * The filters are passed in rather than read from the list, so that changing a chip and
	 * answering a request go through exactly the same call and cannot drift apart. A later
	 * page is appended to what is on screen; the first page replaces it.
	 */
	const loadRequests = useCallback(async (filters, offset = 0) => {
		const chosen = filters || filtersOnScreen.current;

		setRequestsLoading(true);
		setRequestsError("");

		try {
			const page = await moneyRequestsApi.list({ ...chosen, limit: PAGE_SIZE, offset });

			setRequests((current) =>
				offset
					? { requests: [...current.requests, ...page.requests], total: page.total, offset: page.offset }
					: { requests: page.requests, total: page.total, offset: page.offset },
			);
		} catch (error) {
			setRequestsError(error.message);
		} finally {
			setRequestsLoading(false);
		}
	}, []);

	const loadWallet = useCallback(async () => {
		setWalletLoading(true);
		setHistoryLoading(true);
		setWalletError("");
		setHistoryError("");
		await loadRequests();
		await loadRefunds();

		try {
			const [currentWallet, page] = await Promise.all([walletApi.balance(), walletApi.transactions(PAGE_SIZE, 0)]);

			setWallet(currentWallet);
			setHistory({ entries: page.entries, total: page.total, offset: page.offset });
		} catch (error) {
			setWalletError(error.message);
			setHistoryError(error.message);
		} finally {
			setWalletLoading(false);
			setHistoryLoading(false);
		}
	}, [loadRequests, loadRefunds]);

	const handleFilterChange = useCallback(
		(filters) => {
			setRequestFilters(filters);
			filtersOnScreen.current = filters;
			loadRequests(filters);
		},
		[loadRequests],
	);

	const loadMoreRequests = useCallback(() => {
		loadRequests(undefined, requests.requests.length);
	}, [loadRequests, requests.requests.length]);

	const loadMore = useCallback(async () => {
		setHistoryLoading(true);
		setHistoryError("");

		try {
			const page = await walletApi.transactions(PAGE_SIZE, history.entries.length);

			setHistory((current) => ({ entries: [...current.entries, ...page.entries], total: page.total, offset: page.offset }));
		} catch (error) {
			setHistoryError(error.message);
		} finally {
			setHistoryLoading(false);
		}
	}, [history.entries.length]);

	/**
	 * Narrow the ledger to one side of it.
	 *
	 * Sent and Received are the ledger's own two directions rather than a new kind of row, so
	 * this is a reading of the movements already held and not a second set of them. The opening
	 * balance is a credit like any other, so it counts as Received: it did arrive, and it is
	 * the one row where saying so is accurate rather than approximate.
	 */
	const filterEntries = useCallback(
		(entries) => {
			if (activityFilter === "sent") return entries.filter((entry) => entry.direction === "DEBIT");
			if (activityFilter === "received") return entries.filter((entry) => entry.direction === "CREDIT");

			return entries;
		},
		[activityFilter],
	);

	const handleSend = useCallback(
		async ({ recipientEmail, amountMinor, note, idempotencyKey }) => {
			setTransferLoading(true);
			setTransfer((current) => ({ ...current, error: "", fieldErrors: {}, notice: "" }));

			try {
				const result = await transfersApi.send(recipientEmail, amountMinor, note, idempotencyKey);

				// The service returns the sender's own wallet with the transfer, so the
				// balance on screen is the balance that actually settled.
				setWallet(result.wallet);

				// A retry of a request the service had already honoured is told apart here,
				// so it reads as the transfer it repeated rather than as a new one.
				const transfer = result.transfer;
				const settled = transfer.state === "COMPLETED";

				setTransfer((current) => ({
					error: "",
					fieldErrors: {},
					notice: settled
						? `${result.idempotentReplay ? "Already sent" : "Sent"} ${formatMinor(transfer.amountMinor)}. New balance ${formatMinor(result.wallet.availableBalanceMinor)}.`
						: `Sending ${formatMinor(transfer.amountMinor)}. It will settle shortly.`,
					resetKey: current.resetKey + 1,
				}));

				const page = await walletApi.transactions(PAGE_SIZE, 0);

				setHistory({ entries: page.entries, total: page.total, offset: page.offset });
			} catch (error) {
				const reported = fieldErrorsFrom(error);

				// A refusal that belongs to a field is shown once, under that field.
				setTransfer((current) => ({
					...current,
					error: Object.keys(reported).length ? "" : error.message,
					fieldErrors: reported,
				}));

				// A refusal means the balance on screen is no longer the balance held, so it
				// is read again rather than left to contradict the ledger.
				if (error.code === "INSUFFICIENT_FUNDS") loadWallet();
			} finally {
				setTransferLoading(false);
			}
		},
		[loadWallet],
	);

	/**
	 * Ask another account for money. Nothing moves here, so the only thing to report is what
	 * was asked and to whom; the list is read again because the request is in it now.
	 */
	const handleAsk = useCallback(
		async ({ approverEmail, amountMinor, note }) => {
			setAskLoading(true);
			setAsk((current) => ({ ...current, error: "", fieldErrors: {}, notice: "" }));

			try {
				const created = await moneyRequestsApi.create(approverEmail, amountMinor, note);

				setAsk((current) => ({
					error: "",
					fieldErrors: {},
					notice: `Asked ${created.counterparty.name} for ${formatMinor(created.amountMinor)}. It is waiting for their answer.`,
					resetKey: current.resetKey + 1,
				}));

				await loadRequests();
			} catch (error) {
				const reported = requestFieldErrorsFrom(error);

				setAsk((current) => ({
					...current,
					error: Object.keys(reported).length ? "" : error.message,
					fieldErrors: reported,
				}));
			} finally {
				setAskLoading(false);
			}
		},
		[loadRequests],
	);

	/**
	 * Approve a request, which is a payment made through the transfer engine.
	 *
	 * The answer carries the approver's own wallet and the transfer that paid the request, so
	 * the balance on screen is the one that actually settled. The ledger is read again rather
	 * than having a row pushed onto it here, because a payment this made is a transfer like
	 * any other and belongs to the transfers app's history. A refusal is left on the row it
	 * happened to: the request is still there and still waiting, so the next thing to do is
	 * about that row rather than the whole list.
	 */
	const handleApprove = useCallback(
		async (requestId) => {
			setRequestActions((current) => ({ ...current, [requestId]: { loading: true, error: "" } }));

			try {
				const result = await moneyRequestsApi.approve(requestId);

				setWallet(result.wallet);
				setRequestActions((current) => ({ ...current, [requestId]: { loading: false, error: "" } }));

				const page = await walletApi.transactions(PAGE_SIZE, 0);

				setHistory({ entries: page.entries, total: page.total, offset: page.offset });
				await loadRequests();
			} catch (error) {
				setRequestActions((current) => ({ ...current, [requestId]: { loading: false, error: error.message } }));

				// A refusal means the list on screen is no longer what is held, so it is read
				// again; the row that failed keeps its own message on top of that.
				await loadRequests();
			}
		},
		[loadRequests],
	);

	/**
	 * Decline a request, which moves nothing and so has nothing to read back except the
	 * request that was answered.
	 */
	const handleDecline = useCallback(
		async (requestId) => {
			setRequestActions((current) => ({ ...current, [requestId]: { loading: true, error: "" } }));

			try {
				await moneyRequestsApi.decline(requestId);

				setRequestActions((current) => ({ ...current, [requestId]: { loading: false, error: "" } }));
				await loadRequests();
			} catch (error) {
				setRequestActions((current) => ({ ...current, [requestId]: { loading: false, error: error.message } }));
				await loadRequests();
			}
		},
		[loadRequests],
	);

	/**
	 * Give some of a received payment back. The payee's own wallet is what moves, so the
	 * engine answers with the balance that settled and the transfer that carried it; the
	 * screen reads both from that answer rather than guessing at them.
	 */
	const handleRefund = useCallback(
		async ({ originalTransferId, amountMinor, note }) => {
			setRefundLoading(true);
			setRefund((current) => ({ ...current, error: "", fieldErrors: {}, notice: "" }));

			try {
				const result = await refundsApi.create(originalTransferId, amountMinor, note);

				setWallet(result.wallet);
				setRefundTarget(null);
				setRefund((current) => ({
					error: "",
					fieldErrors: {},
					notice: `Returned ${formatMinor(result.refund.amountMinor)} to ${result.refund.counterparty?.name ?? "the sender"}.`,
					resetKey: current.resetKey + 1,
				}));

				const page = await walletApi.transactions(PAGE_SIZE, 0);

				setHistory({ entries: page.entries, total: page.total, offset: page.offset });
				await loadRefunds();
			} catch (error) {
				const reported = refundFieldErrorsFrom(error);

				setRefund((current) => ({
					...current,
					error: Object.keys(reported).length ? "" : error.message,
					fieldErrors: reported,
				}));

				// A refusal means the refundable total the form was quoting may have changed,
				// so the register underneath is read again.
				if (error.code === "REFUND_LIMIT_EXCEEDED" || error.code === "INSUFFICIENT_FUNDS") {
					const page = await walletApi.transactions(PAGE_SIZE, 0);

					setHistory({ entries: page.entries, total: page.total, offset: page.offset });
				}
			} finally {
				setRefundLoading(false);
			}
		},
		[loadRefunds],
	);

	const handleSignIn = useCallback(
		async (email, password) => {
			setSignIn({ error: "", loading: true });

			try {
				const result = await authApi.login(email, password);

				setSessionToken(result.token);
				setAccount(result.account);
				resetWallet();
				setPage("home");
				setStage("wallet");
				await loadWallet();
			} catch (error) {
				setSignIn({ error: error.message, loading: false });
			}
		},
		[loadWallet],
	);

	function handleSignOut() {
		setSessionToken("");
		setAccount(null);
		setSignIn({ error: "", loading: false });
		resetWallet();
		// Whoever signs in next starts from the dashboard rather than inheriting the page the
		// last account happened to be reading.
		setPage("home");
		setActivityFilter("all");
		setStage("signIn");
	}

	useEffect(() => {
		let cancelled = false;

		if (!hasSessionToken()) {
			setStage("signIn");
		} else {
			authApi
				.session()
				.then((result) => {
					if (cancelled) return;

					setAccount(result.account);
					setStage("wallet");
					loadWallet();
				})
				.catch(() => {
					if (cancelled) return;

					setSessionToken("");
					setStage("signIn");
				});
		}

		return () => {
			cancelled = true;
		};
	}, [loadWallet]);

	useEffect(() => {
		function handleExpired(event) {
			setAccount(null);
			setSignIn({ error: event.detail || "Your session ended. Sign in again.", loading: false });
			resetWallet();
			setPage("home");
			setActivityFilter("all");
			setStage("signIn");
		}

		window.addEventListener("wallet-session-expired", handleExpired);

		return () => window.removeEventListener("wallet-session-expired", handleExpired);
	}, []);

	return (
		<div className="app-shell">
			<header className="app-shell-header">
				<span className="app-shell-mark" aria-hidden="true">
					W
				</span>
				<h1 className="app-shell-title">Wallet</h1>
				{account ? (
					<>
						<nav className="app-nav" aria-label="Sections">
							{NAV_PAGES.map((item) => (
								<button
									className={page === item.id ? "app-nav-link app-nav-link-current" : "app-nav-link"}
									key={item.id}
									type="button"
									aria-current={page === item.id ? "page" : undefined}
									onClick={() => setPage(item.id)}
								>
									{item.label}
								</button>
							))}
						</nav>
						<div className="app-shell-account">
							<span className="app-shell-account-name">{account.name}</span>
							<span className="app-shell-account-email">{account.email}</span>
							<button className="secondary-button" type="button" onClick={handleSignOut}>
								Sign out
							</button>
						</div>
					</>
				) : null}
			</header>

			<main className={stage === "signIn" ? "app-shell-main app-shell-main-centered" : "app-shell-main"}>
				{stage === "booting" ? (
					<p className="app-shell-summary" role="status">
						Checking your session…
					</p>
				) : null}

				{stage === "signIn" ? <SignInForm error={signIn.error} loading={signIn.loading} onSignIn={handleSignIn} /> : null}

				{stage === "wallet" ? (
					<div className="wallet-layout">
						{/* The dashboard. It draws the balance card and a short preview of the
						    ledger, both of which are already held, and offers the two things a
						    wallet is opened to do. */}
						<div className="app-page" hidden={page !== "home"}>
							<HomePage
								account={account}
								wallet={wallet}
								walletLoading={walletLoading}
								walletError={walletError}
								onRetryWallet={loadWallet}
								entries={history.entries}
								historyTotal={history.total}
								historyLoading={historyLoading}
								onSend={() => setPage("send")}
								onAsk={() => setPage("ask")}
								onSeeAll={() => setPage("activity")}
							/>
						</div>

						{/* One job per page, each with its own way back. The transfer form is
						    given the balance to quote, exactly as it always has been. */}
						<div className="app-page" hidden={page !== "send"}>
							<div className="page-heading">
								<BackButton onBack={() => setPage("home")} />
								<h2 className="page-title">Send money</h2>
							</div>

							<TransferForm
								balanceMinor={wallet?.availableBalanceMinor ?? null}
								onSend={handleSend}
								loading={transferLoading}
								error={transfer.error}
								fieldErrors={transfer.fieldErrors}
								notice={transfer.notice}
								resetKey={transfer.resetKey}
							/>
						</div>

						{/* Asking moves no money, so the list of requests belongs beside the form
						    that raises them: what you asked, and what you are waiting on. */}
						<div className="app-page" hidden={page !== "ask"}>
							<div className="page-heading">
								<BackButton onBack={() => setPage("home")} />
								<h2 className="page-title">Ask for money</h2>
							</div>

							<MoneyRequestForm
								onAsk={handleAsk}
								loading={askLoading}
								error={ask.error}
								fieldErrors={ask.fieldErrors}
								notice={ask.notice}
								resetKey={ask.resetKey}
							/>
							<MoneyRequestList
								requests={requests.requests}
								total={requests.total}
								offset={requests.offset}
								filters={requestFilters}
								actions={requestActions}
								loading={requestsLoading}
								error={requestsError}
								onFilterChange={handleFilterChange}
								onApprove={handleApprove}
								onDecline={handleDecline}
								onLoadMore={loadMoreRequests}
								onRetry={loadWallet}
							/>
						</div>

						{/* The full register. The four filters are a reading of the same ledger
						    and the same refunds the wallet already holds, so choosing one
						    decides what is listed and never asks the service for anything. */}
						<div className="app-page" hidden={page !== "activity"}>
							<div className="page-heading">
								<BackButton onBack={() => setPage("home")} />
								<h2 className="page-title">Activity</h2>
							</div>

							<div className="activity-filters" role="group" aria-label="Narrow the activity shown">
								{ACTIVITY_FILTERS.map((item) => (
									<button
										className={activityFilter === item.id ? "request-chip request-chip-on" : "request-chip"}
										key={item.id}
										type="button"
										aria-pressed={activityFilter === item.id}
										onClick={() => setActivityFilter(item.id)}
									>
										{item.label}
									</button>
								))}
							</div>

							{activityFilter === "refunds" ? (
								<RefundsSection
									entries={history.entries}
									refunds={refunds.refunds}
									refundTotal={refunds.total}
									target={refundTarget}
									refundLoading={refundLoading}
									refundError={refund.error}
									refundFieldErrors={refund.fieldErrors}
									refundNotice={refund.notice}
									refundResetKey={refund.resetKey}
									refundsLoading={refundsLoading}
									refundsError={refundsError}
									onSelect={setRefundTarget}
									onCancel={() => setRefundTarget(null)}
									onRefund={handleRefund}
									onRetry={() => loadRefunds()}
								/>
							) : (
								<WalletHistoryList
									entries={filterEntries(history.entries)}
									total={activityFilter === "all" ? history.total : filterEntries(history.entries).length}
									offset={history.offset}
									loading={historyLoading}
									error={historyError}
									onLoadMore={loadMore}
									onRetry={loadWallet}
								/>
							)}
						</div>
					</div>
				) : null}
			</main>
		</div>
	);
}
