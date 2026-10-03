import { useState } from "react";

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function SignInForm({ error = "", loading = false, onSignIn }) {
	const [email, setEmail] = useState("");
	const [password, setPassword] = useState("");
	const [fieldErrors, setFieldErrors] = useState({});

	function validate() {
		const next = {};

		if (!email.trim()) next.email = "Enter the email address on your account.";
		else if (!EMAIL_PATTERN.test(email.trim())) next.email = "Enter a valid email address.";

		if (!password) next.password = "Enter your password.";

		setFieldErrors(next);

		return Object.keys(next).length === 0;
	}

	function handleSubmit(event) {
		event.preventDefault();

		if (!validate()) return;

		onSignIn(email.trim(), password);
	}

	return (
		<section className="signin" aria-labelledby="signin-heading">
			<h2 className="signin-heading" id="signin-heading">
				Sign in to your wallet
			</h2>
			<p className="signin-intro">Your balance and transaction history are visible only to the account that owns them.</p>

			<form className="signin-form" onSubmit={handleSubmit} noValidate>
				<div className="field">
					<label className="field-label" htmlFor="email">
						Email
					</label>
					<input
						className={fieldErrors.email ? "field-input field-input-invalid" : "field-input"}
						id="email"
						name="email"
						type="email"
						autoComplete="username"
						value={email}
						disabled={loading}
						aria-invalid={Boolean(fieldErrors.email)}
						aria-describedby={fieldErrors.email ? "email-error" : undefined}
						onChange={(event) => setEmail(event.target.value)}
					/>
					{fieldErrors.email ? (
						<p className="field-error" id="email-error" role="alert">
							{fieldErrors.email}
						</p>
					) : null}
				</div>

				<div className="field">
					<label className="field-label" htmlFor="password">
						Password
					</label>
					<input
						className={fieldErrors.password ? "field-input field-input-invalid" : "field-input"}
						id="password"
						name="password"
						type="password"
						autoComplete="current-password"
						value={password}
						disabled={loading}
						aria-invalid={Boolean(fieldErrors.password)}
						aria-describedby={fieldErrors.password ? "password-error" : undefined}
						onChange={(event) => setPassword(event.target.value)}
					/>
					{fieldErrors.password ? (
						<p className="field-error" id="password-error" role="alert">
							{fieldErrors.password}
						</p>
					) : null}
				</div>

				{error ? (
					<p className="form-error" role="alert">
						{error}
					</p>
				) : null}

				<button className="primary-button" type="submit" disabled={loading}>
					{loading ? "Signing in…" : "Sign in"}
				</button>
			</form>
		</section>
	);
}
