import { WalletBalanceCard } from "../wallet/WalletBalanceCard.jsx";
import { RecentActivity } from "./RecentActivity.jsx";

/** The hour decides which greeting reads true, rather than a fixed one that is wrong all day. */
function greeting() {
	const hour = new Date().getHours();

	if (hour < 12) return "Good morning";
	if (hour < 17) return "Good afternoon";

	return "Good evening";
}

/**
 * The wallet's front door: who is signed in, what they have, and the two things they came
 * to do.
 *
 * Send and Ask are actions rather than tabs. Each one opens its own page, because sending and
 * asking are different jobs with different consequences and neither is a view onto the other.
 * Everything here is read from state the app already holds — the same balance card, the same
 * history the Activity page shows in full — so landing on Home costs no request at all.
 */
export function HomePage({
	account = null,
	wallet = null,
	walletLoading = false,
	walletError = "",
	onRetryWallet = () => {},
	entries = [],
	historyTotal = 0,
	historyLoading = false,
	onSend = () => {},
	onAsk = () => {},
	onSeeAll = () => {},
}) {
	const firstName = account?.name?.split(" ")[0]?.trim() || "";

	return (
		<div className="home-page">
			<p className="home-greeting">
				{greeting()}
				{firstName ? `, ${firstName}` : ""}
			</p>

			<WalletBalanceCard wallet={wallet} loading={walletLoading} error={walletError} onRetry={onRetryWallet} />

			<div className="home-actions">
				<button className="home-action" type="button" onClick={onSend}>
					<span className="home-action-icon" aria-hidden="true">
						↑
					</span>
					<span className="home-action-label">Send money</span>
				</button>

				<button className="home-action" type="button" onClick={onAsk}>
					<span className="home-action-icon" aria-hidden="true">
						↓
					</span>
					<span className="home-action-label">Ask money</span>
				</button>
			</div>

			<RecentActivity entries={entries} total={historyTotal} loading={historyLoading} onSeeAll={onSeeAll} />
		</div>
	);
}
