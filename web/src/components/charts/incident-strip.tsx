"use client";

import { timeMonth, utcMonth } from "d3-time";
import { useMemo, useRef, useState } from "react";
import type { IncidentSummary } from "@/lib/api/types";
import { useNow } from "@/lib/clock";
import { DAY, formatDate, formatDuration, MINUTE, parseTime, parts } from "@/lib/format";
import { useZone } from "@/lib/prefs";
import { impactGlyph, impactLabel } from "@/lib/severity";
import { useElementWidth } from "@/lib/use-width";

const LANE = 9;
const GAP = 3;

interface Bar {
  inc: IncidentSummary;
  x0: number;
  x1: number;
  lane: number;
  start: number;
  end: number;
  open: boolean;
}

/**
 * Ninety days of incidents on one time axis: one bar per incident, its length the duration, its
 * color the impact. Overlapping incidents stack into lanes so none hides another.
 */
export function IncidentStrip({ incidents, days = 90 }: { incidents: IncidentSummary[]; days?: number }) {
  const wrap = useRef<HTMLDivElement>(null);
  const width = useElementWidth(wrap);
  const now = useNow(MINUTE);
  const [zone] = useZone();
  const [hover, setHover] = useState<Bar | null>(null);

  const bars = useMemo<Bar[]>(() => {
    if (!width || !now) return [];
    const from = now - days * DAY;
    const x = (t: number) => ((t - from) / (now - from)) * width;
    const sorted = incidents
      .map((inc) => {
        const start = parseTime(inc.started_at);
        if (start == null) return null;
        const resolved = parseTime(inc.resolved_at);
        const end = resolved != null && resolved >= start ? resolved : resolved == null ? now : start;
        return { inc, start, end, open: resolved == null };
      })
      .filter((b): b is { inc: IncidentSummary; start: number; end: number; open: boolean } => b != null && b.end >= from)
      .sort((a, b) => a.start - b.start);
    const laneEnds: number[] = [];
    return sorted.map((b) => {
      const x0 = Math.max(0, x(b.start));
      const x1 = Math.max(x0 + 2, Math.min(width, x(b.end)));
      let lane = laneEnds.findIndex((e) => e < x0 - 2);
      if (lane === -1) {
        lane = laneEnds.length;
        laneEnds.push(x1);
      } else laneEnds[lane] = x1;
      return { ...b, x0, x1, lane };
    });
  }, [incidents, width, now, days]);

  const lanes = Math.max(1, ...bars.map((b) => b.lane + 1));
  const plotH = lanes * (LANE + GAP) - GAP;
  const h = plotH + 26;
  const from = now ? now - days * DAY : 0;
  const ticks = now && width ? (zone === "utc" ? utcMonth : timeMonth).range(new Date(from), new Date(now)).map((d) => d.getTime()) : [];

  return (
    <div ref={wrap} className="relative">
      {width && now ? (
        <svg width={width} height={h} className="block overflow-visible" role="img" aria-label={`${bars.length} incidents over the last ${days} days`}>
          <line x1={0} x2={width} y1={plotH + 6} y2={plotH + 6} stroke="var(--rule)" />
          {ticks.map((t) => {
            const x = ((t - from) / (now - from)) * width;
            const p = parts(t, zone);
            const room = width - x > 64;
            return (
              <g key={t}>
                <line x1={x} x2={x} y1={0} y2={plotH + 10} stroke="var(--grid)" />
                {room ? (
                  <text x={x + 4} y={plotH + 21} fontSize="11" fill="var(--ink-soft)">
                    {p.month}
                  </text>
                ) : null}
              </g>
            );
          })}
          {bars.map((b) => {
            const tone = impactGlyph(b.inc.impact).tone;
            const fill = tone === "faint" ? "var(--ink-faint)" : `var(--sev-${tone})`;
            return (
              <rect
                key={b.inc.id}
                x={b.x0}
                y={b.lane * (LANE + GAP)}
                width={b.x1 - b.x0}
                height={LANE}
                rx={2}
                fill={fill}
                stroke={hover?.inc.id === b.inc.id ? "var(--ink)" : "none"}
                strokeWidth={1.5}
                onPointerEnter={() => setHover(b)}
                onPointerLeave={() => setHover((h) => (h?.inc.id === b.inc.id ? null : h))}
              />
            );
          })}
          <text x={width} y={plotH + 21} fontSize="11" fill="var(--ink-soft)" textAnchor="end">
            now
          </text>
        </svg>
      ) : (
        <div style={{ height: 60 }} />
      )}
      {hover ? (
        <div
          role="tooltip"
          className="ledger pointer-events-none absolute z-10 w-72 px-3 py-2 text-xs"
          style={{ left: Math.min(Math.max(0, hover.x0 - 40), Math.max(0, width - 288)), top: hover.lane * (LANE + GAP) + LANE + 8 }}
        >
          <p className="font-medium text-ink">{hover.inc.title}</p>
          <p className="mt-0.5 text-ink-soft">
            {impactLabel(hover.inc.impact)}, {formatDate(hover.start, zone)},{" "}
            {hover.open ? `open for ${formatDuration((now - hover.start) / MINUTE)}` : `lasted ${formatDuration(hover.inc.duration_min)}`}
          </p>
        </div>
      ) : null}
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-soft" aria-label="Legend">
        {(["critical", "major", "minor", "maintenance", "none"] as const).map((k) => {
          const tone = impactGlyph(k).tone;
          return (
            <li key={k} className="inline-flex items-center gap-1.5">
              <span className="inline-block h-2 w-3.5 rounded-[2px]" style={{ background: tone === "faint" ? "var(--ink-faint)" : `var(--sev-${tone})` }} />
              {impactLabel(k)}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
