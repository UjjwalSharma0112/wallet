import { formatMinor, formatMoment } from "./wallet-format.js";

function CardMessage({ children, tone, action, onAction }) {
	return (
		<section className={`wallet-card wallet-card-${tone}`} role={tone === "error" ? "alert" : "status"} aria-live="polite">
			<p>{children}</p>
			{action ? (
				<button className="secondary-button" type="button" onClick={onAction}>
					{action}
				</button>
			) : null}
		</section>
	);
}

export function WalletBalanceCard({ wallet, loading = false, error = "", onRetry = () => {} }) {
	if (loading) return <CardMessage tone="loading">Loading your available balance…</CardMessage>;
	if (error) return <CardMessage tone="error">{error}</CardMessage>;
	if (!wallet) return null;

	return (
		<section className="wallet-card wallet-card-balance" aria-labelledby="balance-heading">
			<p className="wallet-card-label" id="balance-heading">
				Available balance
			</p>
			<p className="wallet-card-amount">{formatMinor(wallet.availableBalanceMinor)}</p>

			<dl className="wallet-card-meta">
				<div className="wallet-card-meta-item">
					<dt>Currency</dt>
					<dd>{wallet.currency}</dd>
				</div>
				<div className="wallet-card-meta-item">
					<dt>Wallet opened</dt>
					<dd>{formatMoment(wallet.createdAt)}</dd>
				</div>
				<div className="wallet-card-meta-item">
					<dt>Wallet ID</dt>
					<dd className="wallet-card-id">{wallet.id}</dd>
				</div>
			</dl>
		</section>
	);
}
