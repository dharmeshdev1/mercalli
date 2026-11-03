/**
 * Types for every document of the Mercalli API v1, as documented in docs/api.md.
 *
 * Times are UTC ISO 8601 strings ending in Z. Durations are minutes. Fields are only ever
 * added within v1, so readers should tolerate extra fields and treat documented optional
 * artifacts (graph, model card, census, replays) as possibly missing.
 */

/** Worst current state of a service's components. */
export type Health =
  | "operational"
  | "maintenance"
  | "degraded"
  | "partial_outage"
  | "major_outage"
  | "unknown";

/** The vendor's own label for an incident, mapped onto one scale. */
export type Impact = "none" | "minor" | "major" | "critical" | "maintenance";

/**
 * Where an incident is in its lifecycle. The first five are documented; the engine also
 * carries the maintenance lifecycle (scheduled, in progress, verifying, completed).
 */
export type Status =
  | "investigating"
  | "identified"
  | "monitoring"
  | "resolved"
  | "postmortem"
  | "scheduled"
  | "in_progress"
  | "verifying"
  | "completed";

export interface LatestUpdate {
  at: string;
  status: Status;
  body: string;
}

/** Time left until the vendor marks the incident resolved, in minutes from `made_at`. */
export interface Forecast {
  p10: number;
  p50: number;
  p90: number;
  eta_p10: string;
  eta_p50: string;
  eta_p90: string;
  made_at: string;
  elapsed_min: number;
  model_version: string;
}

export interface BlastEntry {
  service_id: string;
  service_name: string;
  probability: number;
  evidence: string[];
}

/** Used in overview.json and services/<id>.json. */
export interface IncidentSummary {
  id: string;
  path: string;
  service_id: string;
  service_name: string;
  category: string;
  title: string;
  impact: Impact;
  status: Status;
  started_at: string;
  resolved_at: string | null;
  resolved_inferred: boolean;
  duration_min: number | null;
  elapsed_min: number;
  components: string[];
  regions: string[];
  url: string | null;
  updates_count: number;
  latest: LatestUpdate | null;
  first_seen_at: string | null;
  forecast: Forecast | null;
  blast: BlastEntry[] | null;
}

export interface Intensity {
  level: number;
  numeral: string;
  label: string;
  score: number;
  /** Share of past hours with a lower score; null at level I. */
  percentile: number | null;
  /** False while the thresholds are defaults rather than computed from a year of history. */
  calibrated: boolean;
}

export type Counts = Partial<Record<Health, number>> & {
  services: number;
  open_incidents: number;
};

export interface BoardService {
  id: string;
  name: string;
  category: string;
  health: Health;
  open: number;
  curated: boolean;
}

export interface SeismographSeries {
  id: string;
  name: string;
  values: number[];
}

/** 48 hours of activity in fixed buckets. Weights: critical 3, major 2, minor 1, none 0.5, maintenance 0.25. */
export interface Seismograph {
  start: string;
  bucket_minutes: number;
  points: number;
  series: SeismographSeries[];
  total: number[];
  /** Summed weight of incidents that began in each bucket. Absent in older documents. */
  arrivals?: number[];
}

export interface Overview {
  generated_at: string;
  counts: Counts;
  intensity: Intensity | null;
  services: BoardService[];
  /** Open incidents with impact minor or worse, open for less than 7 days, worst first. */
  active: IncidentSummary[];
  /** Open for more than 7 days: usually incidents a vendor has not closed. */
  long_running: IncidentSummary[];
  /** Open advisories and maintenance (impact none or maintenance). */
  notices: IncidentSummary[];
  /** Incidents resolved in the last 48 hours, most recent first. */
  recent: IncidentSummary[];
  seismograph: Seismograph;
}

export interface Tick {
  started_at: string;
  duration_s: number;
  polled: number;
  ok: number;
  failed: number;
  changes: Record<string, number>;
  refreshed: number;
  models: Record<string, unknown>;
}

export interface Meta {
  api_version: number;
  generated_at: string;
  edition: string;
  version: string;
  services: number;
  services_ok: number;
  tick: Tick | null;
  models: ModelSummary | null;
  census: { generated_at: string | null; domains: number | null } | null;
}

export interface Stats90 {
  incidents: number;
  minutes: number;
  median_min: number | null;
}

export interface ServiceEntry {
  id: string;
  name: string;
  category: string;
  health: Health;
  indicator: string;
  description: string;
  curated: boolean;
  rank: number | null;
  status_url: string;
  domains: string[];
  last_polled_at: string | null;
  last_error: string | null;
  /** Number of components on the status page. */
  components: number;
  stats_90d: Stats90;
}

export interface ServicesDoc {
  generated_at: string;
  services: ServiceEntry[];
}

export interface Component {
  id: string;
  name: string;
  status: Health;
  group: string | null;
}

export interface ServiceHeader {
  id: string;
  name: string;
  category: string;
  health: Health;
  indicator: string;
  description: string;
  status_url: string;
  domains: string[];
  curated: boolean;
  last_polled_at: string | null;
  last_error: string | null;
}

export interface ServiceDoc {
  generated_at: string;
  service: ServiceHeader;
  components: Component[];
  incidents: IncidentSummary[];
  stats_90d: Stats90 | null;
  /** Edges from this service to what it depends on. */
  depends_on: GraphEdge[];
  /** Edges from services that depend on this one. */
  dependents: GraphEdge[];
}

export interface IncidentUpdate {
  at: string;
  status: Status;
  body: string;
  components: string[];
}

/** One forecast made while the incident was open. */
export interface ForecastPoint {
  made_at: string;
  elapsed_min: number;
  phase: string;
  p10: number;
  p50: number;
  p90: number;
  eta_p50: string;
}

/** A past incident that reads like this one. The engine writes these from similar.json. */
export interface SimilarIncident {
  id: string;
  service_id?: string;
  service_name?: string;
  title?: string;
  impact?: Impact;
  started_at?: string;
  duration_min?: number | null;
  similarity?: number;
}

export interface IncidentDoc extends IncidentSummary {
  updates: IncidentUpdate[];
  forecast_history: ForecastPoint[];
  similar: SimilarIncident[];
}

export type EdgeKind = "dns" | "edge" | "hosting" | "named" | "cofailure";

export interface GraphNode {
  id: string;
  name: string;
  category: string;
  kind: "service" | "provider";
  health?: Health;
  dependents: number;
}

/** Points from a dependent (source) to what it depends on (target). */
export interface GraphEdge {
  source: string;
  target: string;
  kinds: EdgeKind[];
  weight: number;
  evidence: string[];
}

export interface GraphDoc {
  generated_at: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface RecoveryMetrics {
  n: number;
  mae_log: number;
  median_abs_error_min: number;
  within_2x: number;
  coverage_80: number;
  median_width_ratio: number;
}

export type BaselineMetrics = Pick<RecoveryMetrics, "n" | "mae_log" | "median_abs_error_min" | "within_2x">;

export interface SplitCounts {
  train: number;
  calib: number;
  test: number;
}

/** Evaluation of a blast radius ranker on held-out upstream incidents. */
export interface BlastEval {
  pairs: number;
  positives: number;
  base_rate: number;
  average_precision: number;
  upstream_incidents_with_positive: number;
  precision_at_10: number;
  recall_at_10: number;
  hit_at_10: number;
  /** 95% intervals, as [low, high]. */
  ci95?: Partial<Record<"precision_at_10" | "recall_at_10" | "hit_at_10", [number, number]>>;
  brier?: number;
}

/** The incident dataset the models were trained on (also published as data/dataset.json). */
export interface DatasetInfo {
  built_at: string;
  incidents: number;
  updates: number;
  services: number;
  first: string;
  last: string;
  public: boolean;
}

export interface ModelSummary {
  recovery_version: string;
  blast_version: string;
  dataset?: DatasetInfo;
}

export interface ModelCard {
  generated_at: string;
  summary: ModelSummary;
  recovery: {
    version?: string;
    trained_at?: string;
    split: { calib_start: string; test_start: string; test_end?: string | null };
    counts: { incidents: SplitCounts; snapshots: SplitCounts };
    conformal_widening_log?: number;
    test: { all_updates: RecoveryMetrics; first_update: RecoveryMetrics };
    baselines: Record<string, { all_updates: BaselineMetrics; first_update: BaselineMetrics }>;
    /** Coverage of the raw 10th to 90th percentile range, before conformal calibration. */
    uncalibrated_coverage_80?: number;
    /** Share of outcomes at or below each raw predicted quantile, keyed by the quantile ("0.1"). */
    reliability: Record<string, number>;
    feature_importance: Record<string, number>;
    by_phase: Record<string, RecoveryMetrics>;
    by_category: Record<string, RecoveryMetrics>;
    production?: { version: string };
  };
  blast: {
    version?: string;
    trained_at?: string;
    /** The window around an upstream incident in which a follow-on incident counts. */
    window_minutes?: { before: number; after: number };
    counts?: Record<string, unknown>;
    /** What a positive means, in words. */
    label?: string;
    features?: string[];
    test: BlastEval;
    /** The same evaluation on major and critical upstream incidents only. */
    test_major?: BlastEval;
    baselines: Record<string, BlastEval>;
  };
  live: { n: number; coverage_80: number; mae_log: number } | null;
  intensity: {
    generated_at?: string;
    /** Hours of history the thresholds were computed from. */
    hours?: number;
    thresholds: number[];
    percentiles: number[];
    weights: Record<string, number>;
    /** Providers with at least this many dependents weigh more. */
    hub_dependents?: number;
    median_score?: number;
    max_score?: number;
  };
}

export type CensusField =
  | "dns_provider"
  | "edge_provider"
  | "hosting_provider"
  | "hosting_region"
  | "tls_issuer"
  | "mx_provider"
  | "hosting_provider_without_edge";

export interface CensusShare {
  /** Provider id; "none" means no provider in this role, "other" one outside the taxonomy. */
  id: string;
  name: string;
  count: number;
  /** Percent of resolved domains where this field could be decided. */
  pct_of_determinable: number;
  /** Percent of all resolved domains. */
  pct_of_resolved: number;
}

export interface CensusFieldSummary {
  determinable: number;
  resolved: number;
  distinct: number;
  /** Herfindahl-Hirschman index on the 0 to 10,000 scale. */
  hhi: number;
  top: CensusShare[];
}

export type CensusFields = Partial<Record<CensusField, CensusFieldSummary>>;

export interface CensusCounts {
  total: number;
  by_source?: Record<string, number>;
  zone_found?: number;
  resolved: number;
  http_response?: number;
  http_ok: number;
  http_status_classes?: Record<string, number>;
  tls_issuer_found?: number;
  redirected_off_domain?: number;
}

export interface CensusBucket {
  counts: CensusCounts;
  fields: CensusFields;
}

export interface SingleProvider {
  count: number;
  comparable?: number;
  pct_of_comparable?: number;
  pct_of_resolved: number;
  by_provider?: { id: string; name: string; count: number }[];
}

export interface CensusSummary {
  generated_at: string;
  vantage: { description: string; dns_resolvers?: string[]; dns_note?: string; http_note?: string; user_agent?: string };
  run?: { started_at: string; wall_seconds: number; domains: number; tranco_top?: number };
  dataset_dates?: Record<string, string>;
  population_note?: string;
  counts: CensusCounts;
  fields: CensusFields;
  single_provider: Record<string, SingleProvider>;
  aws_regions?: {
    aws_hosted: number;
    with_region: number;
    without_region: number;
    regions: { region: string; count: number; pct_of_aws_with_region: number; pct_of_resolved: number }[];
  };
  top_asns?: { asn: number; org: string; count: number; pct_of_resolved: number }[];
  rank_buckets: Partial<Record<"top_100" | "top_1k" | "top_10k", CensusBucket>>;
}

export interface ReplayEvent {
  service_id: string;
  service_name: string;
  category: string;
  incident_id: string;
  title: string;
  impact: Impact;
  offset_min: number;
  duration_min: number | null;
  names_upstream: boolean;
}

export interface Replay {
  slug: string;
  title: string;
  upstream: {
    service_id: string;
    service_name: string;
    /** The provider's own incident, when Mercalli has it on record. */
    incident_id: string | null;
    /** The incident's title when on record; otherwise a factual sentence about the cascade. */
    title: string;
    impact: Impact;
    /** The moment the replay clock counts from. */
    started_at: string;
    resolved_at: string | null;
    duration_min: number | null;
    /** False when Mercalli has no record of the provider's own incident. */
    on_record: boolean;
  };
  window_min: number;
  events: ReplayEvent[];
  totals: { services: number; named: number; categories: Record<string, number> };
}

export interface ReplaysDoc {
  generated_at?: string;
  replays: Replay[];
}
