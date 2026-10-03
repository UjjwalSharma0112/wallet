const minorUnitsPerMajor = 100;
const currencyFormatter = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
const momentFormatter = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" });

const entryLabels = {
	OPENING: "Opening balance",
};

/** Balances arrive as integer paise so no amount is ever stored as a float. */
export function formatMinor(amountMinor) {
	const amount = Number(amountMinor);

	return currencyFormatter.format((Number.isFinite(amount) ? amount : 0) / minorUnitsPerMajor);
}

export function formatMoment(value) {
	return value ? momentFormatter.format(new Date(value)) : "—";
}

export function entryLabel(entry) {
	return entryLabels[entry.kind] || entry.kind;
}
