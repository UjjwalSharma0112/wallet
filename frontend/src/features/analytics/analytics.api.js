import { request } from "../../shared/api/client.js";

/**
 * The figures for a period, asked of the service rather than worked out here.
 *
 * A period is named by its two calendar dates in UTC, which is the timezone the service
 * buckets on. The client sends no timezone, because sending one would make the answer depend
 * on where the browser is. Both dates are optional: the service fills in whichever one is
 * missing, so asking with no arguments reads the default thirty-day window ending today.
 */
export function analytics(from, to) {
	const range = new URLSearchParams();

	if (from) range.set("from", from);
	if (to) range.set("to", to);

	const query = range.toString();

	return request(`/analytics${query ? `?${query}` : ""}`);
}

/** The windows the section offers. Each is a length of days ending on a named day. */
export const PERIOD_PRESETS = [
	{ id: "7", label: "7 days", days: 7 },
	{ id: "30", label: "30 days", days: 30 },
	{ id: "90", label: "90 days", days: 90 },
];

/**
 * The `days`-long window ending on the day a period ends on.
 *
 * Anchored on the day the service reported rather than on the browser's clock, so the
 * presets keep describing the period that is on screen: a date range the reader typed by
 * hand is still a period with an end, and "30 days" of it means the thirty days up to that
 * end rather than thirty days from this morning.
 */
export function windowEndingOn(to, days) {
	return { from: shiftDays(to, -(days - 1)), to };
}

/** Move a `YYYY-MM-DD` date by whole days, in UTC, and return it in the same shape. */
function shiftDays(date, days) {
	const moment = new Date(`${date}T00:00:00Z`);

	moment.setUTCDate(moment.getUTCDate() + days);

	return moment.toISOString().slice(0, 10);
}

/**
 * The largest single day in a series, so every bar is drawn against the same scale.
 *
 * Both the money and the count are considered: a day with four ₹1 payments is busy even
 * though it moved less money than a day with one large one, and a chart that scaled on money
 * alone would draw it as an empty day.
 */
export function peakOf(volume) {
	return volume.reduce((peak, bucket) => Math.max(peak, bucket.sentMinor + bucket.receivedMinor, bucket.transactionCount), 0);
}
