import { useEffect, useState } from "react";
import { formatMinor, parseRupeesToMinor } from "../wallet/wallet-format.js";

const NOTE_LIMIT = 140;

/**
 * Return some, or all, of a completed payment to the account that sent it.
 *
 * This moves money out of the payee's own wallet, so the amount is capped at what the
 * original payment still has left to give back. The form reads the cap the server last
 * published but never trusts it: a concurrent refund may have taken some of it, and the
 * server is the one that decides what can actually move.
 */
export function RefundForm({ transfer, onRefund, loading = false, error = "", fieldErrors = {}, notice = "", resetKey = 0 }) {
	const [amount, setAmount] = useState("");
	const [note, setNote] = useState("");
	const [localError, setLocalError] = useState("");

	useEffect(() => {
		setAmount("");
		setNote("");
		setLocalError("");
	}, [resetKey, transfer?.transactionId]);

	const remainingMinor = transfer?.remainingRefundableMinor ?? null;

	/** Read the typed amount as whole paise, or report why the text is not an amount. */
	function readAmount() {
		const text = amount.trim();

		if (!text) return { error: "Enter an amount to refund." };

		const minor = parseRupeesToMinor(text);

		if (minor === null || minor < 1) {
			return { error: "Enter an amount in rupees, with at most two decimal places." };
		}

		if (remainingMinor !== null && minor > remainingMinor) {
			return { error: `This payment has only ${formatMinor(remainingMinor)} left to give back.` };
		}

		return { minor };
	}

	function handleSubmit(event) {
		event.preventDefault();

		if (loading || !transfer) return;

		const read = readAmount();

		if (read.error) {
			setLocalError(read.error);

			return;
		}

		if (note.length > NOTE_LIMIT) {
			setLocalError(`Keep the note to ${NOTE_LIMIT} characters or fewer.`);

			return;
		}

		setLocalError("");
		onRefund({ originalTransferId: transfer.transactionId, amountMinor: read.minor, note: note.trim() });
	}

	if (!transfer) return null;

	const amountMessage =
		localError ||
		(fieldErrors.amountMinor
			? Array.isArray(fieldErrors.amountMinor)
				? fieldErrors.amountMinor.join(" ")
				: fieldErrors.amountMinor
			: "");
	const amountValue = readAmount();

	return (
		<section className="transfer refund-form" aria-labelledby="refund-heading">
			<div className="transfer-header">
				<h2 className="transfer-heading" id="refund-heading">
					Refund a payment
				</h2>
				<span className="transfer-available">Up to {formatMinor(remainingMinor ?? 0)} left to give back</span>
			</div>

			<form className="transfer-form" onSubmit={handleSubmit} noValidate>
				<p className="refund-target" data-testid="refund-target">
					Returning{" "}
					{transfer.counterparty?.name ? (
						<>
							to <strong>{transfer.counterparty.name}</strong>
						</>
					) : (
						"to the sender"
					)}{" "}
					— paid
					{` ${formatMinor(transfer.amountMinor)}${transfer.counterparty?.note ? ` for “${transfer.counterparty.note}”` : ""}.`}
				</p>

				<div className="transfer-row">
					<div className="field">
						<label className="field-label" htmlFor="refundAmount">
							Amount
						</label>
						<input
							className={amountMessage ? "field-input field-input-invalid" : "field-input"}
							id="refundAmount"
							name="amount"
							type="text"
							inputMode="decimal"
							autoComplete="off"
							placeholder="0.00"
							value={amount}
							disabled={loading}
							aria-invalid={Boolean(amountMessage)}
							onChange={(event) => {
								setAmount(event.target.value);
								setLocalError("");
							}}
						/>
						{amountMessage ? (
							<p className="field-error" role="alert">
								{amountMessage}
							</p>
						) : null}
					</div>

					<div className="field">
						<label className="field-label" htmlFor="refundNote">
							Note <span className="field-optional">optional</span>
						</label>
						<input
							className="field-input"
							id="refundNote"
							name="note"
							type="text"
							maxLength={NOTE_LIMIT}
							autoComplete="off"
							placeholder="Why it is being returned"
							value={note}
							disabled={loading}
							onChange={(event) => setNote(event.target.value)}
						/>
					</div>
				</div>

				{error ? (
					<p className="form-error" role="alert">
						{error}
					</p>
				) : null}

				{notice ? (
					<p className="transfer-notice" role="status">
						{notice}
					</p>
				) : null}

				<div className="transfer-actions">
					<button className="primary-button" type="submit" disabled={loading}>
						{loading ? "Refunding…" : "Refund"}
					</button>
					<button className="secondary-button" type="button" disabled={loading} onClick={() => onCancel()}>
						Cancel
					</button>
					{!loading && amountValue.minor ? (
						<span className="transfer-confirm">You will return {formatMinor(amountValue.minor)}</span>
					) : null}
				</div>
			</form>
		</section>
	);
}
