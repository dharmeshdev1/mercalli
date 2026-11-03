import type { Health, Impact, Status } from "@/lib/api/types";

/**
 * The severity vocabulary. Color comes from the MMI ramp tokens; every glyph also differs in
 * shape (fill level means severity), so nothing depends on color alone.
 */

export type Tone = "operational" | "maintenance" | "degraded" | "partial" | "major" | "critical" | "faint";

/** Fill level means severity: empty ring, center dot, half disc, full disc, disc in a ring. */
export type GlyphKind = "ring" | "dot" | "half" | "full" | "epicenter" | "dashed" | "dotted" | "small";

export interface GlyphSpec {
  kind: GlyphKind;
  tone: Tone;
}

export const HEALTH_LABEL: Record<Health, string> = {
  operational: "Operational",
  maintenance: "Maintenance",
  degraded: "Degraded",
  partial_outage: "Partial outage",
  major_outage: "Major outage",
  unknown: "Unknown",
};

/** Higher is worse. Unknown sits above maintenance: a page we cannot read is a problem. */
export const HEALTH_RANK: Record<Health, number> = {
  operational: 0,
  maintenance: 1,
  unknown: 2,
  degraded: 3,
  partial_outage: 4,
  major_outage: 5,
};

export const HEALTH_GLYPH: Record<Health, GlyphSpec> = {
  operational: { kind: "ring", tone: "operational" },
  maintenance: { kind: "dashed", tone: "maintenance" },
  unknown: { kind: "dotted", tone: "faint" },
  degraded: { kind: "dot", tone: "degraded" },
  partial_outage: { kind: "half", tone: "partial" },
  major_outage: { kind: "full", tone: "major" },
};

export const IMPACT_LABEL: Record<Impact, string> = {
  none: "No impact",
  maintenance: "Maintenance",
  minor: "Minor",
  major: "Major",
  critical: "Critical",
};

/** Higher is worse. */
export const IMPACT_RANK: Record<Impact, number> = {
  none: 0,
  maintenance: 1,
  minor: 2,
  major: 3,
  critical: 4,
};

/** Seismograph weights, as documented in docs/api.md. */
export const IMPACT_WEIGHT: Record<Impact, number> = {
  critical: 3,
  major: 2,
  minor: 1,
  none: 0.5,
  maintenance: 0.25,
};

export const IMPACT_GLYPH: Record<Impact, GlyphSpec> = {
  none: { kind: "small", tone: "faint" },
  maintenance: { kind: "dashed", tone: "maintenance" },
  minor: { kind: "dot", tone: "degraded" },
  major: { kind: "full", tone: "major" },
  critical: { kind: "epicenter", tone: "critical" },
};

export const STATUS_LABEL: Record<Status, string> = {
  investigating: "Investigating",
  identified: "Identified",
  monitoring: "Monitoring",
  resolved: "Resolved",
  postmortem: "Postmortem",
  scheduled: "Scheduled",
  in_progress: "In progress",
  verifying: "Verifying",
  completed: "Completed",
};

export function statusLabel(status: string): string {
  return (STATUS_LABEL as Record<string, string>)[status] ?? status.replace(/_/g, " ");
}

export function impactLabel(impact: string): string {
  return (IMPACT_LABEL as Record<string, string>)[impact] ?? impact;
}

export function healthLabel(health: string): string {
  return (HEALTH_LABEL as Record<string, string>)[health] ?? health;
}

export function healthGlyph(health: string): GlyphSpec {
  return (HEALTH_GLYPH as Record<string, GlyphSpec>)[health] ?? HEALTH_GLYPH.unknown;
}

export function impactGlyph(impact: string): GlyphSpec {
  return (IMPACT_GLYPH as Record<string, GlyphSpec>)[impact] ?? IMPACT_GLYPH.none;
}

export function healthRank(health: string): number {
  return (HEALTH_RANK as Record<string, number>)[health] ?? 0;
}

export function impactRank(impact: string): number {
  return (IMPACT_RANK as Record<string, number>)[impact] ?? 0;
}

export function impactWeight(impact: string): number {
  return (IMPACT_WEIGHT as Record<string, number>)[impact] ?? 0.5;
}

/** The CSS custom property that colors a tone. */
export function toneVar(tone: Tone): string {
  return tone === "faint" ? "var(--ink-faint)" : `var(--sev-${tone})`;
}

/** Category ids to the names people use. Unknown categories are humanized. */
export const CATEGORY_LABEL: Record<string, string> = {
  cloud: "Cloud",
  edge: "Edge and CDN",
  dns: "DNS",
  hosting: "Hosting",
  devtools: "Developer tools",
  data: "Data",
  observability: "Observability",
  ai: "AI platforms",
  payments: "Payments",
  messaging: "Messaging",
  collaboration: "Collaboration",
  identity: "Identity",
  commerce: "Commerce",
  media: "Media",
  consumer: "Consumer",
  other: "Other",
};

export function categoryLabel(category: string): string {
  if (CATEGORY_LABEL[category]) return CATEGORY_LABEL[category];
  const s = category.replace(/[_-]/g, " ");
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}
