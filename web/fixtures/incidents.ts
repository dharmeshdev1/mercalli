import { rng, weighted } from "./rng.ts";

/**
 * Development fixture for data/incidents.parquet, as rows. The columns follow the engine's
 * dataset (the public columns only). Rows are generated and never shipped.
 */

export interface IncidentRow {
  id: string;
  service_id: string;
  category: string;
  curated: boolean;
  title: string;
  impact: string;
  impact_rank: number;
  created_at: string;
  start: string;
  resolved_at: string | null;
  duration_min: number | null;
  n_updates: number;
  first_status: string;
  identified_at: string | null;
  monitoring_at: string | null;
  retroactive: boolean;
  n_components: number;
  components: string[];
  regions: string[];
  mentions: string[];
  generic_upstream: boolean;
  source: string;
  url: string | null;
}

const SERVICES: [string, string][] = [
  ["aws", "cloud"], ["azure", "cloud"], ["gcp", "cloud"], ["cloudflare", "edge"], ["fastly", "edge"],
  ["akamai", "edge"], ["github", "devtools"], ["gitlab", "devtools"], ["vercel", "hosting"],
  ["netlify", "hosting"], ["twilio", "messaging"], ["sendgrid", "messaging"], ["slack", "collaboration"],
  ["atlassian", "collaboration"], ["stripe", "payments"], ["okta", "identity"], ["datadog", "observability"],
  ["openai", "ai"], ["shopify", "commerce"], ["discord", "consumer"], ["mongodb", "data"], ["snowflake", "data"],
];
const WORDS = ["Elevated errors", "Degraded performance", "Increased latency", "Delayed processing", "Partial outage", "Connectivity issues", "Login failures", "API errors"];
const PARTS = ["for the API", "in the dashboard", "for webhooks", "in us-east-1", "in Europe", "for some customers", "for deployments", "for search"];
const IMPACTS: [string, number][] = [["minor", 62], ["major", 22], ["critical", 4], ["none", 7], ["maintenance", 5]];
const RANK: Record<string, number> = { none: 0, maintenance: 0, minor: 1, major: 2, critical: 3 };
const UPSTREAM = ["aws", "cloudflare", "gcp", "azure", "github", "fastly", "akamai", "twilio", "stripe", "okta", "openai"];

let cached: IncidentRow[] | null = null;

/** "2026-03-04 05:06:07.890": a UTC timestamp DuckDB's JSON reader types as TIMESTAMP. */
function ts(ms: number): string {
  return new Date(ms).toISOString().replace("T", " ").replace("Z", "");
}

export function incidentRowsFixture(): IncidentRow[] {
  if (cached) return cached;
  const random = rng(7340);
  const rows: IncidentRow[] = [];
  const from = Date.parse("2025-11-01T00:00:00Z");
  const to = Date.parse("2026-11-02T00:00:00Z");
  for (let i = 0; i < 2600; i++) {
    const [service_id, category] = SERVICES[Math.floor(random() * SERVICES.length)];
    const impact = weighted(random, IMPACTS);
    const start = from + random() * (to - from);
    const duration = Math.round(Math.exp(2.4 + random() * 3.6 + (impact === "critical" ? 0.6 : 0)) * 10) / 10;
    const retro = random() < 0.13;
    const created = retro ? start + duration * 60_000 + 5 * 60_000 : start + random() * 12 * 60_000;
    const resolved = start + duration * 60_000;
    const mentions = random() < 0.16 ? [UPSTREAM[Math.floor(random() * UPSTREAM.length)]].filter((m) => m !== service_id) : [];
    const n_updates = retro ? 1 : 2 + Math.floor(random() * 6);
    rows.push({
      id: `${service_id}:fx${i.toString(36)}`,
      service_id,
      category,
      curated: true,
      title: `${WORDS[Math.floor(random() * WORDS.length)]} ${PARTS[Math.floor(random() * PARTS.length)]}`,
      impact,
      impact_rank: RANK[impact],
      created_at: ts(created),
      start: ts(start),
      resolved_at: ts(resolved),
      duration_min: duration,
      n_updates,
      first_status: retro ? "resolved" : "investigating",
      identified_at: n_updates > 2 ? ts(start + duration * 20_000) : null,
      monitoring_at: n_updates > 3 ? ts(start + duration * 45_000) : null,
      retroactive: retro,
      n_components: 1 + Math.floor(random() * 4),
      components: [],
      regions: [],
      mentions,
      generic_upstream: mentions.length === 0 && random() < 0.05,
      source: "statuspage",
      url: null,
    });
  }
  cached = rows;
  return rows;
}
