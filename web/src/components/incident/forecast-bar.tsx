"use client";

import type { Forecast } from "@/lib/api/types";
import { useNow } from "@/lib/clock";
import { formatClock, formatClockNear, formatTimeRange, parseTime } from "@/lib/format";
import { forecastGeometry, forecastTimes } from "@/lib/forecast";
import { useZone } from "@/lib/prefs";

const pct = (v: number) => `${(v * 100).toFixed(3)}%`;

/**
 * Time axis from the incident's start, through now, to past the 90th percentile: a shaded 80%
 * band, a tick at the median and a marker at now. Drawn in percentages so it needs no measuring.
 */
export function ForecastBar({ startedAt, forecast, size = "card" }: { startedAt: string; forecast: Forecast; size?: "card" | "large" }) {
  const now = useNow(30_000);
  const [zone] = useZone();
  if (!now) return <div className="h-[72px]" />;
  const times = forecastTimes(startedAt, forecast, now);
  if (!times) return null;
  const g = forecastGeometry(times, 1);
  const h = size === "large" ? 56 : 40;
  const axis = h / 2 + 4;
  const bandTop = axis - (size === "large" ? 11 : 8);
  const bandH = (size === "large" ? 22 : 16);
  const made = parseTime(forecast.made_at);
  const likely = `Likely resolved by ${formatClockNear(times.p50, now, zone)}`;
  const range = `80% range: ${formatTimeRange(times.p10, times.p90, zone, now)}`;
  return (
    <div className="mt-4">
      <svg width="100%" height={h} role="img" aria-label={`${likely}. ${range}.`} className="block overflow-visible">
        {/* Elapsed: the incident so far, solid. Ahead: a hairline. */}
        <line x1="0" x2={pct(g.nowX)} y1={axis} y2={axis} stroke="var(--ink-soft)" strokeWidth="2" />
        <line x1={pct(g.nowX)} x2="100%" y1={axis} y2={axis} stroke="var(--rule)" strokeWidth="1" />
        <rect
          x={pct(g.p10X)}
          width={pct(Math.max(0.004, g.p90X - g.p10X))}
          y={bandTop}
          height={bandH}
          rx="2"
          fill="color-mix(in oklab, var(--accent) 18%, transparent)"
          stroke="color-mix(in oklab, var(--accent) 55%, transparent)"
          strokeWidth="1"
        />
        <line x1={pct(g.p50X)} x2={pct(g.p50X)} y1={bandTop - 4} y2={bandTop + bandH + 4} stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
        {g.compressed ? (
          <svg x={pct(g.nowX * 0.5)} y={axis} overflow="visible" aria-hidden="true">
            <path d="M-5 5l4-10M1 5l4-10" stroke="var(--paper-raised)" strokeWidth="4" />
            <path d="M-5 5l4-10M1 5l4-10" stroke="var(--ink-soft)" strokeWidth="1.25" />
          </svg>
        ) : null}
        <circle cx="0" cy={axis} r="3" fill="var(--ink-soft)" />
        <line x1={pct(g.nowX)} x2={pct(g.nowX)} y1={axis - (size === "large" ? 18 : 14)} y2={axis + (size === "large" ? 18 : 14)} stroke="var(--ink)" strokeWidth="1.5" />
        <text x={pct(g.nowX)} y={axis - (size === "large" ? 22 : 17)} textAnchor="middle" fontSize="11" fill="var(--ink-soft)">
          now
        </text>
      </svg>
      {size === "large" && !g.overdue ? null : (
        <div className="mt-1 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 text-xs">
          {g.overdue ? (
            <p className="font-medium text-ink">Still open after its forecast range ended at {formatClock(times.p90, zone)}</p>
          ) : (
            <p className="font-medium text-ink">{likely}</p>
          )}
          <p className="text-ink-soft">{range}</p>
        </div>
      )}
      {made != null && size === "large" ? (
        <p className="mt-1 text-xs text-ink-soft">Forecast made at {formatClockNear(made, now, zone)} with model {forecast.model_version}.</p>
      ) : null}
    </div>
  );
}
