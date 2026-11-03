import { MINUTE, parseTime } from "@/lib/format";
import type { Forecast } from "@/lib/api/types";

/** The past may take at most this share of the bar before it is drawn compressed. */
export const PAST_MAX_SHARE = 0.42;

export interface ForecastTimes {
  startedAt: number;
  now: number;
  p10: number;
  p50: number;
  p90: number;
}

export interface ForecastGeometry {
  axisStart: number;
  axisEnd: number;
  /** True when the past is squeezed into PAST_MAX_SHARE of the width (a break mark is drawn). */
  compressed: boolean;
  /** True when the incident is still open after the end of the 80% range. */
  overdue: boolean;
  x: (t: number) => number;
  nowX: number;
  p10X: number;
  p50X: number;
  p90X: number;
}

/** Absolute times of a forecast: the clock times the API gives for each percentile. */
export function forecastTimes(startedAt: string, forecast: Forecast, now: number): ForecastTimes | null {
  const s = parseTime(startedAt);
  const p10 = parseTime(forecast.eta_p10);
  const p50 = parseTime(forecast.eta_p50);
  const p90 = parseTime(forecast.eta_p90);
  if (s == null || p10 == null || p50 == null || p90 == null) return null;
  return { startedAt: s, now, p10, p50, p90 };
}

/**
 * Geometry of the forecast bar: a time axis from the incident start, through now, to a little
 * beyond the 90th percentile. When the incident has been open much longer than the forecast
 * reaches ahead, the past is compressed so the future stays readable.
 */
export function forecastGeometry(times: ForecastTimes, width: number): ForecastGeometry {
  const { startedAt, now } = times;
  const p10 = Math.max(times.p10, startedAt);
  const p50 = Math.max(times.p50, p10);
  const p90 = Math.max(times.p90, p50);
  const horizon = Math.max(p90, now);
  // Room past the horizon: a share of what lies ahead, at least enough to keep "now" off the edge.
  const pad = Math.max((horizon - now) * 0.12, Math.min((horizon - startedAt) * 0.04, 30 * MINUTE), 5 * MINUTE);
  const axisStart = startedAt;
  const axisEnd = horizon + pad;
  const total = axisEnd - axisStart;
  const past = Math.max(0, now - axisStart);
  const compressed = total > 0 && past / total > PAST_MAX_SHARE;

  const x = (t: number): number => {
    const c = Math.min(Math.max(t, axisStart), axisEnd);
    if (total <= 0) return 0;
    if (!compressed) return ((c - axisStart) / total) * width;
    const split = PAST_MAX_SHARE * width;
    if (c <= now) return past > 0 ? ((c - axisStart) / past) * split : split;
    return split + ((c - now) / (axisEnd - now)) * (width - split);
  };

  return {
    axisStart,
    axisEnd,
    compressed,
    overdue: now > p90,
    x,
    nowX: x(now),
    p10X: x(p10),
    p50X: x(p50),
    p90X: x(p90),
  };
}
