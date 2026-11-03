"use client";

import { scaleLinear } from "d3-scale";
import { area, line } from "d3-shape";
import { useRef, useState, type PointerEvent } from "react";
import type { ForecastPoint } from "@/lib/api/types";
import { formatClock, formatDuration, MINUTE, parseTime } from "@/lib/format";
import { useZone } from "@/lib/prefs";
import { useElementWidth } from "@/lib/use-width";

interface Pt {
  x: number;
  p10: number;
  p50: number;
  p90: number;
  made: number;
}

function niceDurationTicks(max: number): number[] {
  const steps = [5, 10, 15, 30, 60, 120, 180, 240, 360, 720, 1440, 2880, 4320, 10080];
  const step = steps.find((s) => max / s <= 5) ?? 10080;
  const out: number[] = [];
  for (let v = 0; v <= max + 0.01; v += step) out.push(v);
  return out;
}

/**
 * Every forecast made while the incident was open. Across: time since the incident began. Up:
 * when each forecast expected it to end, also as time since the start, with the 80% band and the
 * median line. The diagonal is "now": an end can never be forecast below it. When the incident has
 * ended, a level line marks the real end and each forecast's error is drawn to it.
 */
export function ForecastHistory({
  points,
  startedAt,
  actualMin,
}: {
  points: ForecastPoint[];
  startedAt: string;
  actualMin: number | null;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const width = useElementWidth(wrap);
  const [zone] = useZone();
  const [hover, setHover] = useState<number | null>(null);
  const start = parseTime(startedAt) ?? 0;
  const pts: Pt[] = points
    .map((p) => ({ x: p.elapsed_min, p10: p.elapsed_min + p.p10, p50: p.elapsed_min + p.p50, p90: p.elapsed_min + p.p90, made: parseTime(p.made_at) ?? 0 }))
    .sort((a, b) => a.x - b.x);
  const h = 300;
  const m = { top: 14, right: 16, bottom: 40, left: 64 };
  const w = Math.max(10, width - m.left - m.right);
  const ih = h - m.top - m.bottom;
  const xMax = Math.max(1, ...pts.map((p) => p.x), actualMin ?? 0) * 1.04;
  const yMax = Math.max(1, ...pts.map((p) => p.p90), actualMin ?? 0) * 1.06;
  const x = scaleLinear().domain([0, xMax]).range([0, w]);
  const y = scaleLinear().domain([0, yMax]).range([ih, 0]);
  const band = area<Pt>()
    .x((p) => x(p.x))
    .y0((p) => y(p.p10))
    .y1((p) => y(p.p90));
  const mid = line<Pt>()
    .x((p) => x(p.x))
    .y((p) => y(p.p50));
  const xt = niceDurationTicks(xMax);
  const yt = niceDurationTicks(yMax);
  const active = hover != null ? pts[hover] : null;

  const onMove = (e: PointerEvent<SVGRectElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const xv = x.invert(e.clientX - r.left);
    let best = 0;
    pts.forEach((p, i) => {
      if (Math.abs(p.x - xv) < Math.abs(pts[best].x - xv)) best = i;
    });
    setHover(best);
  };

  return (
    <div ref={wrap} className="relative">
      {width ? (
        <svg width={width} height={h} role="img" aria-label={`${pts.length} forecasts made while the incident was open`} className="block overflow-visible">
          <g transform={`translate(${m.left},${m.top})`}>
            {yt.map((t) => (
              <g key={`y${t}`}>
                <line x1={0} x2={w} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
                <text x={-10} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--ink-soft)">
                  {t === 0 ? "start" : formatDuration(t)}
                </text>
              </g>
            ))}
            {xt.map((t) => (
              <g key={`x${t}`}>
                <line x1={x(t)} x2={x(t)} y1={ih} y2={ih + 5} stroke="var(--rule)" />
                <text x={x(t)} y={ih + 19} textAnchor="middle" fontSize="11" fill="var(--ink-soft)">
                  {t === 0 ? "start" : `+${formatDuration(t)}`}
                </text>
              </g>
            ))}
            <text x={w / 2} y={ih + 36} textAnchor="middle" fontSize="11" fill="var(--ink-soft)">
              When the forecast was made, after the start
            </text>
            <text transform={`translate(${-52},${ih / 2}) rotate(-90)`} textAnchor="middle" fontSize="11" fill="var(--ink-soft)">
              Forecast end, after the start
            </text>
            <line x1={x(0)} y1={y(0)} x2={x(Math.min(xMax, yMax))} y2={y(Math.min(xMax, yMax))} stroke="var(--ink-faint)" />
            {pts.length > 1 ? <path d={band(pts) ?? undefined} fill="color-mix(in oklab, var(--accent) 16%, transparent)" /> : null}
            {/* Each forecast's own 80% range, so a single forecast reads as clearly as a series. */}
            {pts.map((p, i) => (
              <line
                key={`r${i}`}
                x1={x(p.x)}
                x2={x(p.x)}
                y1={y(p.p10)}
                y2={y(p.p90)}
                stroke="color-mix(in oklab, var(--accent) 55%, transparent)"
                strokeWidth={pts.length > 40 ? 1 : 3}
                strokeLinecap="round"
              />
            ))}
            {pts.length > 1 ? <path d={mid(pts) ?? undefined} fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" /> : null}
            {actualMin != null ? (
              <g>
                {pts.map((p, i) => (
                  <line key={i} x1={x(p.x)} x2={x(p.x)} y1={y(p.p50)} y2={y(actualMin)} stroke="var(--sev-major)" strokeOpacity="0.45" strokeWidth="1" />
                ))}
                <line x1={0} x2={w} y1={y(actualMin)} y2={y(actualMin)} stroke="var(--ink)" strokeWidth="1.5" />
                <text x={w} y={y(actualMin) - 6} textAnchor="end" fontSize="11" fill="var(--ink)">
                  Resolved at +{formatDuration(actualMin)}
                </text>
              </g>
            ) : null}
            {pts.map((p, i) => (
              <circle key={i} cx={x(p.x)} cy={y(p.p50)} r={hover === i ? 5 : 3.5} fill="var(--accent)" stroke="var(--paper)" strokeWidth="2" />
            ))}
            {active ? <line x1={x(active.x)} x2={x(active.x)} y1={0} y2={ih} stroke="var(--ink-faint)" /> : null}
            <rect width={w} height={ih} fill="transparent" onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
          </g>
        </svg>
      ) : (
        <div style={{ height: h }} />
      )}
      {active && width ? (
        <div
          role="tooltip"
          className="ledger pointer-events-none absolute z-10 w-64 px-3 py-2 text-xs"
          style={{ left: Math.min(Math.max(0, m.left + x(active.x) + 12), width - 260), top: 8 }}
        >
          <p className="font-medium text-ink">Made at {formatClock(active.made, zone)}, +{formatDuration(active.x)}</p>
          <p className="mt-0.5 text-ink-soft">
            Median end {formatClock(start + active.p50 * MINUTE, zone)}. 80% range {formatClock(start + active.p10 * MINUTE, zone, false)} to{" "}
            {formatClock(start + active.p90 * MINUTE, zone)}.
          </p>
          {actualMin != null ? (
            <p className="mt-0.5 text-ink-soft">
              Off by {formatDuration(Math.abs(active.p50 - actualMin))}, {active.p50 > actualMin ? "too late" : "too early"}.
            </p>
          ) : null}
        </div>
      ) : null}
      <details className="mt-3 text-xs">
        <summary className="inline-flex min-h-8 cursor-pointer items-center text-ink-soft hover:text-ink">Show the forecasts as a table</summary>
        <div className="mt-3 overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Made at</th>
                <th scope="col">Phase</th>
                <th scope="col">Median end</th>
                <th scope="col">80% range</th>
                {actualMin != null ? <th scope="col">Error</th> : null}
              </tr>
            </thead>
            <tbody>
              {points.map((p) => {
                const made = parseTime(p.made_at) ?? 0;
                return (
                  <tr key={p.made_at}>
                    <td>{formatClock(made, zone)}</td>
                    <td>{p.phase}</td>
                    <td>{formatClock(made + p.p50 * MINUTE, zone)}</td>
                    <td>
                      {formatClock(made + p.p10 * MINUTE, zone, false)} to {formatClock(made + p.p90 * MINUTE, zone)}
                    </td>
                    {actualMin != null ? <td>{formatDuration(Math.abs(p.elapsed_min + p.p50 - actualMin))}</td> : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
