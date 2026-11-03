"use client";

import Link from "next/link";
import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { ImpactGlyph } from "@/components/ui/glyph";
import { ZoneToggle } from "@/components/chrome/zone-toggle";
import type { Impact, IncidentSummary, Overview } from "@/lib/api/types";
import { useNow } from "@/lib/clock";
import {
  formatClock,
  formatDuration,
  formatTimeRange,
  HOUR,
  MINUTE,
  parseTime,
  zoneLabel,
} from "@/lib/format";
import {
  helicorderRows,
  hourStart,
  majorStarts,
  makeEnvelope,
  openByBucket,
  pickFlags,
  uniqueIncidents,
  type Envelope,
  type Flag,
} from "@/lib/helicorder";
import { useReducedMotionPref, useResolvedTheme, useZone } from "@/lib/prefs";
import { impactLabel, impactRank } from "@/lib/severity";
import { useElementWidth } from "@/lib/use-width";
import {
  baselineOf,
  buildTraces,
  drawBleed,
  drawGrid,
  drawRows,
  heliLayout,
  penPoint,
  xOf,
  type HeliColors,
  type HeliLayout,
} from "./draw";

/** Data older than this is not extended by the pen; the record shows a dotted line instead. */
const LIVE_GRACE = 20 * MINUTE;
const ENTRANCE_MS = 1200;
const ROW_SWEEP_MS = 420;
const IMPACT_ORDER: Impact[] = ["critical", "major", "minor", "maintenance", "none"];

function readColors(): HeliColors {
  const cs = getComputedStyle(document.documentElement);
  const v = (name: string) => cs.getPropertyValue(name).trim() || "#888";
  return {
    inks: [v("--trace-1"), v("--trace-2"), v("--trace-3"), v("--trace-4")],
    grid: v("--grid"),
    rule: v("--rule"),
    faint: v("--ink-faint"),
  };
}

function easeOutCubic(x: number): number {
  return 1 - Math.pow(1 - x, 3);
}

interface Cursor {
  row: number;
  bucket: number;
  /** Set when the reader is on a flag. */
  flag?: string;
}

interface BucketInfo {
  from: number;
  to: number;
  open: IncidentSummary[];
  started: IncidentSummary[];
}

function countsPhrase(list: readonly IncidentSummary[]): string {
  const counts = new Map<Impact, number>();
  for (const i of list) counts.set(i.impact, (counts.get(i.impact) ?? 0) + 1);
  return IMPACT_ORDER.filter((k) => counts.get(k))
    .map((k) => (k === "none" ? `${counts.get(k)} with no impact` : `${counts.get(k)} ${impactLabel(k).toLowerCase()}`))
    .join(", ");
}

function byWorst(a: IncidentSummary, b: IncidentSummary): number {
  return impactRank(b.impact) - impactRank(a.impact) || (parseTime(b.started_at) ?? 0) - (parseTime(a.started_at) ?? 0);
}

export function Helicorder({ overview }: { overview: Overview | null }) {
  const [zone] = useZone();
  const theme = useResolvedTheme();
  const reduced = useReducedMotionPref();
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nibRef = useRef<HTMLDivElement>(null);
  const enteredRef = useRef(false);
  const width = useElementWidth(wrapRef);
  const minute = useNow(MINUTE);
  const rowsCount = width > 0 && width < 560 ? 12 : 24;
  const layout = useMemo<HeliLayout | null>(() => (width ? heliLayout(width, rowsCount) : null), [width, rowsCount]);
  const currentHour = minute ? hourStart(minute, zone) : 0;
  const rowStarts = useMemo(() => (currentHour ? helicorderRows(currentHour, rowsCount, zone) : []), [currentHour, rowsCount, zone]);

  const seismograph = overview?.seismograph ?? null;
  const drawnFrom = rowStarts[0] ?? 0;
  const drawnTo = rowStarts.length ? rowStarts[rowStarts.length - 1] + HOUR : 0;
  const env = useMemo<Envelope | null>(
    () => (seismograph && seismograph.total.length ? makeEnvelope(seismograph, drawnTo ? { from: drawnFrom, to: drawnTo } : undefined) : null),
    [seismograph, drawnFrom, drawnTo],
  );
  const generatedAt = parseTime(overview?.generated_at) ?? 0;
  const incidents = useMemo(
    () => (overview ? uniqueIncidents(overview.active, overview.long_running, overview.notices, overview.recent) : []),
    [overview],
  );
  const buckets = useMemo(
    () => (seismograph ? openByBucket(incidents, seismograph, generatedAt) : []),
    [incidents, seismograph, generatedAt],
  );
  const windowStart = rowStarts[0] ?? 0;
  const windowEnd = rowStarts.length ? rowStarts[rowStarts.length - 1] + HOUR : 0;
  const flags = useMemo(() => majorStarts(incidents, windowStart, windowEnd), [incidents, windowStart, windowEnd]);
  const traces = useMemo(() => (layout && rowStarts.length ? buildTraces(layout, rowStarts, env) : []), [layout, rowStarts, env]);

  const [cursor, setCursor] = useState<Cursor | null>(null);
  const tooltipId = useId();
  const helpId = useId();

  // Drawing: an entrance on first paint, then the pen advances in real time.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !layout || !traces.length) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const colors = readColors();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(layout.width * dpr);
    canvas.height = Math.round(layout.height * dpr);
    canvas.style.width = `${layout.width}px`;
    canvas.style.height = `${layout.height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const hasData = env != null;
    const liveUntil = hasData ? generatedAt + LIVE_GRACE : 0;
    const current = traces[traces.length - 1];
    let raf = 0;
    let timer = 0;
    let lastSample = -1;
    let stopped = false;

    const paint = (now: number, progress?: (row: number) => number) => {
      ctx.clearRect(0, 0, layout.width, layout.height);
      drawGrid(ctx, layout, colors);
      if (!hasData) return;
      drawRows(ctx, layout, traces, colors, { now, liveUntil, progress });
      if (!progress && now <= liveUntil) drawBleed(ctx, layout, current, colors, now);
    };

    const placeNib = (now: number, visible: boolean) => {
      const nib = nibRef.current;
      if (!nib) return;
      if (!visible || !hasData || now > liveUntil) {
        nib.style.opacity = "0";
        return;
      }
      const p = penPoint(layout, current, now);
      nib.style.opacity = "1";
      nib.style.transform = `translate(${p.x}px, ${p.y}px)`;
    };

    const live = () => {
      if (stopped) return;
      const now = Date.now();
      const sample = Math.floor((((now - current.start) / HOUR) * layout.traceWidth) / layout.step);
      if (sample !== lastSample) {
        lastSample = sample;
        paint(now);
        placeNib(now, true);
      }
      // Wake again at the next sample instead of every frame; drawing a pixel every couple of
      // seconds is all the pen needs.
      const msPerSample = (layout.step / layout.traceWidth) * HOUR;
      const wait = Math.max(250, msPerSample - ((now - current.start) % msPerSample));
      timer = window.setTimeout(() => {
        raf = requestAnimationFrame(live);
      }, wait);
    };

    const entrance = () => {
      const t0 = performance.now();
      const stagger = (ENTRANCE_MS - ROW_SWEEP_MS) / Math.max(1, traces.length - 1);
      const step = () => {
        if (stopped) return;
        const elapsed = performance.now() - t0;
        const progress = (row: number) => easeOutCubic(Math.min(1, Math.max(0, (elapsed - row * stagger) / ROW_SWEEP_MS)));
        paint(Date.now(), progress);
        if (elapsed < ENTRANCE_MS) {
          raf = requestAnimationFrame(step);
        } else {
          enteredRef.current = true;
          live();
        }
      };
      placeNib(Date.now(), false);
      raf = requestAnimationFrame(step);
    };

    const start = () => {
      stopped = false;
      lastSample = -1;
      if (!enteredRef.current && hasData && !reduced && document.visibilityState === "visible") entrance();
      else {
        if (hasData) enteredRef.current = true;
        live();
      }
    };
    const stop = () => {
      stopped = true;
      cancelAnimationFrame(raf);
      window.clearTimeout(timer);
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") stop();
      else {
        stop();
        start();
      }
    };

    start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [layout, traces, env, generatedAt, reduced, theme]);

  const nowMs = minute || generatedAt;

  const info = useMemo<BucketInfo | null>(() => {
    if (!cursor || !env) return null;
    const from = env.startMs + cursor.bucket * env.bucketMs;
    const ids = buckets[cursor.bucket] ?? [];
    const open = ids.map((i) => incidents[i]).sort(byWorst);
    const started = open.filter((i) => {
      const t = parseTime(i.started_at) ?? 0;
      return t >= from && t < from + env.bucketMs;
    });
    return { from, to: from + env.bucketMs, open, started };
  }, [cursor, env, buckets, incidents]);

  const bucketAt = (t: number): number | null => {
    if (!env) return null;
    const b = Math.floor((t - env.startMs) / env.bucketMs);
    return b >= 0 && b < env.points ? b : null;
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!layout || !rowStarts.length || !env) return;
    if ((e.target as HTMLElement).closest("[data-flag]")) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const row = Math.floor((y - layout.top) / layout.pitch);
    if (row < 0 || row >= rowStarts.length || x < layout.left || x > layout.left + layout.traceWidth) {
      setCursor(null);
      return;
    }
    const t = Math.min(rowStarts[row] + ((x - layout.left) / layout.traceWidth) * HOUR, Math.max(nowMs, generatedAt));
    const bucket = bucketAt(t);
    if (bucket == null || t > Date.now()) {
      setCursor(null);
      return;
    }
    setCursor((c) => (c && c.row === row && c.bucket === bucket && !c.flag ? c : { row, bucket }));
  };

  const lastBucket = env ? Math.max(0, Math.min(env.points - 1, Math.floor((Math.min(nowMs, env.endMs - 1) - env.startMs) / env.bucketMs))) : 0;
  const rowOfBucket = (b: number): number => {
    if (!env || !rowStarts.length) return 0;
    const t = env.startMs + b * env.bucketMs + 1;
    return Math.min(rowStarts.length - 1, Math.max(0, Math.floor((t - rowStarts[0]) / HOUR)));
  };
  const firstBucket = env && rowStarts.length ? Math.max(0, Math.floor((rowStarts[0] - env.startMs) / env.bucketMs)) : 0;

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!env || !rowStarts.length) return;
    const current = cursor?.bucket ?? lastBucket;
    let next = current;
    const perRow = Math.round(HOUR / env.bucketMs);
    if (e.key === "ArrowLeft") next = current - 1;
    else if (e.key === "ArrowRight") next = current + 1;
    else if (e.key === "ArrowUp") next = current - perRow;
    else if (e.key === "ArrowDown") next = current + perRow;
    else if (e.key === "Home") next = firstBucket;
    else if (e.key === "End") next = lastBucket;
    else if (e.key === "Escape") {
      setCursor(null);
      return;
    } else return;
    e.preventDefault();
    next = Math.min(lastBucket, Math.max(firstBucket, next));
    setCursor({ row: rowOfBucket(next), bucket: next });
  };

  const height = layout?.height ?? 24 * 26 + 44;

  // At most ten flags, critical first, skipping any whose label would run into another on its row.
  // A label that would run past the right edge is set to the left of its staff.
  const placedFlags = useMemo(() => {
    if (!layout || !rowStarts.length) return [];
    const candidates = flags.flatMap((flag) => {
      const row = Math.floor((flag.t - rowStarts[0]) / HOUR);
      if (row < 0 || row >= rowStarts.length) return [];
      const x = xOf(layout, rowStarts[row], flag.t);
      const width = flag.incident.service_name.length * 6.4 + 14;
      const left = x + width > layout.width - 4;
      return [{ flag, row, x: left ? x - width : x, width }];
    });
    return pickFlags(candidates, 10).map((c) => {
      const x = xOf(layout, rowStarts[c.row], c.flag.t);
      return {
        flag: c.flag,
        x,
        y: baselineOf(layout, c.row) - layout.pitch * 0.5,
        row: c.row,
        label: (x + c.width > layout.width - 4 ? "left" : "right") as "left" | "right",
      };
    });
  }, [flags, layout, rowStarts]);

  const activeFlag = cursor?.flag ? flags.find((f) => f.incident.id === cursor.flag) ?? null : null;

  // Tooltip placement: below the row in the upper half, above it in the lower half.
  let tooltipStyle: CSSProperties | undefined;
  if (cursor && layout && rowStarts.length && env) {
    const rowTop = layout.top + cursor.row * layout.pitch;
    const bandX = activeFlag ? xOf(layout, rowStarts[cursor.row], activeFlag.t) : xOf(layout, rowStarts[cursor.row], env.startMs + cursor.bucket * env.bucketMs + env.bucketMs / 2);
    const w = Math.min(344, layout.width - 16);
    const left = Math.min(Math.max(8, bandX - w / 2), layout.width - w - 8);
    tooltipStyle =
      cursor.row < rowStarts.length / 2
        ? { left, width: w, top: rowTop + layout.pitch + 8 }
        : { left, width: w, bottom: layout.height - rowTop + 8 };
  }

  let band: CSSProperties | undefined;
  if (cursor && !activeFlag && layout && rowStarts.length && env) {
    const rowStart = rowStarts[cursor.row];
    const from = Math.max(env.startMs + cursor.bucket * env.bucketMs, rowStart);
    const to = Math.min(env.startMs + (cursor.bucket + 1) * env.bucketMs, rowStart + HOUR);
    const x0 = xOf(layout, rowStart, from);
    const x1 = xOf(layout, rowStart, to);
    band = { left: x0, width: Math.max(2, x1 - x0), top: layout.top + cursor.row * layout.pitch, height: layout.pitch };
  }

  const summary = useMemo(() => {
    if (!env || !rowStarts.length) return null;
    let min = Infinity;
    let max = 0;
    for (let b = firstBucket; b <= lastBucket; b++) {
      const n = buckets[b]?.length ?? 0;
      min = Math.min(min, n);
      max = Math.max(max, n);
    }
    if (!Number.isFinite(min)) return null;
    return { min, max, flags: flags.length };
  }, [env, rowStarts, buckets, firstBucket, lastBucket, flags]);

  const summaryText = summary
    ? `Over the last ${rowStarts.length} hours, between ${summary.min} and ${summary.max} incidents were open at any one time. ${
        summary.flags === 0 ? "No major or critical incident began." : `${summary.flags} major or critical ${summary.flags === 1 ? "incident" : "incidents"} began.`
      }`
    : "";

  const zoneName = zoneLabel(zone, nowMs || generatedAt);
  const minuteMarks = [0, 10, 20, 30, 40, 50, 60];

  return (
    <figure className="m-0" aria-labelledby={`${helpId}-cap`}>
      <div
        ref={wrapRef}
        className="relative select-none rounded-sm border border-rule bg-paper-raised outline-offset-4"
        style={{ height }}
        tabIndex={0}
        aria-label="Helicorder of internet disruption. Use the arrow keys to read the record hour by hour."
        aria-describedby={`${helpId}-help`}
        onPointerMove={onPointerMove}
        onPointerLeave={() => setCursor((c) => (c?.flag ? c : null))}
        onKeyDown={onKeyDown}
        onFocus={(e) => {
          if (e.target === e.currentTarget && env) setCursor({ row: rowOfBucket(lastBucket), bucket: lastBucket });
        }}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node)) setCursor(null);
        }}
      >
        <canvas ref={canvasRef} aria-hidden="true" className="absolute inset-0" />

        {layout
          ? minuteMarks.map((m) => (
              <span
                key={m}
                aria-hidden="true"
                className="absolute font-mono text-[0.68rem] leading-none text-ink-soft"
                style={{ left: layout.left + (m / 60) * layout.traceWidth, top: layout.top - 20, transform: m === 60 ? "translateX(-100%)" : m === 0 ? "none" : "translateX(-50%)" }}
              >
                {m === 60 ? ":60" : `:${String(m).padStart(2, "0")}`}
              </span>
            ))
          : null}

        {layout
          ? rowStarts.map((start, i) => (
              <span
                key={start}
                aria-hidden="true"
                className={`absolute font-mono leading-none ${i === rowStarts.length - 1 ? "font-semibold text-ink" : "text-ink-soft"} ${rowsCount === 12 ? "text-[0.64rem]" : "text-[0.7rem]"}`}
                style={{ right: layout.width - layout.left + (rowsCount === 12 ? 8 : 12), top: baselineOf(layout, i), transform: "translateY(-50%)" }}
              >
                {formatClock(start, zone, false)}
              </span>
            ))
          : null}

        {band ? <div aria-hidden="true" className="pointer-events-none absolute bg-wash" style={band} /> : null}

        {placedFlags.map(({ flag, x, y, label }) => {
          const inc = flag.incident;
          const crit = inc.impact === "critical";
          const color = crit ? "var(--sev-critical)" : "var(--sev-major)";
          return (
            <Link
              key={inc.id}
              href={`/incident/?id=${encodeURIComponent(inc.id)}`}
              data-flag
              className="quiet-link absolute z-10 flex items-start"
              style={{ left: x - 1, top: y - 8, transform: label === "left" ? "translateX(-100%)" : undefined, flexDirection: label === "left" ? "row-reverse" : "row" }}
              aria-label={`${inc.service_name}, ${impactLabel(inc.impact).toLowerCase()} incident from ${formatClock(flag.t, zone)}: ${inc.title}`}
              onPointerEnter={() => {
                const b = bucketAt(flag.t);
                if (b != null) setCursor({ row: Math.floor((flag.t - rowStarts[0]) / HOUR), bucket: b, flag: inc.id });
              }}
              onPointerLeave={() => setCursor(null)}
              onFocus={() => {
                const b = bucketAt(flag.t);
                if (b != null) setCursor({ row: Math.floor((flag.t - rowStarts[0]) / HOUR), bucket: b, flag: inc.id });
              }}
            >
              <svg width="9" height="22" viewBox="0 0 9 22" aria-hidden="true" className="flex-none" style={{ transform: label === "left" ? "scaleX(-1)" : undefined }}>
                <path d="M1 1v21" stroke={color} strokeWidth="1.25" />
                <path d={crit ? "M1 1h7l-2 3 2 3H1z" : "M1 1l7 3-7 3z"} fill={color} />
                {crit ? <path d="M1 8l6 2.5-6 2.5z" fill={color} /> : null}
              </svg>
              {label ? (
                <span
                  className="mx-1 -mt-0.5 whitespace-nowrap rounded-xs px-0.5 text-[0.7rem] font-medium leading-tight text-ink"
                  style={{ background: "color-mix(in oklab, var(--paper-raised) 86%, transparent)" }}
                >
                  {inc.service_name}
                </span>
              ) : null}
            </Link>
          );
        })}

        <div
          ref={nibRef}
          aria-hidden="true"
          className="pointer-events-none absolute top-0 left-0 opacity-0 transition-opacity duration-300"
          style={{ willChange: "transform" }}
        >
          <span
            className="absolute size-5 rounded-full"
            style={{
              background: `radial-gradient(circle, color-mix(in oklab, var(--trace-${(traces[traces.length - 1]?.ink ?? 0) + 1}) 38%, transparent) 0%, transparent 70%)`,
              animation: reduced ? undefined : "bleed 3.2s ease-in-out infinite",
              transform: "translate(-50%, -50%)",
            }}
          />
          <span
            className="absolute size-[7px] rounded-full ring-2 ring-paper-raised"
            style={{ background: `var(--trace-${(traces[traces.length - 1]?.ink ?? 0) + 1})`, transform: "translate(-50%, -50%)" }}
          />
        </div>

        {cursor && info && tooltipStyle ? (
          <div id={tooltipId} role="tooltip" className="ledger pointer-events-none absolute z-20 px-3.5 py-3 text-xs" style={tooltipStyle}>
            {activeFlag ? (
              <>
                <p className="font-medium text-ink">
                  {activeFlag.incident.service_name}: {activeFlag.incident.title}
                </p>
                <p className="mt-0.5 text-ink-soft">
                  {impactLabel(activeFlag.incident.impact)} incident,{" "}
                  {activeFlag.incident.resolved_at
                    ? formatTimeRange(activeFlag.t, parseTime(activeFlag.incident.resolved_at) ?? activeFlag.t, zone, nowMs)
                    : `from ${formatClock(activeFlag.t, zone)}, open for ${formatDuration((nowMs - activeFlag.t) / MINUTE)}`}
                </p>
                <p className="mt-2 text-ink-soft">
                  When it began, {info.open.length} {info.open.length === 1 ? "incident was" : "incidents were"} open.
                </p>
                {info.open.length > 1 ? (
                  <ul className="mt-2 space-y-1">
                    {info.open
                      .filter((inc) => inc.id !== activeFlag.incident.id)
                      .slice(0, 3)
                      .map((inc) => (
                        <li key={inc.id} className="flex min-w-0 items-center gap-2">
                          <ImpactGlyph impact={inc.impact} size={12} />
                          <span className="flex-none font-medium text-ink">{inc.service_name}</span>
                          <span className="truncate text-ink-soft">{inc.title}</span>
                        </li>
                      ))}
                  </ul>
                ) : null}
                {info.open.length > 4 ? <p className="mt-1 text-ink-soft">and {info.open.length - 4} more</p> : null}
              </>
            ) : (
              <>
                <p className="font-medium text-ink">{formatTimeRange(info.from, Math.min(info.to, Math.max(nowMs, info.from)), zone, nowMs)}</p>
                <p className="mt-0.5 text-ink-soft">
                  {info.open.length === 0
                    ? "No incidents open."
                    : `${info.open.length} ${info.open.length === 1 ? "incident" : "incidents"} open: ${countsPhrase(info.open)}.`}
                </p>
                {info.open.length ? (
                  <ul className="mt-2 space-y-1">
                    {(info.started.length ? [...info.started, ...info.open.filter((i) => !info.started.includes(i))] : info.open).slice(0, 4).map((inc) => (
                      <li key={inc.id} className="flex min-w-0 items-center gap-2">
                        <ImpactGlyph impact={inc.impact} size={12} />
                        <span className="flex-none font-medium text-ink">{inc.service_name}</span>
                        <span className="truncate text-ink-soft">{inc.title}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {info.open.length > 4 ? <p className="mt-1 text-ink-soft">and {info.open.length - 4} more</p> : null}
                {info.started.length ? (
                  <p className="mt-2 text-ink">
                    Began in this window: {info.started.map((i) => i.service_name).join(", ")}.
                  </p>
                ) : null}
              </>
            )}
          </div>
        ) : null}

        {!overview ? (
          <p className="absolute inset-x-0 top-1/2 -translate-y-1/2 text-center text-xs text-ink-soft">Reading the latest record</p>
        ) : !env ? (
          <p className="absolute inset-x-0 top-1/2 -translate-y-1/2 text-center text-xs text-ink-soft">No seismograph data in this record.</p>
        ) : null}
      </div>

      <figcaption className="mt-3 flex flex-col gap-3 text-xs text-ink-soft md:flex-row md:items-start md:justify-between md:gap-8">
        <div className="space-y-1">
          <p id={`${helpId}-cap`}>
            The last {rowsCount} hours, one hour per row, newest at the bottom. Times in {zone === "utc" ? "UTC" : `your time zone (${zoneName})`}.
          </p>
          <p className="prose-measure">
            Height follows the number and severity of open incidents, and each burst marks a new one. The fine texture is drawn, not measured.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 md:flex-none">
          <p className="inline-flex items-center gap-2">
            <svg width="9" height="14" viewBox="0 0 9 14" aria-hidden="true">
              <path d="M1 1v13" stroke="var(--sev-major)" strokeWidth="1.25" />
              <path d="M1 1l7 3-7 3z" fill="var(--sev-major)" />
            </svg>
            Start of a major or critical incident
          </p>
          <ZoneToggle />
        </div>
      </figcaption>
      <p id={`${helpId}-help`} className="sr-only">
        {summaryText} Left and right move ten minutes, up and down move an hour. Times are in {zoneName}.
      </p>
      <p className="sr-only" aria-live="polite">
        {info && !activeFlag
          ? `${formatTimeRange(info.from, info.to, zone, nowMs)}. ${info.open.length} incidents open${info.open.length ? `: ${countsPhrase(info.open)}` : ""}.`
          : ""}
      </p>
      {summary ? <HelicorderTable rowStarts={rowStarts} env={env} buckets={buckets} incidents={incidents} flags={flags} zone={zone} summary={summaryText} lastBucket={lastBucket} /> : null}
    </figure>
  );
}

function HelicorderTable({
  rowStarts,
  env,
  buckets,
  incidents,
  flags,
  zone,
  summary,
  lastBucket,
}: {
  rowStarts: number[];
  env: Envelope | null;
  buckets: number[][];
  incidents: IncidentSummary[];
  flags: Flag[];
  zone: "utc" | "local";
  summary: string;
  lastBucket: number;
}) {
  if (!env) return null;
  const rows = rowStarts.map((start) => {
    const b0 = Math.max(0, Math.floor((start - env.startMs) / env.bucketMs));
    const b1 = Math.min(lastBucket, Math.floor((start + HOUR - 1 - env.startMs) / env.bucketMs));
    let most = 0;
    for (let b = b0; b <= b1; b++) most = Math.max(most, buckets[b]?.length ?? 0);
    const began = incidents.filter((i) => {
      const t = parseTime(i.started_at) ?? 0;
      return t >= start && t < start + HOUR;
    }).length;
    const majors = flags.filter((f) => f.t >= start && f.t < start + HOUR).map((f) => f.incident.service_name);
    return { start, most: b1 >= b0 ? most : null, began, majors };
  });
  return (
    <details className="mt-2 text-xs">
      <summary className="inline-flex min-h-8 cursor-pointer list-none items-center gap-2 text-ink-soft hover:text-ink [&::-webkit-details-marker]:hidden">
        <svg width="10" height="10" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M6 1v10M1 6h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
        Show the record as a table
      </summary>
      <div className="mt-3 overflow-x-auto">
        <table className="table">
          <caption className="mb-2 text-left text-ink-soft">{summary}</caption>
          <thead>
            <tr>
              <th scope="col">Hour starting</th>
              <th scope="col">Most incidents open</th>
              <th scope="col">Incidents that began</th>
              <th scope="col">Major or critical that began</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.start}>
                <td className="font-mono">{formatClock(r.start, zone)}</td>
                <td>{r.most ?? "No data"}</td>
                <td>{r.began}</td>
                <td>{r.majors.length ? r.majors.join(", ") : "None"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
