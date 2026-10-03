const minorUnitsPerMajor = 100;
const currencyFormatter = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
const momentFormatter = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" });
const amountPattern = /^\d+(\.\d{1,2})?$/;

const entryLabels = {
	OPENING: "Opening balance",
	TRANSFER_IN: "Received",
	TRANSFER_OUT: "Sent",
	REFUND_IN: "Refund received",
	REFUND_OUT: "Refund sent",
};

/** Balances arrive as integer paise so no amount is ever stored as a float. */
export function formatMinor(amountMinor) {
	const amount = Number(amountMinor);

	return currencyFormatter.format((Number.isFinite(amount) ? amount : 0) / minorUnitsPerMajor);
}

/**
 * Read a rupee amount typed by a person as whole paise.
 *
 * The text is split into its two parts and each becomes a whole number, so no rounding
 * of a decimal ever decides what amount is sent. Anything that is not a plain amount with
 * at most two decimal places is refused rather than guessed at.
 */
export function parseRupeesToMinor(text) {
	const trimmed = String(text ?? "").trim();

	if (!amountPattern.test(trimmed)) return null;

	const [rupees, paise = ""] = trimmed.split(".");
	const minor = Number(rupees) * minorUnitsPerMajor + Number(paise.padEnd(2, "0"));

	return Number.isSafeInteger(minor) ? minor : null;
}

export function formatMoment(value) {
	return value ? momentFormatter.format(new Date(value)) : "—";
}

/** Name the other party when a transfer has one, since that is what the row is about. */
export function entryLabel(entry) {
	const base = entryLabels[entry.kind] || entry.kind;

	return entry.counterparty ? `${base} · ${entry.counterparty.name}` : base;
}

/** A note typed on a transfer, shown under the party it was sent to or from. */
export function entryNote(entry) {
	return entry.counterparty?.note || "";
}
