import { request } from "../../shared/api/client.js";

/**
 * R2 — sending money to another account's wallet.
 *
 * R3 — `idempotencyKey` names one operation for the sender. The service keeps the key with
 * the transfer it created, so sending it again with the same request returns that transfer
 * instead of sending the money a second time.
 */
export const transfersApi = {
	send(recipientEmail, amountMinor, note, idempotencyKey) {
		return request("/transfers", {
			method: "POST",
			body: JSON.stringify({
				recipientEmail,
				amountMinor,
				...(note ? { note } : {}),
			}),
			...(idempotencyKey ? { headers: { "Idempotency-Key": idempotencyKey } } : {}),
		});
	},
};
