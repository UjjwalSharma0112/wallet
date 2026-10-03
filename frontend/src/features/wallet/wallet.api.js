import { request } from "../../shared/api/client.js";

export const walletApi = {
	balance: () => request("/wallet"),
	transactions: (limit, offset) => request(`/wallet/transactions?limit=${limit}&offset=${offset}`),
};
