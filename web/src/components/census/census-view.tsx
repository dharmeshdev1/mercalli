"use client";

import { useId, useMemo, useState } from "react";
import { ShareBars } from "@/components/charts/share-bars";
import { PageHeader } from "@/components/ui/page-header";
import { FixtureNote, Pending, Section } from "@/components/ui/section";
import { Ago } from "@/components/ui/time";
import type { CensusField, CensusFields, CensusSummary } from "@/lib/api/types";
import { useArtifact } from "@/lib/api/use-artifact";
import { BUCKETS, hhiText, isNonProvider, rankedTotal, toRows, type BucketKey } from "@/lib/census";
import { formatCount, formatShare } from "@/lib/format";

const FIELDS: { field: CensusField; label: string; role: string }[] = [
  { field: "dns_provider", label: "DNS provider", role: "answers for their domain names" },
  { field: "edge_provider", label: "Edge network", role: "serves their traffic at the edge" },
  { field: "hosting_provider", label: "Hosting provider", role: "hosts their origin" },
  { field: "hosting_region", label: "Cloud region", role: "runs them in one region" },
  { field: "tls_issuer", label: "Certificate authority", role: "issued their certificates" },
  { field: "mx_provider", label: "Mail provider", role: "receives their mail" },
  { field: "hosting_provider_without_edge", label: "Hosting, for sites with no edge network", role: "hosts them with nothing in front" },
];

const SINGLE_LABEL: Record<string, string> = {
  dns_and_edge: "The same provider for DNS and the edge network",
  dns_and_hosting: "The same provider for DNS and hosting",
  dns_and_front_door: "The same provider for DNS and the front door",
  dns_edge_and_hosting: "One provider for DNS, edge network and hosting",
  dns_multi_provider: "DNS spread over more than one provider",
};

const pct = (v: number) => formatShare(v / 100, v < 10 ? 1 : 0);

export function CensusView() {
  const doc = useArtifact<CensusSummary>("census-summary.json");
  const c = doc.data;
  const catalog = c?.counts.by_source?.catalog;
  return (
    <>
      <PageHeader
        title="Census"
        lead={
          <p>
            {c ? `The top ${formatCount(rankedTotal(c))} websites` : "The most visited websites"}
            {catalog ? `, plus ${formatCount(catalog)} domains of the services Mercalli watches,` : ""} and which DNS provider, edge
            network, hosting provider, cloud region, certificate authority and mail provider each one relies on.
          </p>
        }
        meta={
          c ? (
            <>
              Resolved {formatCount(c.counts.resolved)} of {formatCount(c.counts.total)} domains, and {formatCount(c.counts.http_ok)}{" "}
              answered HTTP without an error. Measured <Ago iso={c.generated_at} />.
            </>
          ) : null
        }
      />
      {doc.status === "loading" ? (
        <div className="h-[60vh]" aria-busy="true" />
      ) : !c ? (
        <Pending title={doc.status === "missing" ? "The census has not been published yet." : "The census could not be loaded."}>
          The census is measured offline and published as census-summary.json. It will appear here after its first run.
        </Pending>
      ) : (
        <CensusBody census={c} fixture={doc.fixture} />
      )}
    </>
  );
}

function CensusBody({ census, fixture }: { census: CensusSummary; fixture: boolean }) {
  const available = BUCKETS.filter((b) => census.rank_buckets[b.key]);
  const [bucket, setBucket] = useState<BucketKey | "all">(available.some((b) => b.key === "top_10k") ? "top_10k" : "all");
  const scope = bucket === "all" ? { counts: census.counts, fields: census.fields } : census.rank_buckets[bucket] ?? { counts: census.counts, fields: census.fields };
  const fields: CensusFields = scope.fields;
  const resolved = scope.counts.resolved;

  // Providers in the order of how much of the web leans on them across every role.
  const providers = useMemo(() => {
    const m = new Map<string, { id: string; name: string; total: number }>();
    for (const { field } of FIELDS) {
      if (field === "hosting_region" || field === "tls_issuer" || field === "hosting_provider_without_edge") continue;
      for (const t of census.fields[field]?.top ?? []) {
        if (isNonProvider(t.id) || t.id === "self") continue;
        const cur = m.get(t.id);
        m.set(t.id, { id: t.id, name: cur?.name ?? t.name, total: (cur?.total ?? 0) + t.pct_of_resolved });
      }
    }
    return [...m.values()].sort((a, b) => b.total - a.total);
  }, [census.fields]);
  const [failing, setFailing] = useState<string>(providers[0]?.id ?? "");
  const selectId = useId();
  const failingName = providers.find((p) => p.id === failing)?.name ?? failing;

  const impact = FIELDS.filter((f) => f.field !== "hosting_region" && f.field !== "hosting_provider_without_edge").map(({ field, label, role }) => {
    const hit = fields[field]?.top.find((t) => t.id === failing);
    return { field, label, role, pct: hit?.pct_of_resolved ?? null, count: hit?.count ?? null };
  });

  const scopeLabel = bucket === "all" ? `all ${formatCount(census.counts.total)} domains` : `the ${available.find((b) => b.key === bucket)?.label.toLowerCase()}`;

  return (
    <div className="pb-8">
      <FixtureNote show={fixture} />
      <div role="radiogroup" aria-label="Which sites" className="flex flex-wrap gap-2">
        {available.map((b) => (
          <button key={b.key} type="button" role="radio" aria-checked={bucket === b.key} className="chip" onClick={() => setBucket(b.key)}>
            {b.label}
          </button>
        ))}
        <button type="button" role="radio" aria-checked={bucket === "all"} className="chip" onClick={() => setBucket("all")}>
          All {formatCount(census.counts.total)} domains
        </button>
      </div>

      <Section
        id="if-it-fails"
        title="If this provider fails"
        className="mt-10"
        lead={
          <p>
            The share of {scopeLabel} that rely on one provider, for each role it plays, out of the {formatCount(resolved)} that resolved.
          </p>
        }
      >
        <label htmlFor={selectId} className="text-xs text-ink-soft">
          Provider
        </label>
        <select id={selectId} className="field mt-1.5 block w-full sm:w-80" value={failing} onChange={(e) => setFailing(e.target.value)}>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <ul className="mt-6 grid grid-cols-1 gap-x-6 gap-y-5 sm:grid-cols-2 xl:grid-cols-3">
          {impact.map((r) => (
            <li key={r.field}>
              <p className="text-xs text-ink-soft">{r.label}</p>
              {r.pct != null ? (
                <p className="mt-1 text-2xl font-medium">{pct(r.pct)}</p>
              ) : (
                <p className="mt-1.5 text-lg text-ink-soft">Not listed</p>
              )}
              <p className="mt-1 text-xs text-ink-soft">
                {r.pct != null && r.count != null
                  ? `${formatCount(r.count)} sites where ${failingName} ${r.role}.`
                  : "Not among the leading providers for this role."}
              </p>
            </li>
          ))}
        </ul>
        <p className="prose-measure mt-6 text-xs text-ink-soft">
          These shares overlap: one site can rely on the same provider for its name, its edge and its hosting.
        </p>
      </Section>

      <SingleProvider census={census} />

      <Section
        id="shares"
        title="Shares by role"
        lead={
          <p>
            The leading providers in each role, as a share of the sites where that role could be decided, with the number of sites. Grey
            rows are not a single provider.
          </p>
        }
      >
        <div className="grid grid-cols-1 gap-x-10 gap-y-10 md:grid-cols-2">
          {FIELDS.map(({ field, label }) => {
            const f = fields[field];
            if (!f) return null;
            return (
              <div key={field}>
                <div className="flex items-baseline justify-between gap-4">
                  <h3 className="text-base font-medium">{label}</h3>
                  <p className="text-xs whitespace-nowrap text-ink-soft">Concentration {hhiText(f.hhi)}</p>
                </div>
                <p className="mt-0.5 mb-3 text-xs text-ink-soft">
                  Decided for {formatCount(f.determinable)} of {formatCount(f.resolved)} resolved sites, {formatCount(f.distinct)} distinct
                </p>
                <ShareBars
                  label={label}
                  rows={toRows(field, f.top, "determinable").slice(0, 8)}
                  format={(r) => formatShare(r.share, r.share < 0.0995 ? 1 : 0)}
                />
              </div>
            );
          })}
        </div>
        <p className="prose-measure mt-8 text-xs text-ink-soft">
          Concentration is the Herfindahl-Hirschman index of each role: the sum of squared percentage shares, from near 0 when sites spread
          across many providers to 10,000 when one provider serves them all.
          {census.population_note ? ` ${census.population_note}` : ""}
        </p>
      </Section>

      {census.top_asns?.length ? (
        <Section id="networks" title="Networks that answer" lead={<p>The networks the first address of each resolved site belongs to.</p>}>
          <ShareBars
            label="Networks"
            rows={census.top_asns.slice(0, 10).map((a) => ({ id: String(a.asn), name: `AS${a.asn} ${a.org}`, share: a.pct_of_resolved / 100, count: a.count }))}
            format={(r) => formatShare(r.share, r.share < 0.1 ? 1 : 0)}
          />
        </Section>
      ) : null}

      <Section id="method" title="How the census is measured">
        <div className="prose-measure space-y-3 text-ink-soft">
          <p>
            For each domain, the census resolves its name servers, follows its web address to the network that answers, reads the issuer
            of its certificate and looks up its mail exchangers. Each answer is mapped to a provider through published IP ranges, name
            servers and response headers. A role that cannot be decided is left out of that role&apos;s count rather than guessed.
          </p>
          <p className="text-ink">{census.vantage.description}</p>
          {census.vantage.dns_resolvers?.length ? (
            <p>
              Resolvers: <span className="font-mono text-[0.8rem] text-ink">{census.vantage.dns_resolvers.join(", ")}</span>.
              {census.vantage.dns_note ? ` ${census.vantage.dns_note}` : ""}
            </p>
          ) : null}
          {census.vantage.http_note ? <p>{census.vantage.http_note}</p> : null}
          {census.vantage.user_agent ? (
            <p>
              Requests identify themselves as <span className="font-mono text-[0.8rem] break-all text-ink">{census.vantage.user_agent}</span>.
            </p>
          ) : null}
        </div>
        {census.dataset_dates && Object.keys(census.dataset_dates).length ? (
          <details className="mt-6 text-xs">
            <summary className="inline-flex min-h-8 cursor-pointer items-center text-ink-soft hover:text-ink">Published IP ranges used, by date</summary>
            <table className="table mt-2 max-w-md">
              <tbody>
                {Object.entries(census.dataset_dates).map(([k, v]) => (
                  <tr key={k}>
                    <td className="text-ink">{k}</td>
                    <td className="font-mono text-[0.72rem] text-ink-soft">{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        ) : null}
      </Section>
    </div>
  );
}

function SingleProvider({ census }: { census: CensusSummary }) {
  const entries = Object.entries(census.single_provider ?? {});
  if (!entries.length) return null;
  return (
    <Section
      id="single-provider"
      title="One provider, several roles"
      lead={<p>How often a site puts more than one role with the same provider, across all {formatCount(census.counts.resolved)} resolved domains.</p>}
    >
      <ul className="grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2">
        {entries.map(([key, v]) => (
          <li key={key}>
            <p className="text-xs text-ink-soft">{SINGLE_LABEL[key] ?? key.replace(/_/g, " ")}</p>
            <p className="mt-1 text-2xl font-medium">{pct(v.pct_of_comparable ?? v.pct_of_resolved)}</p>
            <p className="mt-1 text-xs text-ink-soft">
              {v.comparable != null
                ? `${formatCount(v.count)} of the ${formatCount(v.comparable)} sites where ${key.split("_and_").length > 2 || key === "dns_edge_and_hosting" ? "all three" : "both"} could be decided.`
                : `${formatCount(v.count)} sites, ${pct(v.pct_of_resolved)} of resolved domains.`}
              {v.by_provider?.length ? ` Most often ${v.by_provider.slice(0, 3).map((b) => b.name).join(", ")}.` : ""}
            </p>
          </li>
        ))}
      </ul>
    </Section>
  );
}
