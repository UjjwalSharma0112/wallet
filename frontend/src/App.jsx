import { useCallback, useEffect, useRef, useState } from "react";
import { authApi } from "./features/auth/auth.api.js";
import { SignInForm } from "./features/auth/SignInForm.jsx";
import { MoneyRequestForm } from "./features/money_requests/MoneyRequestForm.jsx";
import { MoneyRequestList } from "./features/money_requests/MoneyRequestList.jsx";
import { moneyRequestsApi } from "./features/money_requests/money-requests.api.js";
import { TransferForm } from "./features/transfers/TransferForm.jsx";
import { transfersApi } from "./features/transfers/transfers.api.js";
import { walletApi } from "./features/wallet/wallet.api.js";
import { WalletBalanceCard } from "./features/wallet/WalletBalanceCard.jsx";
import { WalletHistoryList } from "./features/wallet/WalletHistoryList.jsx";
import { formatMinor } from "./features/wallet/wallet-format.js";
import { hasSessionToken, setSessionToken } from "./shared/api/client.js";

const PAGE_SIZE = 25;
const EMPTY_HISTORY = { entries: [], total: 0, offset: 0 };
const EMPTY_TRANSFER = { error: "", fieldErrors: {}, notice: "", resetKey: 0 };
const EMPTY_REQUESTS = { requests: [], total: 0, offset: 0 };
const EMPTY_FILTERS = { role: "", state: "" };

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

export default function App() {
	const [stage, setStage] = useState("booting");
	const [account, setAccount] = useState(null);
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
	}

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
	}, [loadRequests]);

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

	const handleSignIn = useCallback(
		async (email, password) => {
			setSignIn({ error: "", loading: true });

			try {
				const result = await authApi.login(email, password);

				setSessionToken(result.token);
				setAccount(result.account);
				resetWallet();
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
					<div className="app-shell-account">
						<span className="app-shell-account-name">{account.name}</span>
						<span className="app-shell-account-email">{account.email}</span>
						<button className="secondary-button" type="button" onClick={handleSignOut}>
							Sign out
						</button>
					</div>
				) : null}
			</header>

			<main className="app-shell-main">
				{stage === "booting" ? (
					<p className="app-shell-summary" role="status">
						Checking your session…
					</p>
				) : null}

				{stage === "signIn" ? <SignInForm error={signIn.error} loading={signIn.loading} onSignIn={handleSignIn} /> : null}

				{stage === "wallet" ? (
					<div className="wallet-layout">
						<WalletBalanceCard wallet={wallet} loading={walletLoading} error={walletError} onRetry={loadWallet} />
						<TransferForm
							balanceMinor={wallet?.availableBalanceMinor ?? null}
							onSend={handleSend}
							loading={transferLoading}
							error={transfer.error}
							fieldErrors={transfer.fieldErrors}
							notice={transfer.notice}
							resetKey={transfer.resetKey}
						/>
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
						<WalletHistoryList
							entries={history.entries}
							total={history.total}
							offset={history.offset}
							loading={historyLoading}
							error={historyError}
							onLoadMore={loadMore}
							onRetry={loadWallet}
						/>
					</div>
				) : null}
			</main>
		</div>
	);
}
