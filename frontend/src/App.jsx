import { useCallback, useEffect, useState } from "react";
import { authApi } from "./features/auth/auth.api.js";
import { SignInForm } from "./features/auth/SignInForm.jsx";
import { walletApi } from "./features/wallet/wallet.api.js";
import { WalletBalanceCard } from "./features/wallet/WalletBalanceCard.jsx";
import { WalletHistoryList } from "./features/wallet/WalletHistoryList.jsx";
import { hasSessionToken, setSessionToken } from "./shared/api/client.js";

const PAGE_SIZE = 25;
const EMPTY_HISTORY = { entries: [], total: 0, offset: 0 };

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

	function resetWallet() {
		setWallet(null);
		setWalletError("");
		setHistory(EMPTY_HISTORY);
		setHistoryError("");
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
