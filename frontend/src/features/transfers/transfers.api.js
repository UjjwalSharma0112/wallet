import { request } from "../../shared/api/client.js";

/** R2 — sending money to another account's wallet. */
export const transfersApi = {
	send(recipientEmail, amountMinor, note) {
		return request("/transfers", {
			method: "POST",
			body: JSON.stringify({
				recipientEmail,
				amountMinor,
				...(note ? { note } : {}),
			}),
		});
	},
};
