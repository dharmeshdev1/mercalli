import type { Intensity } from "@/lib/api/types";
import type { Tone } from "@/lib/severity";

/** The ten levels of the scale, as numerals. Labels come from the API. */
export const NUMERALS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"] as const;

/**
 * Intensity levels on the Modified Mercalli color ramp: weak shaking is blue, strong is orange,
 * destructive is red.
 */
export function intensityTone(level: number): Tone {
  if (level <= 2) return "operational";
  if (level === 3) return "maintenance";
  if (level <= 5) return "degraded";
  if (level <= 7) return "partial";
  if (level <= 9) return "major";
  return "critical";
}

function formatScore(score: number): string {
  return Number.isInteger(score) ? String(score) : score.toFixed(1);
}

/** "VII, very strong" */
export function intensityName(i: Intensity): string {
  return `${i.numeral}, ${i.label.toLowerCase()}`;
}

/**
 * One line of meaning. Calibrated: "IV, light: busier than 75% of hours in the past year".
 * Until the thresholds are computed from history, the line says the scale is provisional
 * rather than presenting a default as a measurement.
 */
export function intensityMeaning(i: Intensity): string {
  if (!i.calibrated) {
    return `Score ${formatScore(i.score)} on a provisional scale, not yet ranked against past hours.`;
  }
  if (i.percentile == null) return "Below every threshold on the scale.";
  return `Busier than ${formatPercentile(i.percentile)}% of hours in the past year.`;
}

/** "IV, light: busier than 75% of hours in the past year" */
export function intensityLine(i: Intensity): string {
  const meaning = intensityMeaning(i);
  return `${intensityName(i)}: ${meaning[0].toLowerCase()}${meaning.slice(1, -1)}`;
}

function formatPercentile(p: number): string {
  return Number.isInteger(p) ? String(p) : String(Math.round(p * 100) / 100);
}
