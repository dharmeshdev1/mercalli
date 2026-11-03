import { describe, expect, it } from "vitest";
import { forecastGeometry, forecastTimes, PAST_MAX_SHARE } from "./forecast";
import { MINUTE, parseTime } from "./format";
import type { Forecast } from "./api/types";

const forecast: Forecast = {
  p10: 12,
  p50: 41,
  p90: 180,
  eta_p10: "2026-11-02T18:30:00Z",
  eta_p50: "2026-11-02T18:59:00Z",
  eta_p90: "2026-11-02T21:18:00Z",
  made_at: "2026-11-02T18:18:00Z",
  elapsed_min: 31.5,
  model_version: "recovery-20261103-1200",
};

describe("forecastGeometry", () => {
  it("places start, now and the percentiles in order on a linear axis", () => {
    const now = parseTime("2026-11-02T18:18:00Z") ?? 0;
    const times = forecastTimes("2026-11-02T17:46:23Z", forecast, now);
    expect(times).not.toBeNull();
    const g = forecastGeometry(times!, 600);
    expect(g.compressed).toBe(false);
    expect(g.overdue).toBe(false);
    expect(g.x(g.axisStart)).toBe(0);
    expect(g.x(g.axisEnd)).toBeCloseTo(600);
    expect(g.nowX).toBeLessThan(g.p10X);
    expect(g.p10X).toBeLessThan(g.p50X);
    expect(g.p50X).toBeLessThan(g.p90X);
    expect(g.p90X).toBeLessThan(600);
  });

  it("compresses a long past so the future stays readable", () => {
    const now = parseTime("2026-11-02T18:18:00Z") ?? 0;
    const times = forecastTimes("2026-10-30T00:00:00Z", forecast, now)!;
    const g = forecastGeometry(times, 1000);
    expect(g.compressed).toBe(true);
    expect(g.nowX).toBeCloseTo(PAST_MAX_SHARE * 1000);
    expect(g.p90X - g.p10X).toBeGreaterThan(300);
  });

  it("marks an incident that outlived its range as overdue and keeps now on the axis", () => {
    const now = parseTime("2026-11-02T23:00:00Z") ?? 0;
    const g = forecastGeometry(forecastTimes("2026-11-02T17:46:23Z", forecast, now)!, 500);
    expect(g.overdue).toBe(true);
    expect(g.p90X).toBeLessThan(g.nowX);
    expect(g.nowX).toBeLessThan(500);
  });

  it("is monotonic everywhere", () => {
    const now = parseTime("2026-11-02T20:00:00Z") ?? 0;
    const g = forecastGeometry(forecastTimes("2026-11-01T00:00:00Z", forecast, now)!, 800);
    let prev = -1;
    for (let t = g.axisStart; t <= g.axisEnd; t += 7 * MINUTE) {
      const x = g.x(t);
      expect(x).toBeGreaterThanOrEqual(prev);
      prev = x;
    }
  });
});
