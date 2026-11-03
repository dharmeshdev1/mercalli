import type { IncidentSummary, Overview } from "@/lib/api/types";

/**
 * Older engines may predate a field (fields are only ever added within v1). Fill the lists the
 * pages rely on so a missing field reads as "none" instead of crashing a page.
 */
export function normalizeOverview(raw: unknown): Overview {
  const o = (raw ?? {}) as Partial<Overview>;
  const list = (v: IncidentSummary[] | undefined) => (Array.isArray(v) ? v : []);
  const seismograph = o.seismograph ?? {
    start: o.generated_at ?? new Date(0).toISOString(),
    bucket_minutes: 10,
    points: 0,
    series: [],
    total: [],
  };
  return {
    generated_at: o.generated_at ?? new Date(0).toISOString(),
    counts: o.counts ?? { services: 0, open_incidents: 0 },
    intensity: o.intensity ?? null,
    services: Array.isArray(o.services) ? o.services : [],
    active: list(o.active),
    long_running: list(o.long_running),
    notices: list(o.notices),
    recent: list(o.recent),
    seismograph: { ...seismograph, total: Array.isArray(seismograph.total) ? seismograph.total : [] },
  };
}

/** Ids present now that were not in the previous document: incidents that just arrived. */
export function arrivals(previous: Overview | null, next: Overview | null): Set<string> {
  if (!previous || !next) return new Set();
  const before = new Set(previous.active.map((i) => i.id));
  return new Set(next.active.filter((i) => !before.has(i.id)).map((i) => i.id));
}
