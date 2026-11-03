"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { IncidentStrip } from "@/components/charts/incident-strip";
import { IncidentCard, incidentHref } from "@/components/incident/incident-card";
import { useOverview } from "@/components/overview-context";
import { HealthGlyph, HealthTag, ImpactGlyph } from "@/components/ui/glyph";
import { Pending, Section } from "@/components/ui/section";
import { Ago, Time } from "@/components/ui/time";
import { servicePath } from "@/lib/api/client";
import type { Component, EdgeKind, GraphEdge, IncidentSummary, ServiceDoc } from "@/lib/api/types";
import { useDoc } from "@/lib/api/use-doc";
import { useNow } from "@/lib/clock";
import { DAY, formatCount, formatDuration, MINUTE, parseTime, plural } from "@/lib/format";
import { categoryLabel, healthLabel, healthRank, impactLabel } from "@/lib/severity";

/** Incident documents are kept for 14 days after an incident ends (docs/api.md). */
const DOC_KEPT_MS = 14 * DAY;

const KIND_LABEL: Record<EdgeKind, string> = {
  dns: "DNS",
  edge: "Edge network",
  hosting: "Hosting",
  named: "Named in incident reports",
  cofailure: "Fails together",
};

function host(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

export function ServiceView({ id, name, category, statusUrl }: { id: string; name: string; category: string | null; statusUrl: string | null }) {
  const path = servicePath(id);
  const doc = useDoc<ServiceDoc>(path, { pollMs: 120_000 });
  const d = doc.data;
  const svc = d?.service;
  const open = useMemo(() => (d ? d.incidents.filter((i) => i.resolved_at == null) : []), [d]);
  const past = useMemo(
    () => (d ? d.incidents.filter((i) => i.resolved_at != null).sort((a, b) => (parseTime(b.started_at) ?? 0) - (parseTime(a.started_at) ?? 0)) : []),
    [d],
  );
  const longest = useMemo(() => past.reduce<number | null>((m, i) => (i.duration_min != null && i.duration_min >= 0 && (m == null || i.duration_min > m) ? i.duration_min : m), null), [past]);

  return (
    <>
      <header className="pt-10 pb-10 md:pt-14 md:pb-12">
        <h1 className="display text-3xl font-normal md:text-4xl">{svc?.name ?? name}</h1>
        <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2">
          {svc ? <HealthTag health={svc.health} className="text-base font-medium" /> : <span className="h-6" />}
          {svc?.description ? <span className="text-ink-soft">{svc.description}</span> : null}
        </div>
        <p className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-soft">
          {(svc?.category ?? category) ? <span>{categoryLabel(svc?.category ?? category ?? "")}</span> : null}
          {(svc?.status_url ?? statusUrl) ? (
            <a href={svc?.status_url ?? statusUrl ?? undefined} rel="noreferrer">
              Status page at {host(svc?.status_url ?? statusUrl ?? "")}
            </a>
          ) : null}
          {svc?.last_polled_at ? (
            <span>
              Last read <Ago iso={svc.last_polled_at} />
            </span>
          ) : null}
          {svc && svc.domains.length ? <span>Domains: {svc.domains.join(", ")}</span> : null}
        </p>
        {svc?.last_error ? (
          <p className="ledger mt-4 px-4 py-3 text-xs" role="status">
            The status page could not be read on the last attempt: {svc.last_error}
          </p>
        ) : null}
      </header>

      {doc.status === "loading" ? (
        <div className="h-96" aria-busy="true" />
      ) : !d ? (
        <Pending title={doc.status === "missing" ? "This service has no document in the API." : "This service could not be loaded."}>
          {doc.status === "missing" ? "It may have been removed from the catalog." : "The API did not answer. Try again in a minute."}
        </Pending>
      ) : (
        <>
          {doc.error ? (
            <p className="mb-6 text-xs text-ink" role="status">
              The latest refresh failed; showing data generated <Ago iso={d.generated_at} />.
            </p>
          ) : null}
          <Section
            id="open"
            title="Open now"
            lead={<p>{open.length ? `${plural(open.length, "open incident")}, worst first.` : "No open incidents."}</p>}
          >
            {open.length ? (
              <ul className="grid grid-cols-1 gap-3">
                {open.map((inc) => (
                  <li key={inc.id}>
                    <IncidentCard incident={inc} showService={false} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-ink-soft">
                {svc && svc.health !== "operational" ? `The status page reports ${healthLabel(svc.health).toLowerCase()} without an open incident.` : "Every component reports operational."}
              </p>
            )}
          </Section>

          <Section
            id="history"
            title="Last 90 days"
            lead={<p>One bar per incident. Length is how long it lasted, color is the impact the vendor gave it.</p>}
          >
            <Reliability doc={d} longest={longest} />
            <div className="mt-8">
              <IncidentStrip incidents={d.incidents} />
            </div>
            <History incidents={past} />
          </Section>

          <Section id="components" title="Components" lead={<ComponentsLead components={d.components} />}>
            <Components components={d.components} />
          </Section>

          <Section
            id="dependencies"
            title="Dependencies"
            lead={<p>Found in the census, in incident reports that name a provider, and in failures that happen together more often than chance.</p>}
          >
            <div className="grid grid-cols-1 gap-x-8 gap-y-10 xl:grid-cols-2">
              <Edges title="Depends on" edges={d.depends_on} side="target" />
              <Edges title="Depended on by" edges={d.dependents} side="source" />
            </div>
          </Section>
        </>
      )}
    </>
  );
}

function Reliability({ doc, longest }: { doc: ServiceDoc; longest: number | null }) {
  const s = doc.stats_90d;
  const items = [
    { label: "Incidents", value: s ? formatCount(s.incidents) : "0" },
    { label: "Median length", value: s?.median_min != null ? formatDuration(s.median_min) : "none" },
    { label: "Longest", value: longest != null ? formatDuration(longest) : "none" },
    { label: "Time in incidents", value: s && s.minutes > 0 ? formatDuration(s.minutes) : "none" },
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-4">
      {items.map((it) => (
        <div key={it.label}>
          <dt className="text-xs text-ink-soft">{it.label}</dt>
          <dd className="mt-1 text-xl font-medium">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function History({ incidents }: { incidents: IncidentSummary[] }) {
  const now = useNow(MINUTE);
  const [all, setAll] = useState(false);
  if (!incidents.length) return <p className="mt-8 text-ink-soft">No incidents ended in the last 90 days.</p>;
  const shown = all ? incidents : incidents.slice(0, 8);
  return (
    <div className="mt-10">
      <h3 className="text-base font-medium">Incidents that ended</h3>
      <ul className="mt-2 text-xs">
        {shown.map((i) => {
          const ended = parseTime(i.resolved_at) ?? 0;
          const kept = now ? now - ended < DOC_KEPT_MS : true;
          return (
            <li key={i.id} className="grid grid-cols-[14px_minmax(0,1fr)_auto] items-baseline gap-x-3 border-t border-grid py-2.5 first:border-t-0">
              <span className="translate-y-[2px]">
                <ImpactGlyph impact={i.impact} size={13} />
              </span>
              <span className="vendor-text min-w-0">
                {kept ? (
                  <Link href={incidentHref(i.id)} className="quiet-link text-ink">
                    {i.title}
                  </Link>
                ) : i.url ? (
                  <a href={i.url} className="quiet-link text-ink" rel="noreferrer">
                    {i.title}
                  </a>
                ) : (
                  <span className="text-ink">{i.title}</span>
                )}
                <span className="block text-ink-soft">
                  <Time iso={i.started_at} style="date" />, lasted {formatDuration(i.duration_min)}
                  {i.resolved_inferred ? ", end inferred" : ""}
                </span>
              </span>
              <span className="text-ink-soft">{impactLabel(i.impact)}</span>
            </li>
          );
        })}
      </ul>
      {incidents.length > 8 ? (
        <button type="button" className="mt-1 min-h-8 text-xs text-accent hover:underline" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${incidents.length}`}
        </button>
      ) : null}
    </div>
  );
}

function ComponentsLead({ components }: { components: Component[] }) {
  if (!components.length) return <p>The status page lists no components.</p>;
  const counts = new Map<string, number>();
  for (const c of components) counts.set(c.status, (counts.get(c.status) ?? 0) + 1);
  const parts = [...counts.entries()].sort((a, b) => healthRank(b[0]) - healthRank(a[0])).map(([h, n]) => `${formatCount(n)} ${healthLabel(h).toLowerCase()}`);
  return (
    <p>
      {plural(components.length, "component")} on the status page: {parts.join(", ")}.
    </p>
  );
}

function Components({ components }: { components: Component[] }) {
  const [allProblems, setAllProblems] = useState(false);
  const problems = components.filter((c) => c.status !== "operational").sort((a, b) => healthRank(b.status) - healthRank(a.status));
  const shownProblems = allProblems ? problems : problems.slice(0, 12);
  const byGroup = new Map<string, Component[]>();
  for (const c of components) {
    const g = c.group ?? "Ungrouped";
    const list = byGroup.get(g);
    if (list) list.push(c);
    else byGroup.set(g, [c]);
  }
  const groups = [...byGroup.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  if (!components.length) return <p className="text-ink-soft">Nothing to show.</p>;
  return (
    <div>
      {problems.length ? (
        <>
          <ul className="grid grid-cols-1 gap-x-8 text-xs sm:grid-cols-2">
            {shownProblems.map((c) => (
              <li key={c.id} className="flex items-start gap-2.5 border-t border-grid py-2">
                <HealthGlyph health={c.status} size={13} className="mt-0.5" />
                <span className="vendor-text min-w-0">
                  <span className="block text-ink">{c.name}</span>
                  <span className="block text-ink-soft">
                    {healthLabel(c.status)}
                    {c.group ? `, in ${c.group}` : ""}
                  </span>
                </span>
              </li>
            ))}
          </ul>
          {problems.length > 12 ? (
            <button type="button" className="mt-1 min-h-8 text-xs text-accent hover:underline" aria-expanded={allProblems} onClick={() => setAllProblems((v) => !v)}>
              {allProblems ? "Show fewer" : `Show all ${formatCount(problems.length)} with problems`}
            </button>
          ) : null}
        </>
      ) : (
        <p className="text-ink-soft">Every component reports operational.</p>
      )}
      <details className="mt-5">
        <summary className="inline-flex min-h-8 cursor-pointer items-center text-xs text-ink-soft hover:text-ink">
          Show all {formatCount(components.length)} components
        </summary>
        <div className="mt-4 columns-1 gap-8 text-xs sm:columns-2 xl:columns-3">
          {groups.map(([g, list]) => (
            <div key={g} className="mb-5 break-inside-avoid">
              <p className="mb-1.5 font-medium text-ink">{g}</p>
              <ul className="space-y-1">
                {list.map((c) => (
                  <li key={c.id} className="flex items-start gap-2 text-ink-soft">
                    <HealthGlyph health={c.status} size={11} className="mt-[3px]" />
                    <span>
                      {c.name}
                      <span className="sr-only">, {healthLabel(c.status)}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

function Edges({ title, edges, side }: { title: string; edges: GraphEdge[]; side: "source" | "target" }) {
  const { data } = useOverview();
  const names = useMemo(() => new Map((data?.services ?? []).map((s) => [s.id, s])), [data]);
  const sorted = edges.slice().sort((a, b) => b.weight - a.weight);
  return (
    <div>
      <h3 className="text-base font-medium">
        {title} <span className="font-normal text-ink-soft">{edges.length}</span>
      </h3>
      {sorted.length === 0 ? (
        <p className="mt-2 text-ink-soft">None recorded yet.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {sorted.map((e) => {
            const other = side === "target" ? e.target : e.source;
            const watched = names.get(other);
            return (
              <li key={`${e.source}>${e.target}`} className="ledger px-3.5 py-2.5 text-xs">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                  {watched ? (
                    <Link href={`/s/${other}/`} className="quiet-link inline-flex items-center gap-2 font-medium text-ink">
                      <HealthGlyph health={watched.health} size={12} />
                      {watched.name}
                    </Link>
                  ) : (
                    <span className="font-medium text-ink">{other}</span>
                  )}
                  <span className="text-ink-soft">{e.kinds.map((k) => KIND_LABEL[k] ?? k).join(", ")}</span>
                </div>
                {e.evidence.length ? (
                  <p className="mt-1.5 font-mono text-[0.7rem] break-words text-ink-soft">{e.evidence.join("  ")}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
