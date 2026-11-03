import { describe, expect, it } from "vitest";
import type { IncidentSummary, Seismograph } from "./api/types";
import { HOUR, MINUTE } from "./format";
import {
  arrivalsOf,
  BURST_PX,
  hash32,
  helicorderRows,
  majorStarts,
  makeEnvelope,
  openByBucket,
  pickFlags,
  quantile,
  QUIET_PX,
  SUSTAINED_PX,
  traceNoise,
  weightOf,
} from "./helicorder";

const start = Date.parse("2026-11-01T13:10:00Z");

function series(total: number[]): Seismograph {
  return { start: new Date(start).toISOString(), bucket_minutes: 10, points: total.length, series: [], total };
}

function incident(p: Partial<IncidentSummary>): IncidentSummary {
  return {
    id: "x:1",
    path: "incidents/x/1.json",
    service_id: "x",
    service_name: "X",
    category: "cloud",
    title: "t",
    impact: "minor",
    status: "investigating",
    started_at: new Date(start).toISOString(),
    resolved_at: null,
    resolved_inferred: false,
    duration_min: null,
    elapsed_min: 0,
    components: [],
    regions: [],
    url: null,
    updates_count: 1,
    latest: null,
    first_seen_at: null,
    forecast: null,
    blast: null,
    ...p,
  };
}

describe("helicorder envelope", () => {
  const zeros = (n: number) => Array.from({ length: n }, () => 0);

  it("draws a quiet line where nothing unusual is open", () => {
    const env = makeEnvelope({ ...series([40, 40, 40, 40, 40, 40]), arrivals: zeros(6) });
    expect(env.at(start + 35 * MINUTE)).toBeCloseTo(QUIET_PX, 6);
    expect(env.sustainedAt(start + 35 * MINUTE)).toBe(0);
  });

  it("measures sustained load above the 20th percentile of the window", () => {
    const total = [10, 10, 10, 10, 10, 20, 20, 20, 20, 20];
    const env = makeEnvelope({ ...series(total), arrivals: zeros(10) });
    expect(env.p20).toBe(10);
    expect(env.max).toBe(20);
    expect(env.at(start + 95 * MINUTE)).toBeCloseTo(QUIET_PX + SUSTAINED_PX, 6);
    expect(env.at(start + 25 * MINUTE)).toBeCloseTo(QUIET_PX, 6);
  });

  it("takes the scale from the window it draws", () => {
    const total = [100, 100, 100, 10, 10, 10, 10, 10, 10];
    const env = makeEnvelope({ ...series(total), arrivals: zeros(9) }, { from: start + 30 * MINUTE, to: start + 90 * MINUTE });
    expect(env.p20).toBe(10);
    expect(env.max).toBe(10);
  });

  it("bursts at each new incident and decays with a seven minute constant", () => {
    const arrivals = [0, 0, 3, 0, 0, 0, 0, 0];
    const env = makeEnvelope({ ...series([5, 5, 5, 5, 5, 5, 5, 5]), arrivals });
    const t0 = start + 20 * MINUTE;
    expect(env.burstAt(t0)).toBeCloseTo(1, 6);
    expect(env.at(t0)).toBeCloseTo(QUIET_PX + BURST_PX, 6);
    expect(env.burstAt(t0 + 7 * MINUTE)).toBeCloseTo(Math.exp(-1), 4);
    expect(env.burstAt(t0 - MINUTE)).toBe(0);
    expect(env.burstAt(t0 + 60 * MINUTE)).toBeLessThan(0.001);
  });

  it("sizes a burst by the weight that arrived, saturating at three", () => {
    const env = makeEnvelope({ ...series([5, 5, 5, 5]), arrivals: [0, 1, 0, 9] });
    expect(env.burstAt(start + 10 * MINUTE)).toBeCloseTo(1 / 3, 6);
    expect(env.burstAt(start + 30 * MINUTE)).toBe(1);
  });

  it("falls back to rises in the weighted count for older documents", () => {
    expect(arrivalsOf(series([2, 2, 5, 4]))).toEqual([0, 0, 3, 0]);
  });

  it("reads the measured value per bucket and nothing outside the series", () => {
    const env = makeEnvelope(series([1, 2, 3]));
    expect(env.valueAt(start + 15 * MINUTE)).toBe(2);
    expect(env.valueAt(start - MINUTE)).toBeNull();
    expect(env.valueAt(start + 31 * MINUTE)).toBeNull();
  });

  it("takes quantiles the way the README helicorder does", () => {
    expect(quantile([5, 1, 4, 2, 3], 0.2)).toBe(2);
    expect(quantile([], 0.2)).toBe(0);
  });
});

describe("flags", () => {
  const flag = (id: string, impact: "major" | "critical", t: number) => ({ incident: incident({ id, impact }), t });

  it("keeps at most ten, critical first, and skips labels that collide on a row", () => {
    const many = Array.from({ length: 14 }, (_, i) => ({ flag: flag(`m${i}`, "major", i), row: i, x: 10, width: 60 }));
    expect(pickFlags(many)).toHaveLength(10);
    const crowded = [
      { flag: flag("a", "major", 1), row: 3, x: 100, width: 60 },
      { flag: flag("b", "critical", 2), row: 3, x: 130, width: 60 },
      { flag: flag("c", "major", 3), row: 3, x: 400, width: 60 },
      { flag: flag("d", "major", 4), row: 4, x: 100, width: 60 },
    ];
    expect(pickFlags(crowded).map((p) => p.flag.incident.id)).toEqual(["b", "c", "d"]);
  });
});

describe("helicorder texture and rows", () => {
  it("draws the same texture for the same hour on every load", () => {
    expect(traceNoise(493876, 120.5)).toBe(traceNoise(493876, 120.5));
    expect(traceNoise(493876, 120.5)).not.toBe(traceNoise(493877, 120.5));
    expect(hash32(42)).toBe(hash32(42));
    for (let x = 0; x < 400; x += 3.3) expect(Math.abs(traceNoise(7, x))).toBeLessThanOrEqual(1);
  });

  it("lays out one row per hour, oldest first, the last holding now", () => {
    const now = Date.parse("2026-11-03T13:17:00Z");
    const rows = helicorderRows(now, 24, "utc");
    expect(rows).toHaveLength(24);
    expect(rows[23]).toBe(Date.parse("2026-11-03T13:00:00Z"));
    expect(rows[0]).toBe(Date.parse("2026-11-02T14:00:00Z"));
    expect(rows[1] - rows[0]).toBe(HOUR);
  });
});

describe("incidents behind each bucket", () => {
  it("uses the API's rule and reproduces the total", () => {
    const s = series([0, 0, 0, 0, 0, 0]);
    const a = incident({ id: "a:1", impact: "major", started_at: new Date(start + 12 * MINUTE).toISOString(), resolved_at: new Date(start + 31 * MINUTE).toISOString() });
    const b = incident({ id: "b:1", impact: "none", started_at: new Date(start - HOUR).toISOString() });
    const list = [a, b];
    const buckets = openByBucket(list, s, start + 55 * MINUTE);
    expect(buckets[0]).toEqual([1]);
    expect(buckets[1]).toEqual([0, 1]);
    expect(buckets[3]).toEqual([0, 1]);
    expect(buckets[4]).toEqual([1]);
    expect(weightOf(list, buckets[2])).toBe(2.5);
  });

  it("flags only major and critical starts inside the window", () => {
    const list = [
      incident({ id: "a", impact: "major", started_at: "2026-11-03T11:08:37Z" }),
      incident({ id: "b", impact: "minor", started_at: "2026-11-03T11:10:00Z" }),
      incident({ id: "c", impact: "critical", started_at: "2026-10-21T14:38:58Z" }),
    ];
    const flags = majorStarts(list, Date.parse("2026-11-02T14:00:00Z"), Date.parse("2026-11-03T14:00:00Z"));
    expect(flags.map((f) => f.incident.id)).toEqual(["a"]);
  });
});
