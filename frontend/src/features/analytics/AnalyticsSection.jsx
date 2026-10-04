import { useEffect, useRef } from "react";

import { PERIOD_PRESETS, peakOf, windowEndingOn } from "./analytics.api.js";
import { formatMinor } from "../wallet/wallet-format.js";

const METRICS = [
	{ id: "sentMinor", label: "Total sent", direction: "out" },
	{ id: "receivedMinor", label: "Total received", direction: "in" },
	{ id: "transactionCount", label: "Transactions", count: true },
	{ id: "netMovementMinor", label: "Net movement", direction: "net" },
	{ id: "averageAmountMinor", label: "Average amount" },
];

const BAR_MINIMUM_PERCENT = 3;

/**
 * What this account's own activity adds up to over a period.
 *
 * This is not the history with a chart around it. History is a register of individual
 * movements, and anything it can answer by being read row by row belongs there. What is here
 * is measured by the service over the whole ledger, which is why a day with nothing in it is
 * still a day — a column of no height rather than a missing one — and why the figures do not
 * move when the page of history happens to be reloaded.
 *
 * Every figure arrives ready-made. Nothing on this screen is worked out from rows the browser
 * holds, because a total that depends on how much of the register has been fetched is not a
 * total.
 */
export function AnalyticsSection({ figures = null, fromDate = "", toDate = "", loading = false, error = "", onSelectPeriod, onRetry }) {
	const shownFrom = fromDate;
	const shownTo = toDate;
	const activePreset = presetFor(shownFrom, shownTo);
	const empty = Boolean(figures) && figures.transactionCount === 0;

	function choosePreset(days) {
		onSelectPeriod(windowEndingOn(shownTo, days));
	}

	function changeFrom(value) {
		if (value && shownTo && value <= shownTo) onSelectPeriod({ from: value, to: shownTo });
	}

	function changeTo(value) {
		if (value && shownFrom && shownFrom <= value) onSelectPeriod({ from: shownFrom, to: value });
	}

	return (
		<section className="analytics" aria-labelledby="analytics-heading">
			<div className="history-header">
				<h2 className="history-heading" id="analytics-heading">
					Analytics
				</h2>
				<span className="history-count">
					{shownFrom && shownTo
						? `${figures ? figures.transactionCount : "…"} ${
								figures && figures.transactionCount === 1 ? "movement" : "movements"
							} in ${shownFrom} → ${shownTo}`
						: "Loading period…"}
				</span>
			</div>

			<div className="analytics-controls">
				<div className="request-filter-group" role="group" aria-label="Choose a period">
					{PERIOD_PRESETS.map((preset) => (
						<button
							className={activePreset === preset.days ? "request-chip request-chip-on" : "request-chip"}
							key={preset.id}
							type="button"
							aria-pressed={activePreset === preset.days}
							disabled={!shownTo || loading}
							onClick={() => choosePreset(preset.days)}
						>
							{preset.label}
						</button>
					))}
				</div>

				<div className="analytics-range">
					<label className="request-filter-label" htmlFor="analytics-from">
						From
					</label>
					<input
						aria-describedby="analytics-range-note"
						className="request-select"
						id="analytics-from"
						type="date"
						value={shownFrom}
						max={shownTo || undefined}
						disabled={loading}
						onChange={(event) => changeFrom(event.target.value)}
					/>
					<label className="request-filter-label" htmlFor="analytics-to">
						To
					</label>
					<input
						aria-describedby="analytics-range-note"
						className="request-select"
						id="analytics-to"
						type="date"
						value={shownTo}
						min={shownFrom || undefined}
						disabled={loading}
						onChange={(event) => changeTo(event.target.value)}
					/>
				</div>
			</div>

			<p className="analytics-range-note" id="analytics-range-note">
				Days are counted in UTC, which is the timezone the service buckets on, so a day is the same day whoever reads it. Both
				endpoints are included, and a period covers at most 366 days.
			</p>

			{error ? (
				<div className="history-error" role="alert">
					<p>{error}</p>
					<button className="secondary-button" type="button" onClick={onRetry}>
						Try again
					</button>
				</div>
			) : null}

			{!error && !figures ? (
				<div className="analytics-metrics" aria-busy="true" aria-live="polite">
					{METRICS.map((metric) => (
						<div className="analytics-metric analytics-metric-loading" key={metric.id}>
							<span className="skeleton-line" />
							<span className="skeleton-line skeleton-line-short" />
						</div>
					))}
				</div>
			) : null}

			{!error && figures ? (
				<>
					<div className={loading ? "analytics-metrics analytics-metrics-stale" : "analytics-metrics"} aria-busy={loading}>
						{METRICS.map((metric) => (
							<MetricTile key={metric.id} metric={metric} figures={figures} />
						))}
					</div>

					<VolumeChart figures={figures} loading={loading} />

					{empty ? (
						<div className="history-empty">
							<h3>No activity in this period</h3>
							<p>
								Nothing moved between {shownFrom} and {shownTo}. Every day of it is still counted above, so the shape of the
								period is visible even while it holds nothing.
							</p>
						</div>
					) : null}
				</>
			) : null}
		</section>
	);
}

/**
 * One figure, labeled by what it counts rather than by what it is called.
 *
 * Every money figure carries its direction as a sign, so a number reads as a movement one way
 * or the other rather than as a magnitude whose direction has to be recalled from the label
 * above it. Net movement is what is left after the two, which is a different number from any
 * single movement it came from, so it also says which way it went.
 */
function MetricTile({ metric, figures }) {
	const value = figures[metric.id];
	const classes = ["analytics-metric"];

	if (metric.direction === "out") classes.push("analytics-metric-out");
	if (metric.direction === "in") classes.push("analytics-metric-in");
	if (metric.direction === "net" && value) {
		classes.push(value < 0 ? "analytics-metric-negative" : "analytics-metric-positive");
	}

	return (
		<div className={classes.join(" ")}>
			<span className="analytics-metric-label">{metric.label}</span>
			<span className="analytics-metric-value">{metric.count ? String(value) : signedMinor(value, metric.direction)}</span>
			<span className="analytics-metric-note">{describe(metric, figures)}</span>
		</div>
	);
}

// `formatMinor` writes a negative amount with an ASCII hyphen, which is a hyphen and not a
// minus sign. It was never asked to: nothing else here shows a negative figure, because a
// history row carries its direction in its own column and a balance cannot be negative. These
// tiles do show one, so the sign is taken off the amount and put back as a real minus, and
// the three money tiles then read the same way beside each other.
function signedMinor(value, direction) {
	// Nothing moved, so nothing went anywhere. Signing zero would assert a direction that
	// never happened, which is the same mistake as an empty bucket being left out.
	if (!value) return formatMinor(0);

	if (direction === "out") return `−${formatMinor(value)}`;
	if (direction === "in") return `+${formatMinor(value)}`;

	if (direction === "net") return `${value < 0 ? "−" : "+"}${formatMinor(Math.abs(value))}`;

	// An average is one amount rather than a movement, so it has no direction to carry. The
	// note beneath it says what it is an average of, which is the part a reader needs.
	return formatMinor(value);
}

/**
 * What each figure is a figure of, in one line.
 *
 * A number with no population behind it is the mistake this feature exists to avoid, so the
 * population is written next to it rather than left in a requirements document: the average
 * is over the movements counted above it, and net movement is over the same ones.
 */
function describe(metric, figures) {
	if (metric.id === "sentMinor") return figures.sentMinor ? "money that left" : "nothing left";
	if (metric.id === "receivedMinor") return figures.receivedMinor ? "money that arrived" : "nothing arrived";
	if (metric.id === "transactionCount") return "ledger entries, not attempts";
	if (metric.id === "netMovementMinor") {
		if (!figures.netMovementMinor) return "nothing moved either way";

		return figures.netMovementMinor < 0 ? "less than it started" : "more than it started";
	}

	if (metric.id === "averageAmountMinor")
		return `over ${figures.transactionCount} ${figures.transactionCount === 1 ? "movement" : "movements"}`;

	return "";
}

/**
 * Money out and money in, one bar each per day.
 *
 * Drawn in CSS from the buckets the service returned, so a bar is a day rather than a row and
 * the shape of a quiet period is still a shape. The scale comes from the busiest day in the
 * period as reported, not from a maximum anybody chose, which is what keeps a week of small
 * payments readable next to a day with one large one.
 */
function VolumeChart({ figures, loading }) {
	const peak = peakOf(figures.volume);
	const busy = figures.transactionCount > 0;
	const barsRef = useRef(null);
	const scale = (value) => (peak > 0 && value > 0 ? Math.max(BAR_MINIMUM_PERCENT, (value / peak) * 100) : 0);

	// A window long enough to scroll opens at the wrong end by default. Scrolling starts at the
	// oldest day, so on a phone a ninety-day window shows a flat stretch of nothing and hides
	// every recent movement past it — a reader would conclude from a chart that is merely
	// cropped that there had been no activity at all. The end of the period is the end a
	// reader came for, since every preset ends on the day the service reported, so the newest
	// day is what the scroll opens on. This is a scroll position and nothing else: no figure
	// is touched, and a window that fits needs none of it.
	useEffect(() => {
		const bars = barsRef.current;

		if (bars) bars.scrollLeft = bars.scrollWidth;
	}, [figures.volume]);

	return (
		<div className="analytics-volume" aria-labelledby="analytics-volume-heading">
			<div className="analytics-volume-header">
				<h3 className="analytics-subheading" id="analytics-volume-heading">
					Daily volume
				</h3>
				<div className="analytics-legend">
					<span className="analytics-legend-item">
						<span className="analytics-swatch analytics-swatch-out" aria-hidden="true" />
						Sent
					</span>
					<span className="analytics-legend-item">
						<span className="analytics-swatch analytics-swatch-in" aria-hidden="true" />
						Received
					</span>
				</div>
			</div>

			<div className={loading ? "analytics-bars analytics-bars-loading" : "analytics-bars"} aria-busy={loading} ref={barsRef}>
				{figures.volume.map((bucket) => (
					<div
						className="analytics-bar-column"
						key={bucket.bucket}
						title={`${bucket.bucket}: ${bucket.transactionCount} ${
							bucket.transactionCount === 1 ? "movement" : "movements"
						}, ${formatMinor(bucket.sentMinor)} out, ${formatMinor(bucket.receivedMinor)} in`}
					>
						<div className="analytics-bar-pair">
							<span className="analytics-bar analytics-bar-out" style={{ height: `${scale(bucket.sentMinor)}%` }} />
							<span className="analytics-bar analytics-bar-in" style={{ height: `${scale(bucket.receivedMinor)}%` }} />
						</div>
						<span className="analytics-bar-count">{bucket.transactionCount || ""}</span>
					</div>
				))}
			</div>

			<div className="analytics-volume-axis">
				<span>{figures.volume[0]?.bucket}</span>
				{busy ? <span className="analytics-axis-note">busiest day scales to the full height</span> : null}
				<span>{figures.volume[figures.volume.length - 1]?.bucket}</span>
			</div>
		</div>
	);
}

/** Which preset, if any, is the period on screen. */
function presetFor(fromDate, toDate) {
	if (!fromDate || !toDate) return 0;

	const days = Math.round((Date.parse(`${toDate}T00:00:00Z`) - Date.parse(`${fromDate}T00:00:00Z`)) / 86400000) + 1;

	return PERIOD_PRESETS.find((preset) => preset.days === days)?.days || 0;
}
