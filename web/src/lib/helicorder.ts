import type { IncidentSummary, Seismograph } from "@/lib/api/types";
import { HOUR, MINUTE, parseTime, type Zone } from "@/lib/format";
import { impactWeight } from "@/lib/severity";

/**
 * Helicorder math. The envelope (how tall the trace is) comes from `seismograph.total`, the
 * weighted count of open incidents in 10 minute buckets. The fine wiggle inside the envelope is
 * procedural noise seeded by the hour, so the drawing is stable across reloads. Nothing here
 * touches the DOM.
 */

/*
 * Amplitude, in pixels for a 19px row (the reference the README helicorder uses; callers scale
 * to their row height). A quiet line, sustained shaking while unusually many incidents are open,
 * and a sharp burst at each new incident, larger for worse ones:
 *
 *   sustained = max(0, (total - p20) / (max - p20))     over the window drawn
 *   burst     = min(1, decayed arrivals / 3)            each arrival decays with a 7 minute constant
 *   amplitude = 0.55 + 2.2 * sqrt(sustained) + 7.8 * burst
 */
export const REFERENCE_ROW = 19;
export const QUIET_PX = 0.55;
export const SUSTAINED_PX = 2.2;
export const BURST_PX = 7.8;
/** Weight of new incidents that saturates a burst. */
export const BURST_FULL = 3;
export const ARRIVAL_TAU = 7 * MINUTE;
/** Share of a bucket spent easing from the previous level, so steps do not look like cliffs. */
const EASE_SHARE = 0.18;

export interface Envelope {
  startMs: number;
  bucketMs: number;
  points: number;
  /** End of the measured series: the end of the last bucket. */
  endMs: number;
  /** The 20th percentile and the maximum of the weighted count over the window. */
  p20: number;
  max: number;
  /** The bucket's weighted count at time t, or null outside the series. */
  valueAt(t: number): number | null;
  /** Sustained shaking at t, 0 to 1. */
  sustainedAt(t: number): number;
  /** Burst from new incidents at t, 0 to 1. */
  burstAt(t: number): number;
  /** Amplitude at t in pixels for a 19px row; 0 where there is no data. */
  at(t: number): number;
}

/** The value at a quantile of the sorted values, as the README script takes it (index floor(n * q)). */
export function quantile(values: readonly number[], q: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))];
}

function smoothstep(x: number): number {
  const c = Math.min(1, Math.max(0, x));
  return c * c * (3 - 2 * c);
}

/** Arrivals per bucket: from the API when present, else the rises in the weighted count. */
export function arrivalsOf(s: Seismograph): number[] {
  if (s.arrivals && s.arrivals.length === s.total.length) return s.arrivals;
  return s.total.map((v, i) => (i === 0 ? 0 : Math.max(0, v - s.total[i - 1])));
}

/**
 * The envelope of the trace. The scale (p20 and max) is taken over the buckets of the window drawn,
 * so a busy baseline reads as quiet and only unusual load shakes the pen.
 */
export function makeEnvelope(s: Seismograph, window?: { from: number; to: number }): Envelope {
  const startMs = parseTime(s.start) ?? 0;
  const bucketMs = s.bucket_minutes * MINUTE;
  const total = s.total;
  const arrivals = arrivalsOf(s);
  const points = total.length;
  const clampB = (b: number) => Math.min(points - 1, Math.max(0, b));
  const b0 = window ? clampB(Math.floor((window.from - startMs) / bucketMs)) : 0;
  const b1 = window ? clampB(Math.floor((window.to - 1 - startMs) / bucketMs)) : points - 1;
  const inWindow = total.slice(b0, b1 + 1);
  const p20 = quantile(inWindow, 0.2);
  const max = inWindow.length ? Math.max(...inWindow) : 0;
  const span = Math.max(1, max - p20);

  const bucketOf = (t: number) => Math.floor((t - startMs) / bucketMs);
  const value = (b: number) => (b < 0 ? 0 : total[Math.min(b, points - 1)] ?? 0);

  const level = (t: number): number => {
    const b = bucketOf(t);
    if (b >= points) return value(points - 1);
    const frac = (t - (startMs + b * bucketMs)) / bucketMs;
    const cur = value(b);
    if (b === 0 || frac >= EASE_SHARE) return cur;
    const prev = value(b - 1);
    return prev + (cur - prev) * smoothstep(frac / EASE_SHARE);
  };

  const sustainedAt = (t: number): number => {
    if (t < startMs || !points) return 0;
    return Math.max(0, (level(t) - p20) / span);
  };

  const burstAt = (t: number): number => {
    if (t < startMs || !points) return 0;
    const b = Math.min(bucketOf(t), points - 1);
    let sum = 0;
    // exp(-50 min / 7 min) is under a thousandth: five buckets back is enough.
    for (let k = Math.max(0, b - 5); k <= b; k++) {
      const w = arrivals[k] ?? 0;
      if (w <= 0) continue;
      const dt = t - (startMs + k * bucketMs);
      if (dt < 0) continue;
      sum += w * Math.exp(-dt / ARRIVAL_TAU);
    }
    return Math.min(1, sum / BURST_FULL);
  };

  return {
    startMs,
    bucketMs,
    points,
    endMs: startMs + points * bucketMs,
    p20,
    max,
    valueAt(t) {
      const b = bucketOf(t);
      return b < 0 || b >= points ? null : value(b);
    },
    sustainedAt,
    burstAt,
    at(t) {
      if (t < startMs || !points) return 0;
      return QUIET_PX + SUSTAINED_PX * Math.sqrt(sustainedAt(t)) + BURST_PX * burstAt(t);
    },
  };
}

/** A 32 bit integer hash (lowbias32), returning an unsigned integer. */
export function hash32(x: number): number {
  let h = x | 0;
  h ^= h >>> 16;
  h = Math.imul(h, 0x7feb352d);
  h ^= h >>> 15;
  h = Math.imul(h, 0x846ca68b);
  h ^= h >>> 16;
  return h >>> 0;
}

/** Deterministic value in [-1, 1] for lattice point i under a seed. */
export function lattice(seed: number, i: number): number {
  return (hash32(hash32(seed) ^ Math.imul(i, 0x9e3779b1)) / 0xffffffff) * 2 - 1;
}

/** Smooth value noise in [-1, 1]; x is in lattice units. */
export function valueNoise(seed: number, x: number): number {
  const i = Math.floor(x);
  const f = x - i;
  const u = f * f * (3 - 2 * f);
  return lattice(seed, i) * (1 - u) + lattice(seed, i + 1) * u;
}

/**
 * The drawn texture of a trace at horizontal position x (CSS px) in a row seeded by its hour.
 * Two octaves of value noise, modulated by a slow packet envelope so the line breathes like
 * microseismic noise. Roughly in [-1, 1].
 */
export function traceNoise(seed: number, x: number): number {
  const fine = valueNoise(seed, x / 1.55);
  const mid = valueNoise(seed + 7919, x / 5.5);
  const packet = 0.7 + 0.3 * (valueNoise(seed + 104729, x / 38) * 0.5 + 0.5);
  return (fine * 0.68 + mid * 0.32) * packet;
}

/** Seed for a row: the index of its starting hour since the epoch, stable across reloads. */
export function rowSeed(rowStart: number): number {
  return Math.floor(rowStart / HOUR);
}

/** Start of the hour containing ms, in UTC or the reader's own zone. */
export function hourStart(ms: number, zone: Zone): number {
  if (zone === "utc") return Math.floor(ms / HOUR) * HOUR;
  const d = new Date(ms);
  d.setMinutes(0, 0, 0);
  return d.getTime();
}

/** Start times of `count` one hour rows, oldest first, the last one holding `now`. */
export function helicorderRows(now: number, count: number, zone: Zone): number[] {
  const last = hourStart(now, zone);
  return Array.from({ length: count }, (_, i) => last - (count - 1 - i) * HOUR);
}

/** Merge incident lists, dropping duplicates by id. */
export function uniqueIncidents(...lists: (readonly IncidentSummary[] | undefined)[]): IncidentSummary[] {
  const seen = new Set<string>();
  const out: IncidentSummary[] = [];
  for (const list of lists) {
    for (const inc of list ?? []) {
      if (seen.has(inc.id)) continue;
      seen.add(inc.id);
      out.push(inc);
    }
  }
  return out;
}

/**
 * Which incidents count toward each bucket, using the same rule as the API: an incident covers
 * every bucket from the one holding its start to the one holding its end (or the document time).
 */
export function openByBucket(incidents: readonly IncidentSummary[], s: Seismograph, generatedAt: number): number[][] {
  const startMs = parseTime(s.start) ?? 0;
  const bucketMs = s.bucket_minutes * MINUTE;
  const n = s.total.length;
  const out: number[][] = Array.from({ length: n }, () => []);
  incidents.forEach((inc, idx) => {
    const st = parseTime(inc.started_at);
    if (st == null) return;
    const en = parseTime(inc.resolved_at) ?? generatedAt;
    const a = Math.max(0, Math.floor((st - startMs) / bucketMs));
    const b = Math.min(n - 1, Math.floor((en - startMs) / bucketMs));
    for (let i = a; i <= b; i++) out[i].push(idx);
  });
  return out;
}

/** The weighted count implied by a set of incidents, for checking against `total`. */
export function weightOf(incidents: readonly IncidentSummary[], indices: readonly number[]): number {
  return indices.reduce((sum, i) => sum + impactWeight(incidents[i].impact), 0);
}

export interface Flag {
  incident: IncidentSummary;
  t: number;
}

export interface PlacedFlag {
  flag: Flag;
  row: number;
  x: number;
  /** Estimated label width in pixels. */
  width: number;
}

/**
 * Choose which flags to draw: critical before major, newest first, at most `limit`, skipping any
 * whose label would run into another flag's on the same row.
 */
export function pickFlags(candidates: readonly PlacedFlag[], limit = 10): PlacedFlag[] {
  const order = [...candidates].sort(
    (a, b) => Number(b.flag.incident.impact === "critical") - Number(a.flag.incident.impact === "critical") || b.flag.t - a.flag.t,
  );
  const chosen: PlacedFlag[] = [];
  for (const c of order) {
    if (chosen.length >= limit) break;
    const clash = chosen.some((o) => o.row === c.row && c.x < o.x + o.width + 8 && o.x < c.x + c.width + 8);
    if (!clash) chosen.push(c);
  }
  return chosen.sort((a, b) => a.flag.t - b.flag.t);
}

/** Starts of major and critical incidents inside [from, to). */
export function majorStarts(incidents: readonly IncidentSummary[], from: number, to: number): Flag[] {
  const out: Flag[] = [];
  for (const incident of incidents) {
    if (incident.impact !== "major" && incident.impact !== "critical") continue;
    const t = parseTime(incident.started_at);
    if (t == null || t < from || t >= to) continue;
    out.push({ incident, t });
  }
  return out.sort((a, b) => a.t - b.t);
}
