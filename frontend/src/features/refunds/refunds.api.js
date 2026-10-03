import { request } from "../../shared/api/client.js";

/**
 * R5 — giving a completed payment back, in whole or in part.
 */
export const refundsApi = {
	create(originalTransferId, amountMinor, note) {
		return request("/refunds", {
			method: "POST",
			body: JSON.stringify({
				originalTransferId,
				amountMinor,
				...(note ? { note } : {}),
			}),
		});
	},

	list({ limit = 0, offset = 0 } = {}) {
		const query = new URLSearchParams();

		if (limit) query.set("limit", String(limit));
		if (offset) query.set("offset", String(offset));

		const search = query.toString();

		return request(`/refunds${search ? `?${search}` : ""}`);
	},
};
