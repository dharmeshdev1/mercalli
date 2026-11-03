"use client";

import Link from "next/link";
import { ShareBars } from "@/components/charts/share-bars";
import { FixtureNote, Pending, Section } from "@/components/ui/section";
import type { CensusField, CensusSummary } from "@/lib/api/types";
import { useArtifact } from "@/lib/api/use-artifact";
import { rankedTotal, toRows } from "@/lib/census";
import { formatCount, formatShare } from "@/lib/format";

const FIELDS: { field: CensusField; label: string }[] = [
  { field: "dns_provider", label: "DNS provider" },
  { field: "edge_provider", label: "Edge network" },
  { field: "hosting_provider", label: "Hosting" },
];

export function CensusTeaser() {
  const doc = useArtifact<CensusSummary>("census-summary.json");
  const c = doc.data;
  const bucket = c?.rank_buckets.top_10k ?? null;
  const fields = bucket?.fields ?? c?.fields;
  return (
    <Section
      id="who-depends"
      compact={!c}
      title="Who depends on whom"
      lead={
        <p>
          {c
            ? `Which providers the top ${formatCount(rankedTotal(c))} websites rely on, as a share of the ${formatCount(bucket?.counts.resolved ?? c.counts.resolved)} that resolved.`
            : "Which DNS, edge network and hosting providers the most visited websites rely on."}
        </p>
      }
      aside={<Link href="/census/">See the full census</Link>}
    >
      {doc.status === "loading" ? (
        <div className="h-56" aria-busy="true" />
      ) : !c || !fields ? (
        <Pending title={doc.status === "missing" ? "The census has not been published yet." : "The census could not be loaded."}>
          The census resolves the DNS, edge network, hosting, certificates and mail of the most visited domains and is
          published as census-summary.json.
        </Pending>
      ) : (
        <div>
          <FixtureNote show={doc.fixture} />
          <div className="grid grid-cols-1 gap-x-8 gap-y-8 md:grid-cols-3">
            {FIELDS.map(({ field, label }) => {
              const f = fields[field];
              if (!f) return null;
              const rows = toRows(field, f.top, "resolved").slice(0, 5);
              return (
                <div key={field}>
                  <h3 className="mb-3 text-base font-medium">{label}</h3>
                  <ShareBars
                    label={label}
                    rows={rows}
                    max={Math.max(...rows.map((r) => r.share))}
                    format={(r) => formatShare(r.share, r.share < 0.0995 ? 1 : 0)}
                  />
                </div>
              );
            })}
          </div>
        </div>
      )}
    </Section>
  );
}
