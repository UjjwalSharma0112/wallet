import { useEffect, useState } from "react";
import { formatMinor, parseRupeesToMinor } from "../wallet/wallet-format.js";

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const NOTE_LIMIT = 140;
const EMPTY_FORM = { approverEmail: "", amount: "", note: "" };

/**
 * Ask another account for money.
 *
 * Asking moves nothing. The account named here is written on the request, and the money only
 * moves if that account approves it, through the same transfer engine a direct payment uses.
 * The form therefore says what will be asked for rather than what will be sent, and it never
 * treats a local check as permission for anything: the service decides every outcome.
 *
 * Messages the service returns are shown under the field they came from. A refusal that is
 * not about a field — asking somebody who does not exist, asking yourself — is shown once
 * above the actions instead.
 */
export function MoneyRequestForm({ onAsk, loading = false, error = "", fieldErrors = {}, notice = "", resetKey = 0 }) {
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

		if (!text) return { error: "Enter an amount to ask for." };

		const minor = parseRupeesToMinor(text);

		if (minor === null || minor < 1) {
			return { error: "Enter an amount in rupees, with at most two decimal places." };
		}

		return { minor };
	}

	function validate() {
		const next = {};
		const approverEmail = values.approverEmail.trim();

		if (!approverEmail) next.approverEmail = "Enter the email address of the account you are asking.";
		else if (!EMAIL_PATTERN.test(approverEmail)) next.approverEmail = "Enter a valid email address.";

		const amount = readAmount();

		if (amount.error) next.amount = amount.error;

		if (values.note.length > NOTE_LIMIT) next.note = `Keep the note to ${NOTE_LIMIT} characters or fewer.`;

		setLocalErrors(next);

		if (Object.keys(next).length) return null;

		return { approverEmail, amountMinor: amount.minor, note: values.note.trim() };
	}

	function handleSubmit(event) {
		event.preventDefault();

		if (loading) return;

		const payload = validate();

		if (!payload) return;

		setEdited({});
		onAsk(payload);
	}

	function messageFor(field) {
		if (localErrors[field]) return localErrors[field];
		if (edited[field]) return "";

		const reported = fieldErrors[field];

		return Array.isArray(reported) ? reported.join(" ") : reported || "";
	}

	const amount = readAmount();

	return (
		<section className="transfer request-form" aria-labelledby="request-heading">
			<div className="transfer-header">
				<h2 className="transfer-heading" id="request-heading">
					Ask for money
				</h2>
				<span className="transfer-available">Nothing moves until they approve</span>
			</div>

			<form className="transfer-form" onSubmit={handleSubmit} noValidate>
				<div className="field">
					<label className="field-label" htmlFor="approverEmail">
						Ask
					</label>
					<input
						className={messageFor("approverEmail") ? "field-input field-input-invalid" : "field-input"}
						id="approverEmail"
						name="approverEmail"
						type="email"
						autoComplete="off"
						placeholder="name@wallet.demo"
						value={values.approverEmail}
						disabled={loading}
						aria-invalid={Boolean(messageFor("approverEmail"))}
						aria-describedby={messageFor("approverEmail") ? "approverEmail-error" : undefined}
						onChange={update("approverEmail")}
					/>
					{messageFor("approverEmail") ? (
						<p className="field-error" id="approverEmail-error" role="alert">
							{messageFor("approverEmail")}
						</p>
					) : null}
				</div>

				<div className="transfer-row">
					<div className="field">
						<label className="field-label" htmlFor="requestAmount">
							Amount
						</label>
						<input
							className={messageFor("amount") ? "field-input field-input-invalid" : "field-input"}
							id="requestAmount"
							name="amount"
							type="text"
							inputMode="decimal"
							autoComplete="off"
							placeholder="0.00"
							value={values.amount}
							disabled={loading}
							aria-invalid={Boolean(messageFor("amount"))}
							aria-describedby={messageFor("amount") ? "requestAmount-error" : undefined}
							onChange={update("amount")}
						/>
						{messageFor("amount") ? (
							<p className="field-error" id="requestAmount-error" role="alert">
								{messageFor("amount")}
							</p>
						) : null}
					</div>

					<div className="field">
						<label className="field-label" htmlFor="requestNote">
							Note <span className="field-optional">optional</span>
						</label>
						<input
							className={messageFor("note") ? "field-input field-input-invalid" : "field-input"}
							id="requestNote"
							name="note"
							type="text"
							maxLength={NOTE_LIMIT}
							autoComplete="off"
							placeholder="What it is for"
							value={values.note}
							disabled={loading}
							aria-invalid={Boolean(messageFor("note"))}
							aria-describedby={messageFor("note") ? "requestNote-error" : undefined}
							onChange={update("note")}
						/>
						{messageFor("note") ? (
							<p className="field-error" id="requestNote-error" role="alert">
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
						{loading ? "Sending request…" : "Send request"}
					</button>
					{!loading && amount.minor ? (
						<span className="transfer-confirm">You will ask for {formatMinor(amount.minor)}</span>
					) : null}
				</div>
			</form>
		</section>
	);
}
