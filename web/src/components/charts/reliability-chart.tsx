import { formatShare } from "@/lib/format";

/**
 * Calibration of the forecast quantiles: for each predicted quantile (10th, 50th, 90th), the
 * share of real outcomes that fell at or below it. A calibrated model sits on the diagonal.
 */
export function ReliabilityChart({ reliability, size = 220 }: { reliability: Record<string, number>; size?: number }) {
  const points = Object.entries(reliability)
    .map(([q, observed]) => ({ q: Number(q), observed }))
    .filter((p) => Number.isFinite(p.q) && Number.isFinite(p.observed))
    .sort((a, b) => a.q - b.q);
  const m = { top: 12, right: 12, bottom: 34, left: 40 };
  const w = size - m.left - m.right;
  const h = size - m.top - m.bottom;
  // Both axes run from 0 to 1, so the scales are plain proportions of the plot.
  const x = (v: number) => v * w;
  const y = (v: number) => h - v * h;
  const ticks = [0, 0.5, 1];
  const label = points.map((p) => `${formatShare(p.q)} quantile: ${formatShare(p.observed)} of outcomes at or below`).join(". ");
  return (
    <figure className="m-0">
      <svg width={size} height={size} role="img" aria-label={`Reliability of the forecast quantiles. ${label}.`} className="block overflow-visible">
        <g transform={`translate(${m.left},${m.top})`}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={0} y2={h} stroke="var(--grid)" />
              <line x1={0} x2={w} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
              <text x={x(t)} y={h + 16} textAnchor="middle" fontSize="11" fill="var(--ink-soft)">
                {formatShare(t)}
              </text>
              <text x={-8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--ink-soft)">
                {formatShare(t)}
              </text>
            </g>
          ))}
          <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="var(--ink-faint)" strokeWidth="1" />
          {points.map((p) => (
            <line key={`v${p.q}`} x1={x(p.q)} x2={x(p.q)} y1={y(p.q)} y2={y(p.observed)} stroke="var(--accent)" strokeWidth="2" />
          ))}
          {points.map((p) => (
            <circle key={p.q} cx={x(p.q)} cy={y(p.observed)} r="4.5" fill="var(--accent)" stroke="var(--paper)" strokeWidth="2" />
          ))}
          <text x={w / 2} y={h + 31} textAnchor="middle" fontSize="11" fill="var(--ink-soft)">
            Predicted quantile
          </text>
        </g>
      </svg>
      <figcaption className="sr-only">
        Each dot is a forecast quantile. Its height is the share of real outcomes at or below it; the diagonal is perfect calibration.
      </figcaption>
    </figure>
  );
}
