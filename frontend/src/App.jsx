import { useCallback, useEffect, useState } from "react";
import { authApi } from "./features/auth/auth.api.js";
import { SignInForm } from "./features/auth/SignInForm.jsx";
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

/** Decide which field, if any, a refusal from the service belongs to. */
function fieldErrorsFrom(error) {
	if (error.code === "RECIPIENT_NOT_FOUND") return { recipientEmail: error.message };
	if (error.code === "VALIDATION_ERROR" && error.details?.fieldErrors) return error.details.fieldErrors;

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

	function resetWallet() {
		setWallet(null);
		setWalletError("");
		setHistory(EMPTY_HISTORY);
		setHistoryError("");
		setTransfer(EMPTY_TRANSFER);
		setTransferLoading(false);
	}

	const loadWallet = useCallback(async () => {
		setWalletLoading(true);
		setHistoryLoading(true);
		setWalletError("");
		setHistoryError("");

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
	}, []);

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
		async ({ recipientEmail, amountMinor, note }) => {
			setTransferLoading(true);
			setTransfer((current) => ({ ...current, error: "", fieldErrors: {}, notice: "" }));

			try {
				const result = await transfersApi.send(recipientEmail, amountMinor, note);

				// The service returns the sender's own wallet with the transfer, so the
				// balance on screen is the balance that actually settled.
				setWallet(result.wallet);
				setTransfer((current) => ({
					error: "",
					fieldErrors: {},
					notice: `Sent ${formatMinor(result.transfer.amountMinor)}. New balance ${formatMinor(result.wallet.availableBalanceMinor)}.`,
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
