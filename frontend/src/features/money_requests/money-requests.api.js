import { request } from "../../shared/api/client.js";

/**
 * R4 — asking another account for money.
 *
 * The three calls mirror the three routes. Asking takes the same body a transfer takes,
 * because a request and a payment are the same two things said at different times.
 *
 * Approving answers with the request, the transfer that paid it, and the approver's own
 * wallet. That wallet is the reason the answer is worth having: it is the balance that
 * actually settled, so the screen does not have to ask a second question to find out.
 */
export const moneyRequestsApi = {
	create(approverEmail, amountMinor, note) {
		return request("/money-requests", {
			method: "POST",
			body: JSON.stringify({
				recipientEmail: approverEmail,
				amountMinor,
				...(note ? { note } : {}),
			}),
		});
	},

	list({ role = "", state = "", limit = 0, offset = 0 } = {}) {
		const query = new URLSearchParams();

		if (role) query.set("role", role);
		if (state) query.set("state", state);
		if (limit) query.set("limit", String(limit));
		if (offset) query.set("offset", String(offset));

		const search = query.toString();

		return request(`/money-requests${search ? `?${search}` : ""}`);
	},

	approve(requestId) {
		return request(`/money-requests/${requestId}/approval`, { method: "POST" });
	},

	decline(requestId) {
		return request(`/money-requests/${requestId}/decline`, { method: "POST" });
	},
};
