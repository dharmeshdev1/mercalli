import type { BlastEval, ModelCard, RecoveryMetrics } from "../src/lib/api/types.ts";

/**
 * Development fixture for model.json. The shape follows the training report the engine writes;
 * every number here is invented for development and never shipped.
 */

const metrics = (n: number, mae: number, med: number, w2: number, cov: number, width: number): RecoveryMetrics => ({
  n,
  mae_log: mae,
  median_abs_error_min: med,
  within_2x: w2,
  coverage_80: cov,
  median_width_ratio: width,
});

const blastEval = (
  ap: number,
  p10: number,
  r10: number,
  h10: number,
  p10ci: [number, number],
  r10ci: [number, number],
  h10ci: [number, number],
): BlastEval => ({
  pairs: 2000000,
  positives: 190,
  base_rate: 0.000095,
  average_precision: ap,
  upstream_incidents_with_positive: 86,
  precision_at_10: p10,
  recall_at_10: r10,
  hit_at_10: h10,
  ci95: { precision_at_10: p10ci, recall_at_10: r10ci, hit_at_10: h10ci },
});

export const modelFixture: ModelCard = {
  generated_at: "2026-11-03T12:00:00Z",
  summary: { recovery_version: "recovery-20261103-1200", blast_version: "blast-20261103-1200" },
  recovery: {
    split: { calib_start: "2026-07-01T00:00:00Z", test_start: "2026-09-01T00:00:00Z" },
    counts: {
      incidents: { train: 18432, calib: 3118, test: 2954 },
      snapshots: { train: 61207, calib: 10544, test: 9871 },
    },
    test: {
      all_updates: metrics(9871, 0.81, 37.4, 0.47, 0.806, 8.9),
      first_update: metrics(2954, 0.97, 52.1, 0.39, 0.794, 11.6),
    },
    baselines: {
      phase_median: {
        all_updates: { n: 9871, mae_log: 1.14, median_abs_error_min: 61.0, within_2x: 0.31 },
        first_update: { n: 2954, mae_log: 1.21, median_abs_error_min: 70.5, within_2x: 0.28 },
      },
      elapsed_and_phase: {
        all_updates: { n: 9871, mae_log: 1.02, median_abs_error_min: 54.2, within_2x: 0.35 },
        first_update: { n: 2954, mae_log: 1.17, median_abs_error_min: 66.8, within_2x: 0.3 },
      },
      service_history: {
        all_updates: { n: 9871, mae_log: 0.98, median_abs_error_min: 49.6, within_2x: 0.37 },
        first_update: { n: 2954, mae_log: 1.09, median_abs_error_min: 59.3, within_2x: 0.33 },
      },
    },
    reliability: { "0.1": 0.11, "0.5": 0.49, "0.9": 0.88 },
    feature_importance: {
      prior_minus_elapsed: 0.214,
      elapsed_log: 0.163,
      title: 0.121,
      phase: 0.094,
      since_update_log: 0.071,
      body: 0.066,
      prior_log_median: 0.052,
      n_updates: 0.038,
      category: 0.031,
      reached_monitoring: 0.027,
      severity: 0.019,
      gap_log: 0.015,
      n_components: 0.011,
      prior_long_share: 0.009,
      hour_cos: 0.004,
      weekend: 0.002,
    },
    by_phase: {
      investigating: metrics(3910, 0.94, 48.2, 0.41, 0.79, 10.4),
      identified: metrics(2465, 0.83, 39.0, 0.45, 0.81, 8.7),
      monitoring: metrics(3102, 0.66, 24.8, 0.55, 0.82, 6.9),
      other: metrics(394, 0.88, 41.7, 0.43, 0.8, 9.6),
    },
    by_category: {
      cloud: metrics(1840, 0.86, 44.1, 0.44, 0.8, 9.8),
      edge: metrics(911, 0.71, 22.9, 0.52, 0.83, 7.4),
      devtools: metrics(2210, 0.78, 33.0, 0.48, 0.81, 8.3),
      messaging: metrics(1702, 0.92, 71.4, 0.4, 0.78, 10.9),
      payments: metrics(806, 0.74, 28.6, 0.5, 0.82, 7.8),
    },
  },
  blast: {
    window_minutes: { before: 45, after: 180 },
    label: "the other service posts an incident naming the upstream provider within the window",
    test: blastEval(0.031, 0.12, 0.63, 0.75, [0.1, 0.14], [0.54, 0.72], [0.65, 0.84]),
    baselines: {
      named_in_reports: blastEval(0.036, 0.11, 0.6, 0.71, [0.09, 0.14], [0.5, 0.69], [0.61, 0.8]),
      census_dependency: blastEval(0.004, 0.03, 0.15, 0.22, [0.02, 0.05], [0.09, 0.21], [0.14, 0.31]),
      base_rate_only: blastEval(0.0003, 0.01, 0.04, 0.07, [0.002, 0.02], [0.004, 0.08], [0.02, 0.13]),
    },
  },
  live: { n: 412, coverage_80: 0.78, mae_log: 0.86 },
  intensity: {
    thresholds: [4, 8, 12, 16, 22, 28, 36, 48, 64],
    percentiles: [50, 75, 90, 95, 98, 99, 99.5, 99.8, 99.95],
    weights: { critical: 4, major: 2, minor: 1 },
    hub_dependents: 20,
    hours: 8760,
  },
};
