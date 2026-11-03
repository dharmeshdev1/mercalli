import { describe, expect, it } from "vitest";
import type { Replay } from "./api/types";
import { formatClock } from "./format";
import { clockStart, narrate, placeEvents, radiusShare, ringsFor, sectors, stepAt } from "./replay";

const replay: Replay = {
  slug: "s",
  title: "t",
  upstream: {
    service_id: "edge",
    service_name: "Edge Co",
    title: "Widespread errors",
    impact: "critical",
    started_at: "2026-06-12T10:20:00Z",
    resolved_at: "2026-06-12T12:00:00Z",
    duration_min: 100,
    incident_id: "edge:1",
    on_record: true,
  },
  window_min: 180,
  events: [
    { service_id: "a", service_name: "A", category: "devtools", incident_id: "a:1", title: "x", impact: "major", offset_min: 4, duration_min: 30, names_upstream: true },
    { service_id: "b", service_name: "B", category: "devtools", incident_id: "b:1", title: "x", impact: "minor", offset_min: 9, duration_min: 30, names_upstream: false },
    { service_id: "c", service_name: "C", category: "payments", incident_id: "c:1", title: "x", impact: "minor", offset_min: 14, duration_min: 30, names_upstream: true },
    { service_id: "d", service_name: "D", category: "messaging", incident_id: "d:1", title: "x", impact: "minor", offset_min: 50, duration_min: 30, names_upstream: true },
  ],
  totals: { services: 4, named: 3, categories: { devtools: 2, payments: 1, messaging: 1 } },
};

describe("replay narration", () => {
  const steps = narrate(replay, (ms) => formatClock(ms, "utc"));

  it("tells the cascade in four to six factual steps", () => {
    expect(steps.length).toBeGreaterThanOrEqual(4);
    expect(steps.length).toBeLessThanOrEqual(6);
    expect(steps[0].lead).toBe("10:20 UTC");
    expect(steps[0].text).toBe("Edge Co posts an incident: Widespread errors.");
    expect(steps[1].text).toBe("A is the first to post an incident of its own.");
    expect(steps.at(-1)?.text).toBe("4 services in 3 categories posted incidents. 3 of them named Edge Co.");
  });

  it("counts from the data at each checkpoint", () => {
    const at15 = steps.find((s) => s.lead === "+15 min");
    expect(at15?.text).toBe("3 services have posted incidents, 2 of them name Edge Co.");
    expect(steps.some((s) => s.text === "Edge Co marks its incident resolved.")).toBe(true);
    for (const s of steps) expect(`${s.lead} ${s.text}`).not.toMatch(/[\u2013\u2014]/);
  });

  it("knows which step is current", () => {
    expect(stepAt(steps, 0)).toBe(0);
    expect(stepAt(steps, 5)).toBe(1);
    expect(stepAt(steps, 999)).toBe(steps.length - 1);
  });
});

describe("a cascade with no record of the provider's own incident", () => {
  const offRecord: Replay = {
    ...replay,
    upstream: { ...replay.upstream, incident_id: null, resolved_at: null, duration_min: null, on_record: false, title: "3 services named Edge Co in their own incident reports" },
  };
  const steps = narrate(offRecord, (ms) => formatClock(ms, "utc"));

  it("never says the provider posted anything", () => {
    for (const s of steps) expect(s.text).not.toMatch(/Edge Co posts|Edge Co marks/);
    expect(steps[0].text).toBe("A posts the first incident of the cascade.");
    expect(steps[0].lead).toBe("10:24 UTC");
  });

  it("still ends with the totals", () => {
    expect(steps.at(-1)?.text).toBe("4 services in 3 categories posted incidents. 3 of them named Edge Co.");
  });

  it("starts the clock early when services reported before the reference time", () => {
    expect(clockStart({ ...offRecord, events: [...offRecord.events, { ...offRecord.events[0], incident_id: "z:1", offset_min: -12 }] })).toBe(-12);
    expect(clockStart(offRecord)).toBe(0);
  });
});

describe("isoseismal geometry", () => {
  it("puts rings at 15, 30, 60, 120 and 180 minutes on a square root scale", () => {
    expect(ringsFor(180)).toEqual([15, 30, 60, 120, 180]);
    expect(radiusShare(180, 180)).toBe(1);
    expect(radiusShare(45, 180)).toBeCloseTo(0.5);
    expect(radiusShare(-3, 180)).toBe(0);
    expect(radiusShare(0, 180, 0.1)).toBeCloseTo(0.1);
    expect(radiusShare(180, 180, 0.1)).toBeCloseTo(1);
  });

  it("gives every category its own sector and every event a place in it", () => {
    const secs = sectors(replay.events);
    expect(secs.map((s) => s.category)).toEqual(["devtools", "messaging", "payments"]);
    const placed = placeEvents(replay.events, secs, 180);
    expect(placed).toHaveLength(4);
    for (const p of placed) {
      const s = secs.find((x) => x.category === p.event.category)!;
      expect(p.angle).toBeGreaterThanOrEqual(s.a0);
      expect(p.angle).toBeLessThanOrEqual(s.a1);
    }
  });
});
