const baseUrl = import.meta.env.VITE_API_URL || "/api/v1";
const tokenKey = "wallet-session-token";
const expiryCodes = ["AUTH_REQUIRED", "INVALID_TOKEN", "ACCOUNT_UNAVAILABLE"];
let sessionToken = localStorage.getItem(tokenKey) || "";

export function setSessionToken(token) {
	sessionToken = token || "";

	if (sessionToken) localStorage.setItem(tokenKey, sessionToken);
	else localStorage.removeItem(tokenKey);
}

export const hasSessionToken = () => Boolean(sessionToken);

export async function request(path, options = {}) {
	const response = await fetch(`${baseUrl}${path}`, {
		...options,
		headers: {
			"Content-Type": "application/json",
			...(sessionToken ? { Authorization: `Bearer ${sessionToken}` } : {}),
			...options.headers,
		},
	});

	if (response.status === 204) return null;

	const contentType = response.headers?.get?.("content-type") || "";
	const payload = contentType.includes("application/json") || !response.headers ? await response.json() : null;

	if (!response.ok) {
		const code = payload?.error?.code;
		const message = payload?.error?.message || `Wallet service returned ${response.status}.`;

		if (response.status === 401 && expiryCodes.includes(code)) {
			setSessionToken("");
			window.dispatchEvent(new CustomEvent("wallet-session-expired", { detail: message }));
		}

		throw new Error(message);
	}

	if (!payload || !("data" in payload)) throw new Error("Wallet service returned an invalid response.");

	return payload.data;
}
