import { formatMinor, formatMoment } from "../wallet/wallet-format.js";

/**
 * The arrow a row is marked with. Money leaving the wallet points up, money arriving points
 * down, and a refund points back the way it came, which is the only honest way to draw a
 * reversal without borrowing a picture.
 */
const ICONS = {
	OPENING: "•",
	TRANSFER_IN: "↓",
	TRANSFER_OUT: "↑",
	REFUND_IN: "↩",
	REFUND_OUT: "↩",
};

const PREVIEW_SIZE = 4;

/**
 * The last few movements, and a way to the rest.
 *
 * This is a preview, not a ledger: it draws from the history the app has already read and
 * deliberately stops at a handful of rows, because the point of it is to answer "what just
 * happened?" without becoming the place the whole register is read. "See all" hands over to
 * the Activity page, which is where the full list lives.
 */
export function RecentActivity({ entries = [], total = 0, loading = false, onSeeAll = () => {} }) {
	const recent = entries.slice(0, PREVIEW_SIZE);

	return (
		<section className="home-recent" aria-labelledby="home-recent-heading">
			<div className="home-recent-header">
				<h2 className="home-recent-heading" id="home-recent-heading">
					Recent activity
				</h2>

				{total ? (
					<button className="link-button" type="button" onClick={onSeeAll}>
						See all <span aria-hidden="true">→</span>
					</button>
				) : null}
			</div>

			{loading && !entries.length ? (
				<ul className="activity-list" aria-live="polite" aria-busy="true">
					{[0, 1, 2].map((row) => (
						<li className="activity-row activity-row-skeleton" key={row} aria-hidden="true">
							<span className="skeleton-line" />
							<span className="skeleton-line skeleton-line-short" />
						</li>
					))}
				</ul>
			) : null}

			{recent.length ? (
				<ul className="activity-list">
					{recent.map((entry) => {
						const isDebit = entry.direction === "DEBIT";
						const party = entry.counterparty?.name;

						return (
							<li className="activity-row" key={entry.id}>
								<span className="activity-row-icon" aria-hidden="true">
									{ICONS[entry.kind] || "•"}
								</span>

								<span className="activity-row-main">
									<span className="activity-row-label">
										{party
											? `${isDebit ? "Sent to" : "Received from"} ${party}`
											: entry.kind === "OPENING"
												? "Opening balance"
												: "Money moved"}
									</span>
									<span className="activity-row-moment">{formatMoment(entry.createdAt)}</span>
								</span>

								<span className={isDebit ? "activity-row-amount activity-row-amount-debit" : "activity-row-amount"}>
									{isDebit ? "−" : "+"}
									{formatMinor(entry.amountMinor)}
								</span>
							</li>
						);
					})}
				</ul>
			) : null}

			{!loading && !recent.length ? (
				<div className="history-empty">
					<h3>No activity yet</h3>
					<p>Send money to someone, or ask to be paid, and your newest movements will appear here.</p>
				</div>
			) : null}
		</section>
	);
}
