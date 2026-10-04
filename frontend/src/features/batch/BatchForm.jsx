import { useEffect, useState } from "react";
import { formatMinor, parseRupeesToMinor } from "../wallet/wallet-format.js";

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const NOTE_LIMIT = 140;
const FIRST_ITEM = { recipientEmail: "", amount: "", note: "" };

/** The most rows the service will hold, and therefore the most worth offering. */
const MAX_ITEMS = 25;

/**
 * Forget a row or a field that has nothing left to say, so what is shown is only ever a
 * message that still has something to point at. A row is kept while any of its fields has a
 * message, which is also what decides whether the row is drawn as wrong at all.
 */
function pruned(errors) {
	return Object.fromEntries(
		Object.entries(errors)
			.map(([position, messages]) => [position, Object.fromEntries(Object.entries(messages || {}).filter(([, message]) => message))])
			.filter(([, messages]) => Object.keys(messages).length),
	);
}

/**
 * Write out a list of payments and save it without paying any of them.
 *
 * Saving a batch moves nothing. The list is written with every item pending and the money only
 * moves when the batch is run, which is what makes it safe to build a list in stages: a row
 * that is still being typed cannot be paid by accident, and a list that turns out to be wrong
 * can be corrected before any of it is spent.
 *
 * Every row is checked here so the whole list can be corrected in one pass, and the service
 * still decides every outcome — a local check here is a convenience, never a permission.
 */
export function BatchForm({ onSave, loading = false, error = "", itemErrors = {}, notice = "", resetKey = 0 }) {
	const [rows, setRows] = useState([{ ...FIRST_ITEM }]);
	const [localErrors, setLocalErrors] = useState({});
	const [touched, setTouched] = useState(false);

	useEffect(() => {
		setRows([{ ...FIRST_ITEM }]);
		setLocalErrors({});
		setTouched(false);
	}, [resetKey]);

	function addRow() {
		if (rows.length >= MAX_ITEMS) return;

		setRows((current) => [...current, { ...FIRST_ITEM }]);
	}

	function removeRow(position) {
		setRows((current) => (current.length === 1 ? current : current.filter((_, index) => index !== position)));
		setLocalErrors((current) => {
			// The rows after the removed one move up, so their messages have to move with them,
			// or a message would be left pointing at whatever now sits where its row was.
			const shifted = {};

			for (const [index, messages] of Object.entries(current)) {
				if (Number(index) === position) continue;

				shifted[Number(index) > position ? Number(index) - 1 : Number(index)] = messages;
			}

			return pruned(shifted);
		});
	}

	function update(position, field) {
		return (event) => {
			const { value } = event.target;

			setRows((current) => current.map((row, index) => (index === position ? { ...row, [field]: value } : row)));
			// Editing a field clears what was said about that field, so a message never outlives
			// the mistake it was about.
			setLocalErrors((current) => pruned({ ...current, [position]: { ...current[position], [field]: "" } }));
		};
	}

	/** Read one row as paise, or say why what is typed is not an amount. */
	function readAmount(row) {
		const text = row.amount.trim();

		if (!text) return { error: "Enter an amount." };

		const minor = parseRupeesToMinor(text);

		if (minor === null || minor < 1) return { error: "Enter an amount in rupees, with at most two decimal places." };

		return { minor };
	}

	function validate() {
		const next = {};

		rows.forEach((row, position) => {
			const email = row.recipientEmail.trim();
			const messages = {};

			if (!email) messages.recipientEmail = "Enter the email address to pay.";
			else if (!EMAIL_PATTERN.test(email)) messages.recipientEmail = "Enter a valid email address.";

			const amount = readAmount(row);

			if (amount.error) messages.amount = amount.error;

			if (row.note.length > NOTE_LIMIT) messages.note = `Keep the note to ${NOTE_LIMIT} characters or fewer.`;

			if (Object.keys(messages).length) next[position] = messages;
		});

		setLocalErrors(next);

		if (Object.keys(next).length) return null;

		return rows.map((row, position) => ({
			recipientEmail: row.recipientEmail.trim(),
			amountMinor: readAmount(row).minor,
			note: row.note.trim() || undefined,
		}));
	}

	function handleSubmit(event) {
		event.preventDefault();

		if (loading) return;

		const items = validate();

		if (!items) return;

		setTouched(true);
		onSave(items);
	}

	function messageFor(position, field) {
		const local = localErrors[position]?.[field];
		const reported = itemErrors[position]?.[field];

		if (local) return local;

		// A message from the service is only worth showing once the row has been worked on,
		// otherwise a batch that failed to save for some other reason would light up every row
		// the moment it was opened.
		return touched ? reported || "" : "";
	}

	const total = rows.reduce((sum, row) => sum + (readAmount(row).minor ?? 0), 0);

	return (
		<section className="transfer batch-form" aria-labelledby="batch-heading">
			<div className="transfer-header">
				<h2 className="transfer-heading" id="batch-heading">
					Batch transfer
				</h2>
				<span className="transfer-available">Nothing moves until you run the batch</span>
			</div>

			<form className="transfer-form" onSubmit={handleSubmit} noValidate>
				<ol className="batch-rows">
					{rows.map((row, position) => (
						<li className="batch-row" key={position}>
							<div className="field">
								<label className="field-label" htmlFor={`batchRecipient-${position}`}>
									Payment {position + 1} — to
								</label>
								<input
									className={messageFor(position, "recipientEmail") ? "field-input field-input-invalid" : "field-input"}
									id={`batchRecipient-${position}`}
									name={`recipientEmail-${position}`}
									type="email"
									autoComplete="off"
									placeholder="name@wallet.demo"
									value={row.recipientEmail}
									disabled={loading}
									aria-invalid={Boolean(messageFor(position, "recipientEmail"))}
									aria-describedby={
										messageFor(position, "recipientEmail") ? `batchRecipient-${position}-error` : undefined
									}
									onChange={update(position, "recipientEmail")}
								/>
								{messageFor(position, "recipientEmail") ? (
									<p className="field-error" id={`batchRecipient-${position}-error`} role="alert">
										{messageFor(position, "recipientEmail")}
									</p>
								) : null}
							</div>

							<div className="transfer-row">
								<div className="field">
									<label className="field-label" htmlFor={`batchAmount-${position}`}>
										Amount
									</label>
									<input
										className={messageFor(position, "amount") ? "field-input field-input-invalid" : "field-input"}
										id={`batchAmount-${position}`}
										name={`amount-${position}`}
										type="text"
										inputMode="decimal"
										autoComplete="off"
										placeholder="0.00"
										value={row.amount}
										disabled={loading}
										aria-invalid={Boolean(messageFor(position, "amount"))}
										aria-describedby={messageFor(position, "amount") ? `batchAmount-${position}-error` : undefined}
										onChange={update(position, "amount")}
									/>
									{messageFor(position, "amount") ? (
										<p className="field-error" id={`batchAmount-${position}-error`} role="alert">
											{messageFor(position, "amount")}
										</p>
									) : null}
								</div>

								<div className="field">
									<label className="field-label" htmlFor={`batchNote-${position}`}>
										Note <span className="field-optional">optional</span>
									</label>
									<input
										className={messageFor(position, "note") ? "field-input field-input-invalid" : "field-input"}
										id={`batchNote-${position}`}
										name={`note-${position}`}
										type="text"
										maxLength={NOTE_LIMIT}
										autoComplete="off"
										placeholder="What it is for"
										value={row.note}
										disabled={loading}
										aria-invalid={Boolean(messageFor(position, "note"))}
										aria-describedby={messageFor(position, "note") ? `batchNote-${position}-error` : undefined}
										onChange={update(position, "note")}
									/>
									{messageFor(position, "note") ? (
										<p className="field-error" id={`batchNote-${position}-error`} role="alert">
											{messageFor(position, "note")}
										</p>
									) : null}
								</div>
							</div>

							{rows.length > 1 ? (
								<button className="link-button" type="button" disabled={loading} onClick={() => removeRow(position)}>
									Remove payment {position + 1}
								</button>
							) : null}
						</li>
					))}
				</ol>

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
						{loading ? "Saving batch…" : `Save batch of ${rows.length}`}
					</button>
					<button className="secondary-button" type="button" disabled={loading || rows.length >= MAX_ITEMS} onClick={addRow}>
						Add another payment
					</button>
					{!loading && total > 0 ? <span className="transfer-confirm">{formatMinor(total)} in total</span> : null}
				</div>
			</form>
		</section>
	);
}
