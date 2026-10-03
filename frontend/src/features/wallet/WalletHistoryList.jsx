import { entryLabel, formatMinor, formatMoment } from "./wallet-format.js";

export function WalletHistoryList({
	entries = [],
	total = 0,
	offset = 0,
	loading = false,
	error = "",
	onLoadMore = () => {},
	onRetry = () => {},
}) {
	const hasMore = offset + entries.length < total;

	return (
		<section className="history" aria-labelledby="history-heading">
			<div className="history-header">
				<h2 className="history-heading" id="history-heading">
					Transaction history
				</h2>
				<span className="history-count">
					{total} {total === 1 ? "entry" : "entries"}
				</span>
			</div>

			{error ? (
				<div className="history-error" role="alert">
					<p>{error}</p>
					<button className="secondary-button" type="button" onClick={onRetry}>
						Try again
					</button>
				</div>
			) : null}

			{!error && loading && !entries.length ? (
				<ul className="history-list" aria-live="polite" aria-busy="true">
					{[0, 1, 2].map((row) => (
						<li className="history-row history-row-skeleton" key={row} aria-hidden="true">
							<span className="skeleton-line" />
							<span className="skeleton-line skeleton-line-short" />
						</li>
					))}
				</ul>
			) : null}

			{!error && entries.length ? (
				<ul className="history-list">
					{entries.map((entry) => {
						const isDebit = entry.direction === "DEBIT";
						const amountClass = isDebit ? "history-row-amount history-row-amount-debit" : "history-row-amount";

						return (
							<li className="history-row" key={entry.id}>
								<div className="history-row-main">
									<span className="history-row-label">{entryLabel(entry)}</span>
									<span className="history-row-moment">{formatMoment(entry.createdAt)}</span>
								</div>
								<div className="history-row-amounts">
									<span className={amountClass}>
										{isDebit ? "−" : "+"}
										{formatMinor(entry.amountMinor)}
									</span>
									<span className="history-row-balance">{formatMinor(entry.balanceAfterMinor)}</span>
								</div>
							</li>
						);
					})}
				</ul>
			) : null}

			{!error && !loading && !entries.length ? (
				<div className="history-empty">
					<h3>No transactions yet</h3>
					<p>This wallet has no recorded movements. Its ledger will appear here as soon as money moves.</p>
				</div>
			) : null}

			{!error && hasMore ? (
				<button className="secondary-button history-more" type="button" onClick={onLoadMore} disabled={loading}>
					{loading ? "Loading…" : "Load more"}
				</button>
			) : null}
		</section>
	);
}
