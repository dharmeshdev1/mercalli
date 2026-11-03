import type { Replay, ReplayEvent } from "@/lib/api/types";
import { formatDuration, MINUTE, parseTime } from "@/lib/format";

/** Isoseismal rings, in minutes after the upstream incident began. */
export const RING_MINUTES = [15, 30, 60, 120, 180] as const;

export function ringsFor(windowMin: number): number[] {
  return RING_MINUTES.filter((m) => m <= windowMin);
}

/**
 * Radius as a share of the outer ring. Minutes map through a square root so the first quarter
 * hour, where most of a cascade happens, is not squeezed into the center. `inner` keeps a small
 * disc free for the epicenter; anything at or before the start sits on its edge.
 */
export function radiusShare(minutes: number, windowMin: number, inner = 0): number {
  if (windowMin <= 0) return inner;
  return inner + (1 - inner) * Math.sqrt(Math.min(1, Math.max(0, minutes / windowMin)));
}

/** The earliest minute on the replay clock: the start, or earlier if services reported before it. */
export function clockStart(replay: Replay): number {
  return Math.min(0, ...replay.events.map((e) => e.offset_min));
}

/** Whether Mercalli has the provider's own incident on record. Older documents always did. */
export function onRecord(replay: Replay): boolean {
  return replay.upstream.on_record !== false;
}

export interface Sector {
  category: string;
  count: number;
  a0: number;
  a1: number;
  mid: number;
}

const TAU = Math.PI * 2;

/** Angle kept free at twelve o'clock for the radial axis that labels the rings. */
export const AXIS_GAP = 0.34;

/** Angular sectors per category, largest first, clockwise from just past the radial axis. */
export function sectors(events: readonly ReplayEvent[], gap = 0.06, axisGap = AXIS_GAP): Sector[] {
  const counts = new Map<string, number>();
  for (const e of events) counts.set(e.category, (counts.get(e.category) ?? 0) + 1);
  const entries = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (entries.length === 0) return [];
  const n = entries.length;
  const usable = TAU - axisGap - gap * (n - 1);
  // Every category gets a minimum slice so single services stay readable.
  const minShare = Math.min(0.06, 1 / n);
  const total = entries.reduce((s, [, c]) => s + c, 0);
  const raw = entries.map(([, c]) => Math.max(minShare, c / total));
  const norm = raw.reduce((s, v) => s + v, 0);
  let a = -Math.PI / 2 + axisGap / 2;
  return entries.map(([category, count], i) => {
    const span = (raw[i] / norm) * usable;
    const s: Sector = { category, count, a0: a, a1: a + span, mid: a + span / 2 };
    a += span + gap;
    return s;
  });
}

export interface PlacedEvent {
  event: ReplayEvent;
  angle: number;
  /** Radius as a share of the outer ring. */
  r: number;
}

/** Spread each category's events across its sector, in order of arrival. */
export function placeEvents(events: readonly ReplayEvent[], secs: readonly Sector[], windowMin: number, inner = 0): PlacedEvent[] {
  const byCat = new Map<string, ReplayEvent[]>();
  for (const e of events) {
    const list = byCat.get(e.category) ?? [];
    list.push(e);
    byCat.set(e.category, list);
  }
  const out: PlacedEvent[] = [];
  for (const s of secs) {
    const list = (byCat.get(s.category) ?? []).slice().sort((a, b) => a.offset_min - b.offset_min);
    const pad = Math.min(0.04, (s.a1 - s.a0) * 0.08);
    const span = s.a1 - s.a0 - 2 * pad;
    list.forEach((event, i) => {
      // Services that report at nearly the same minute would land side by side; a golden ratio
      // sequence spreads consecutive arrivals across the whole sector instead.
      const k = list.length === 1 ? 0.5 : (i * 0.6180339887 + 0.5) % 1;
      out.push({ event, angle: s.a0 + pad + span * k, r: radiusShare(event.offset_min, windowMin, inner) });
    });
  }
  return out.sort((a, b) => a.event.offset_min - b.event.offset_min);
}

export interface NarrationStep {
  /** Minutes after the upstream start when this step becomes current. */
  atMin: number;
  /** Short lead, such as "10:20 UTC" or "+14 min". */
  lead: string;
  text: string;
}

function sentence(s: string): string {
  const t = s.trim();
  return /[.!?]$/.test(t) ? t : `${t}.`;
}

function countAt(events: readonly ReplayEvent[], minute: number): { posted: number; named: number } {
  let posted = 0;
  let named = 0;
  for (const e of events) {
    if (e.offset_min <= minute) {
      posted++;
      if (e.names_upstream) named++;
    }
  }
  return { posted, named };
}

function services(n: number): string {
  return n === 1 ? "1 service has" : `${n} services have`;
}

/**
 * Four to six short, factual steps generated from the replay: the upstream report (only when
 * Mercalli has it on record), the first service to post, one or two counts on the way out, the
 * upstream resolution when it falls inside the window, and the totals.
 */
export function narrate(replay: Replay, clock: (ms: number) => string): NarrationStep[] {
  const up = replay.upstream;
  const recorded = onRecord(replay);
  const events = replay.events.slice().sort((a, b) => a.offset_min - b.offset_min);
  const steps: NarrationStep[] = [];
  const startMs = parseTime(up.started_at);
  const at = (minute: number) => (startMs != null ? clock(startMs + minute * MINUTE) : `+${formatDuration(minute)}`);
  const first = events[0];

  if (recorded) {
    steps.push({ atMin: 0, lead: at(0), text: `${up.service_name} posts an incident: ${sentence(up.title)}` });
    if (first) {
      steps.push({
        atMin: first.offset_min,
        lead: `+${formatDuration(first.offset_min)}`,
        text: `${first.service_name} is the first to post an incident of its own.`,
      });
    }
  } else if (first) {
    // No record of the provider's own incident: the story starts with the first service to report.
    steps.push({ atMin: Math.min(0, first.offset_min), lead: at(first.offset_min), text: `${first.service_name} posts the first incident of the cascade.` });
  }

  const checkpoints: number[] = [];
  let last = first ? countAt(events, first.offset_min).posted : 0;
  for (const m of [15, 30, 60, 120]) {
    if (m >= replay.window_min || (first && m <= first.offset_min)) continue;
    const { posted } = countAt(events, m);
    if (posted > last && posted >= 2) {
      checkpoints.push(m);
      last = posted;
    }
  }
  const resolvedInside = recorded && up.duration_min != null && up.duration_min > 0 && up.duration_min <= replay.window_min;
  const room = resolvedInside ? 1 : 2;
  const chosen = checkpoints.length > room ? [checkpoints[0], checkpoints[checkpoints.length - 1]].slice(0, room) : checkpoints;
  for (const m of chosen) {
    const { posted, named } = countAt(events, m);
    const tail = named === 0 ? `none of them name ${up.service_name}` : `${named} of them name ${up.service_name}`;
    steps.push({ atMin: m, lead: `+${formatDuration(m)}`, text: `${services(posted)} posted incidents, ${tail}.` });
  }

  if (resolvedInside && up.duration_min != null) {
    steps.push({
      atMin: up.duration_min,
      lead: `+${formatDuration(up.duration_min)}`,
      text: `${up.service_name} marks its incident resolved.`,
    });
  }

  const t = replay.totals;
  const cats = Object.keys(t.categories).length;
  const named =
    t.named === 0
      ? `None of them named ${up.service_name}.`
      : `${t.named} of them named ${up.service_name}.`;
  steps.push({
    atMin: replay.window_min,
    lead: `+${formatDuration(replay.window_min)}`,
    text: `${t.services === 1 ? "1 service" : `${t.services} services`} in ${cats === 1 ? "1 category" : `${cats} categories`} posted incidents. ${named}`,
  });

  return steps.sort((a, b) => a.atMin - b.atMin);
}

/** Index of the step that is current at a given minute. */
export function stepAt(steps: readonly NarrationStep[], minute: number): number {
  let idx = 0;
  steps.forEach((s, i) => {
    if (s.atMin <= minute) idx = i;
  });
  return idx;
}
