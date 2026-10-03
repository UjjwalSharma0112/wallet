/**
 * The way back out of a focused page to the one that lists them.
 *
 * A button rather than a link, because nothing is being linked to: the app decides which page
 * is on screen, and a link would promise an address that does not change.
 */
export function BackButton({ onBack, label = "Back" }) {
	return (
		<button className="back-button" type="button" onClick={onBack}>
			<span aria-hidden="true">←</span> {label}
		</button>
	);
}
