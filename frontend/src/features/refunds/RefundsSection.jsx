import { formatMinor, formatMoment } from "../wallet/wallet-format.js";
import { RefundForm } from "./RefundForm.jsx";

const STATE_LABELS = {
	PENDING: "Processing",
	COMPLETED: "Refunded",
	FAILED: "Failed",
};

/**
 * The give-back side of the register: every received payment that still has something
 * left to return is listed with a "Refund" action, and the account's own refund history
 * sits beside it. The server stays the one authority on what remains — the amount the
 * form caps at is read from the history decoration and is only a convenience.
 */
export function RefundsSection({
	entries = [],
	refunds = [],
	refundTotal = 0,
	target = null,
	refundLoading = false,
	refundError = "",
	refundFieldErrors = {},
	refundNotice = "",
	refundResetKey = 0,
	refundsLoading = false,
	refundsError = "",
	onSelect = () => {},
	onCancel = () => {},
	onRefund = () => {},
	onRetry = () => {},
}) {
	const eligible = entries.filter((entry) => entry.kind === "TRANSFER_IN" && Number(entry.remainingRefundableMinor || 0) > 0);

	return (
		<section className="history refunds-section" aria-labelledby="refunds-heading">
			<div className="history-header">
				<h2 className="history-heading" id="refunds-heading">
					Giving money back
				</h2>
				<span className="history-count">
					{refundTotal} {refundTotal === 1 ? "refund" : "refunds"}
				</span>
			</div>

			{refundNotice && !target ? (
				<p className="transfer-notice" role="status">
					{refundNotice}
				</p>
			) : null}

			{target ? (
				<RefundForm
					transfer={target}
					onRefund={onRefund}
					onCancel={onCancel}
					loading={refundLoading}
					error={refundError}
					fieldErrors={refundFieldErrors}
					notice={refundNotice}
					resetKey={refundResetKey}
				/>
			) : null}

			<h3 className="refund-subheading">Payments with money left to return</h3>

			{eligible.length ? (
				<ul className="history-list">
					{eligible.map((entry) => (
						<li className="history-row" key={entry.id}>
							<div className="history-row-main">
								<span className="history-row-label">Received from {entry.counterparty?.name ?? "an account"}</span>
								{entry.counterparty?.note ? <span className="history-row-note">“{entry.counterparty.note}”</span> : null}
								<span className="history-row-moment">
									{formatMoment(entry.createdAt)} · {formatMinor(entry.remainingRefundableMinor)} refundable
								</span>
							</div>

							<div className="history-row-amounts">
								<span className="history-row-amount">{formatMinor(entry.amountMinor)}</span>
								<button
									className="secondary-button request-button"
									type="button"
									disabled={refundLoading}
									onClick={() => onSelect(entry)}
								>
									Refund
								</button>
							</div>
						</li>
					))}
				</ul>
			) : (
				<p className="history-empty-note">No received payments with money left to return.</p>
			)}

			<h3 className="refund-subheading">Refunds you have raised</h3>

			{refundsError ? (
				<div className="history-error" role="alert">
					<p>{refundsError}</p>
					<button className="secondary-button" type="button" onClick={onRetry}>
						Try again
					</button>
				</div>
			) : null}

			{!refundsError && refundsLoading && !refunds.length ? (
				<p className="history-empty-note" role="status">
					Loading…
				</p>
			) : null}

			{!refundsError && !refundsLoading && !refunds.length ? (
				<p className="history-empty-note">You have not raised a refund yet.</p>
			) : null}

			{refunds.length ? (
				<ul className="history-list">
					{refunds.map((refund) => (
						<li className="history-row" key={refund.id}>
							<div className="history-row-main">
								<span className="history-row-label">Returned to {refund.counterparty?.name ?? "the sender"}</span>
								{refund.note ? <span className="history-row-note">“{refund.note}”</span> : null}
								<span className="request-row-meta">
									<span
										className={`request-state request-state-${refund.state === "COMPLETED" ? "approved" : refund.state === "FAILED" ? "declined" : "pending"}`}
									>
										{STATE_LABELS[refund.state] || refund.state}
									</span>
									<span className="history-row-moment">{formatMoment(refund.createdAt)}</span>
								</span>
							</div>

							<div className="history-row-amounts">
								<span className="history-row-amount">{formatMinor(refund.amountMinor)}</span>
							</div>
						</li>
					))}
				</ul>
			) : null}
		</section>
	);
}
