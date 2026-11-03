import type { CensusShare, CensusSummary } from "@/lib/api/types";
import type { ShareRow } from "@/components/charts/share-bars";

/** Rows that are not a provider: no provider in this role, or providers outside the taxonomy. */
export function isNonProvider(id: string): boolean {
  return id === "none" || id === "other" || id === "unknown";
}

export function shareName(field: string, t: CensusShare): string {
  if (t.id === "none") return field === "edge_provider" ? "No edge network" : field === "mx_provider" ? "No mail server" : "None";
  if (t.id === "other") return "Other providers, outside the taxonomy";
  return t.name;
}

/** Census rows as bars. `basis` picks the denominator the census reports. */
export function toRows(field: string, top: CensusShare[], basis: "determinable" | "resolved"): ShareRow[] {
  return top.map((t) => ({
    id: t.id,
    name: shareName(field, t),
    share: (basis === "determinable" ? t.pct_of_determinable : t.pct_of_resolved) / 100,
    count: t.count,
    muted: isNonProvider(t.id),
  }));
}

export const BUCKETS = [
  { key: "top_100", label: "Top 100" },
  { key: "top_1k", label: "Top 1,000" },
  { key: "top_10k", label: "Top 10,000" },
] as const;

export type BucketKey = (typeof BUCKETS)[number]["key"];

/** How many ranked sites the census covers, from its own counts. */
export function rankedTotal(c: CensusSummary): number {
  return c.counts.by_source?.tranco ?? c.rank_buckets.top_10k?.counts.total ?? c.counts.total;
}

/** HHI as the census reports it, on the 0 to 10,000 scale; older documents used 0 to 1. */
export function hhiText(hhi: number): string {
  const v = hhi <= 1 ? hhi * 10000 : hhi;
  return `${Math.round(v).toLocaleString("en-US")} of 10,000`;
}
