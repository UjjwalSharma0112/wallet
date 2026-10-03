import { formatMinor, formatMoment } from "../wallet/wallet-format.js";

const STATE_LABELS = {
	PENDING: "Awaiting an answer",
	APPROVING: "Approving",
	APPROVED: "Approved",
	DECLINED: "Declined",
	EXPIRED: "Expired",
};

const SIDE_FILTERS = [
	{ value: "", label: "All" },
	{ value: "APPROVER", label: "Asked of me" },
	{ value: "REQUESTER", label: "Asked by me" },
];

const STATE_FILTERS = [
	{ value: "", label: "Any state" },
	{ value: "PENDING", label: "Awaiting an answer" },
	{ value: "APPROVED", label: "Approved" },
	{ value: "DECLINED", label: "Declined" },
	{ value: "EXPIRED", label: "Expired" },
];

/**
 * Every request the signed-in account is a party to, on both sides of the ask.
 *
 * The list answers a request exactly once, and only for the account that was asked, so the
 * two buttons appear on a row only when this account is that account and the request is
 * still waiting. A refusal is shown on the row it happened to, because the next thing to do
 * is not the same for every row: a request that is being approved is worth trying again, and
 * one that has been declined is not worth offering at all.
 */
export function MoneyRequestList({
	requests = [],
	total = 0,
	offset = 0,
	filters = { role: "", state: "" },
	actions = {},
	loading = false,
	error = "",
	onFilterChange = () => {},
	onApprove = () => {},
	onDecline = () => {},
	onLoadMore = () => {},
	onRetry = () => {},
}) {
	const hasMore = offset + requests.length < total;

	return (
		<section className="history request-list" aria-labelledby="request-list-heading">
			<div className="history-header">
				<h2 className="history-heading" id="request-list-heading">
					Money requests
				</h2>
				<span className="history-count">
					{total} {total === 1 ? "request" : "requests"}
				</span>
			</div>

			<div className="request-filters" role="group" aria-label="Narrow the requests shown">
				<div className="request-filter-group" role="group" aria-label="Which side of the request">
					{SIDE_FILTERS.map((option) => (
						<button
							className={filters.role === option.value ? "request-chip request-chip-on" : "request-chip"}
							type="button"
							key={option.value || "all"}
							aria-pressed={filters.role === option.value}
							disabled={loading}
							onClick={() => onFilterChange({ ...filters, role: option.value })}
						>
							{option.label}
						</button>
					))}
				</div>

				<label className="request-filter-select">
					<span className="request-filter-label">State</span>
					<select
						className="field-input request-select"
						value={filters.state}
						disabled={loading}
						onChange={(event) => onFilterChange({ ...filters, state: event.target.value })}
					>
						{STATE_FILTERS.map((option) => (
							<option value={option.value} key={option.value || "any"}>
								{option.label}
							</option>
						))}
					</select>
				</label>
			</div>

			{error ? (
				<div className="history-error" role="alert">
					<p>{error}</p>
					<button className="secondary-button" type="button" onClick={onRetry}>
						Try again
					</button>
				</div>
			) : null}

			{!error && loading && !requests.length ? (
				<ul className="history-list" aria-live="polite" aria-busy="true">
					{[0, 1].map((row) => (
						<li className="history-row history-row-skeleton" key={row} aria-hidden="true">
							<span className="skeleton-line" />
							<span className="skeleton-line skeleton-line-short" />
						</li>
					))}
				</ul>
			) : null}

			{!error && requests.length ? (
				<ul className="history-list">
					{requests.map((entry) => {
						const action = actions[entry.id] || {};
						const answerable = entry.role === "APPROVER" && entry.state === "PENDING";

						return (
							<li className="history-row request-row" key={entry.id}>
								<div className="history-row-main">
									<span className="history-row-label">
										{entry.role === "APPROVER" ? "Asked of you by " : "You asked "}
										{entry.counterparty.name}
									</span>
									{entry.note ? <span className="history-row-note">“{entry.note}”</span> : null}
									<span className="request-row-meta">
										<span className={`request-state request-state-${entry.state.toLowerCase()}`}>
											{STATE_LABELS[entry.state] || entry.state}
										</span>
										<span className="history-row-moment">
											{entry.role === "APPROVER" ? "Asked" : "You asked"} {formatMoment(entry.createdAt)}
										</span>
									</span>
									{action.error ? (
										<p className="field-error" role="alert">
											{action.error}
										</p>
									) : null}
								</div>

								<div className="history-row-amounts">
									<span className="history-row-amount">{formatMinor(entry.amountMinor)}</span>
									{answerable ? (
										<span className="request-actions">
											<button
												className="secondary-button request-button"
												type="button"
												disabled={action.loading}
												onClick={() => onApprove(entry.id)}
											>
												{action.loading ? "Working…" : "Approve"}
											</button>
											<button
												className="secondary-button request-button"
												type="button"
												disabled={action.loading}
												onClick={() => onDecline(entry.id)}
											>
												Decline
											</button>
										</span>
									) : null}
								</div>
							</li>
						);
					})}
				</ul>
			) : null}

			{!error && !loading && !requests.length ? (
				<div className="history-empty">
					<h3>No requests yet</h3>
					<p>
						Ask another account for money above, or nothing here means nobody has asked you either. Requests are answerable for
						a week.
					</p>
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
