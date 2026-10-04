import { request } from "../../shared/api/client.js";

/**
 * R7 — paying a list of people in one recorded action.
 *
 * The three calls mirror the three routes. Creating a batch pays nothing: it writes a list
 * with every item pending, so a list somebody is still typing is not a list of payments. The
 * money moves when the owner runs it, through the same transfer engine a single payment uses,
 * which is why running it answers with the batch and the owner's own wallet — the balance that
 * actually settled, so the screen never has to ask a second question to find out.
 *
 * The key a batch item spends is derived by the service from the batch and the item's own
 * position, so nothing here sends an idempotency key. The client's part in not paying twice is
 * that running the same batch again is a request to find out what already happened, not a
 * request to pay again.
 */
export const batchesApi = {
	create(items) {
		return request("/batches", { method: "POST", body: JSON.stringify({ items }) });
	},

	list({ state = "", limit = 0, offset = 0 } = {}) {
		const query = new URLSearchParams();

		if (state) query.set("state", state);
		if (limit) query.set("limit", String(limit));
		if (offset) query.set("offset", String(offset));

		const search = query.toString();

		return request(`/batches${search ? `?${search}` : ""}`);
	},

	process(batchId) {
		return request(`/batches/${batchId}/processing`, { method: "POST" });
	},
};

/**
 * The service reports what is wrong with a batch against the item it belongs to, keyed
 * `items.<n>.<field>`. A list of rows is corrected one row at a time, so the messages are
 * gathered per position here rather than leaving a form to read keys it would have to know
 * the shape of.
 */
export function batchFieldErrors(details) {
	const fieldErrors = details?.fieldErrors || {};
	const items = {};

	for (const [path, messages] of Object.entries(fieldErrors)) {
		const [, position, field] = path.split(".");

		if (position === undefined || field === undefined) {
			fieldErrors[path] = messages;

			continue;
		}

		items[position] = { ...items[position], [field]: [].concat(messages).join(" ") };
	}

	return { fieldErrors, items };
}
