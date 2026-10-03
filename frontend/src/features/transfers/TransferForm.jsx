import { useEffect, useState } from "react";
import { formatMinor, parseRupeesToMinor } from "../wallet/wallet-format.js";

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const NOTE_LIMIT = 140;
const EMPTY_FORM = { recipientEmail: "", amount: "", note: "" };

/**
 * Send money to another account's wallet.
 *
 * The amount is read here so the button can say what will be sent, but the service
 * decides what actually happens: this form never treats a local check as permission to
 * move money. Messages the service returns are shown under the field they came from.
 */
export function TransferForm({ balanceMinor = null, onSend, loading = false, error = "", fieldErrors = {}, notice = "", resetKey = 0 }) {
	const [values, setValues] = useState(EMPTY_FORM);
	const [localErrors, setLocalErrors] = useState({});
	const [edited, setEdited] = useState({});

	useEffect(() => {
		setValues(EMPTY_FORM);
		setLocalErrors({});
		setEdited({});
	}, [resetKey]);

	function update(field) {
		return (event) => {
			const { value } = event.target;

			setValues((current) => ({ ...current, [field]: value }));
			setLocalErrors((current) => ({ ...current, [field]: "" }));
			setEdited((current) => ({ ...current, [field]: true }));
		};
	}

	/** Read the typed amount as whole paise, or report why the text is not an amount. */
	function readAmount() {
		const text = values.amount.trim();

		if (!text) return { error: "Enter an amount to send." };

		const minor = parseRupeesToMinor(text);

		if (minor === null || minor < 1) {
			return { error: "Enter an amount in rupees, with at most two decimal places." };
		}

		return { minor };
	}

	function validate() {
		const next = {};
		const recipientEmail = values.recipientEmail.trim();

		if (!recipientEmail) next.recipientEmail = "Enter the email address of the account you are paying.";
		else if (!EMAIL_PATTERN.test(recipientEmail)) next.recipientEmail = "Enter a valid email address.";

		const amount = readAmount();

		if (amount.error) next.amount = amount.error;

		if (values.note.length > NOTE_LIMIT) next.note = `Keep the note to ${NOTE_LIMIT} characters or fewer.`;

		setLocalErrors(next);

		if (Object.keys(next).length) return null;

		return { recipientEmail, amountMinor: amount.minor, note: values.note.trim() };
	}

	function handleSubmit(event) {
		event.preventDefault();

		if (loading) return;

		const payload = validate();

		if (!payload) return;

		setEdited({});
		onSend(payload);
	}

	function messageFor(field) {
		if (localErrors[field]) return localErrors[field];
		if (edited[field]) return "";

		const reported = fieldErrors[field];

		return Array.isArray(reported) ? reported.join(" ") : reported || "";
	}

	const amount = readAmount();

	return (
		<section className="transfer" aria-labelledby="transfer-heading">
			<div className="transfer-header">
				<h2 className="transfer-heading" id="transfer-heading">
					Send money
				</h2>
				{balanceMinor !== null ? <span className="transfer-available">Available {formatMinor(balanceMinor)}</span> : null}
			</div>

			<form className="transfer-form" onSubmit={handleSubmit} noValidate>
				<div className="field">
					<label className="field-label" htmlFor="recipientEmail">
						To
					</label>
					<input
						className={messageFor("recipientEmail") ? "field-input field-input-invalid" : "field-input"}
						id="recipientEmail"
						name="recipientEmail"
						type="email"
						autoComplete="off"
						placeholder="name@wallet.demo"
						value={values.recipientEmail}
						disabled={loading}
						aria-invalid={Boolean(messageFor("recipientEmail"))}
						aria-describedby={messageFor("recipientEmail") ? "recipientEmail-error" : undefined}
						onChange={update("recipientEmail")}
					/>
					{messageFor("recipientEmail") ? (
						<p className="field-error" id="recipientEmail-error" role="alert">
							{messageFor("recipientEmail")}
						</p>
					) : null}
				</div>

				<div className="transfer-row">
					<div className="field">
						<label className="field-label" htmlFor="amount">
							Amount
						</label>
						<input
							className={messageFor("amount") ? "field-input field-input-invalid" : "field-input"}
							id="amount"
							name="amount"
							type="text"
							inputMode="decimal"
							autoComplete="off"
							placeholder="0.00"
							value={values.amount}
							disabled={loading}
							aria-invalid={Boolean(messageFor("amount"))}
							aria-describedby={messageFor("amount") ? "amount-error" : undefined}
							onChange={update("amount")}
						/>
						{messageFor("amount") ? (
							<p className="field-error" id="amount-error" role="alert">
								{messageFor("amount")}
							</p>
						) : null}
					</div>

					<div className="field">
						<label className="field-label" htmlFor="note">
							Note <span className="field-optional">optional</span>
						</label>
						<input
							className={messageFor("note") ? "field-input field-input-invalid" : "field-input"}
							id="note"
							name="note"
							type="text"
							maxLength={NOTE_LIMIT}
							autoComplete="off"
							placeholder="What it was for"
							value={values.note}
							disabled={loading}
							aria-invalid={Boolean(messageFor("note"))}
							aria-describedby={messageFor("note") ? "note-error" : undefined}
							onChange={update("note")}
						/>
						{messageFor("note") ? (
							<p className="field-error" id="note-error" role="alert">
								{messageFor("note")}
							</p>
						) : null}
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
						{loading ? "Sending…" : "Send money"}
					</button>
					{!loading && amount.minor ? <span className="transfer-confirm">You will send {formatMinor(amount.minor)}</span> : null}
				</div>
			</form>
		</section>
	);
}
