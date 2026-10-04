import { formatMinor, formatMoment } from "../wallet/wallet-format.js";

const STATE_LABELS = {
	PENDING: "Waiting to be run",
	PROCESSING: "Being processed",
	COMPLETED: "Paid in full",
	PARTIALLY_COMPLETED: "Partly paid",
	FAILED: "Not paid",
};

const ITEM_LABELS = { PENDING: "Waiting", COMPLETED: "Paid", FAILED: "Not paid" };

/**
 * What the button on a row offers to do, decided by what is left rather than by the state.
 *
 * A batch that has paid everything is still worth asking about, and the honest answer to
 * asking is the same summary — so it reads "Check again" rather than being refused. A batch
 * that was never run reads "Process batch", and one part-run reads "Run again", which is the
 * same action told the truth about: the payments already made are not repeated.
 */
function actionLabel(batch) {
	if (batch.summary.completedItems >= batch.summary.totalItems) return "Check again";

	return batch.state === "PENDING" ? "Process batch" : "Run again";
}

/**
 * The batches this account created, and what became of each of them.
 *
 * Every figure shown is one the service derived from the items, so nothing here keeps a count
 * of its own that could disagree with the rows underneath it. Running a batch answers with
 * what the service settled, and this list takes that answer as read: the service is the only
 * thing that knows whether a payment moved, so a batch is worth re-reading rather than having
 * a row's outcome assumed from what was clicked.
 */
export function BatchList({
	batches = [],
	total = 0,
	offset = 0,
	loading = false,
	processingId = "",
	error = "",
	runError = "",
	onRun = () => {},
	onLoadMore = () => {},
	onRetry = () => {},
}) {
	const hasMore = offset + batches.length < total;

	return (
		<section className="history request-list" aria-labelledby="batch-list-heading">
			<div className="history-header">
				<h2 className="history-heading" id="batch-list-heading">
					Your batches
				</h2>
				<span className="history-count">{loading && !total ? "Loading…" : `${total} ${total === 1 ? "batch" : "batches"}`}</span>
			</div>

			{error ? (
				<div className="history-error">
					<p>{error}</p>
					<button className="secondary-button" type="button" onClick={onRetry}>
						Try again
					</button>
				</div>
			) : null}

			{!loading && !total ? (
				<div className="history-empty">
					<h3>No batches yet</h3>
					<p>Write one above and it waits here until you run it. Nothing is sent until you do.</p>
				</div>
			) : null}

			<ul className="history-list">
				{batches.map((batch) => {
					const running = processingId === batch.id;

					return (
						<li className="history-row request-row batch-row" key={batch.id}>
							<div className="history-row-main">
								<div className="request-row-meta">
									<span className={`request-state batch-state-${batch.state.toLowerCase()}`}>
										{STATE_LABELS[batch.state] || batch.state}
									</span>
									<span className="batch-summary">
										{batch.summary.completedItems} of {batch.summary.totalItems} paid ·{" "}
										{formatMinor(batch.summary.completedAmountMinor)} sent
										{batch.summary.failedItems ? ` · ${formatMinor(batch.summary.failedAmountMinor)} not sent` : ""}
									</span>
									<span className="history-row-moment">{formatMoment(batch.createdAt)}</span>
								</div>

								{/* The items are what the summary above was derived from, so they are
								    shown rather than hidden behind the number. Each one carries its own
								    outcome and, where it has one, the reason it did not go. */}
								<ul className="batch-items">
									{batch.items.map((item) => (
										<li className={`batch-item batch-item-${item.state.toLowerCase()}`} key={item.position}>
											<span className="batch-item-state">{ITEM_LABELS[item.state] || item.state}</span>
											<span className="batch-item-to">{item.recipientEmail}</span>
											{item.note ? <span className="history-row-note">{item.note}</span> : null}
											<span className="batch-item-amount">{formatMinor(item.amountMinor)}</span>
											{item.failureMessage ? <p className="batch-item-reason">{item.failureMessage}</p> : null}
										</li>
									))}
								</ul>

								<div className="request-actions">
									{batch.state === "PROCESSING" ? (
										<span className="history-row-moment">Being processed right now</span>
									) : (
										<button
											className="secondary-button request-button"
											type="button"
											disabled={loading || Boolean(processingId)}
											onClick={() => onRun(batch)}
										>
											{running ? "Processing…" : actionLabel(batch)}
										</button>
									)}
									{batch.completedAt ? (
										<span className="history-row-moment">Finished {formatMoment(batch.completedAt)}</span>
									) : null}
								</div>

								{running && runError ? <p className="field-error">{runError}</p> : null}
							</div>

							<div className="history-row-amounts">
								<span className="history-row-amount">{formatMinor(batch.summary.totalAmountMinor)}</span>
								<span className="history-row-balance">of which to send</span>
							</div>
						</li>
					);
				})}
			</ul>

			{hasMore ? (
				<button className="secondary-button history-more" type="button" disabled={loading} onClick={onLoadMore}>
					{loading ? "Loading…" : "Show more"}
				</button>
			) : null}
		</section>
	);
}
