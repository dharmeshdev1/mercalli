"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { BoardFilters, matches, type FilterState } from "@/components/board/filters";
import { useOverview } from "@/components/overview-context";
import { HealthGlyph } from "@/components/ui/glyph";
import { PageHeader } from "@/components/ui/page-header";
import { Pending } from "@/components/ui/section";
import { Ago } from "@/components/ui/time";
import type { ServiceEntry, ServicesDoc } from "@/lib/api/types";
import { useDoc } from "@/lib/api/use-doc";
import { useMediaQuery } from "@/lib/prefs";
import { formatCount, formatDuration, plural } from "@/lib/format";
import { categoryLabel, healthLabel, healthRank } from "@/lib/severity";

type SortKey = "problems" | "name" | "incidents" | "median";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "problems", label: "Health" },
  { key: "name", label: "Name" },
  { key: "incidents", label: "Incidents in 90 days" },
  { key: "median", label: "Median incident length" },
];

interface Row extends ServiceEntry {
  open: number;
}

function sorter(key: SortKey): (a: Row, b: Row) => number {
  switch (key) {
    case "name":
      return (a, b) => a.name.localeCompare(b.name);
    case "incidents":
      return (a, b) => b.stats_90d.incidents - a.stats_90d.incidents || a.name.localeCompare(b.name);
    case "median":
      return (a, b) => (b.stats_90d.median_min ?? -1) - (a.stats_90d.median_min ?? -1) || a.name.localeCompare(b.name);
    default:
      return (a, b) => healthRank(b.health) - healthRank(a.health) || b.open - a.open || a.name.localeCompare(b.name);
  }
}

function host(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

export function ServicesIndex() {
  const doc = useDoc<ServicesDoc>("services.json");
  const ov = useOverview();
  const [filter, setFilter] = useState<FilterState>({ query: "", category: null });
  const [sort, setSort] = useState<SortKey>("problems");
  // Hundreds of rows: render the table or the cards, not both.
  const wide = useMediaQuery("(min-width: 48rem)");

  const rows = useMemo<Row[]>(() => {
    const open = new Map((ov.data?.services ?? []).map((s) => [s.id, s.open]));
    return (doc.data?.services ?? []).map((s) => ({ ...s, open: open.get(s.id) ?? 0 }));
  }, [doc.data, ov.data]);
  const shown = useMemo(() => rows.filter((r) => matches(r, filter)).sort(sorter(sort)), [rows, filter, sort]);
  const withIncidents = rows.filter((r) => r.stats_90d.incidents > 0).length;

  return (
    <>
      <PageHeader
        title="Services"
        lead={
          doc.data ? (
            <p>
              {plural(rows.length, "status page")} read every few minutes. {formatCount(withIncidents)} of them posted at least one
              incident in the last 90 days.
            </p>
          ) : (
            <p>Every status page Mercalli reads, with its health now and its incidents over the last 90 days.</p>
          )
        }
        meta={doc.data ? <>Generated <Ago iso={doc.data.generated_at} /></> : null}
      />
      {doc.status === "loading" ? (
        <div className="h-96" aria-busy="true" />
      ) : !doc.data ? (
        <Pending title="The service list could not be loaded.">The API did not answer. Try again in a minute.</Pending>
      ) : (
        <div className="pb-20">
          <div className="flex flex-col gap-4">
            <BoardFilters items={rows} state={filter} onChange={setFilter} shown={shown.length} />
            <div className="flex flex-wrap items-center gap-2 text-xs md:hidden" role="group" aria-label="Sort by">
              <span className="text-ink-soft">Sort by</span>
              {SORTS.map((s) => (
                <button key={s.key} type="button" className="chip" aria-pressed={sort === s.key} onClick={() => setSort(s.key)}>
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          {wide ? (
          <div className="mt-6">
            <table className="table">
              <caption className="sr-only">Services, sorted by {SORTS.find((s) => s.key === sort)?.label.toLowerCase()}</caption>
              <thead>
                <tr>
                  <SortHeader label="Service" k="name" sort={sort} onSort={setSort} />
                  <th scope="col">Category</th>
                  <SortHeader label="Health" k="problems" sort={sort} onSort={setSort} />
                  <th scope="col" className="text-right">
                    Open
                  </th>
                  <SortHeader label="Incidents in 90 days" k="incidents" sort={sort} onSort={setSort} align="right" />
                  <SortHeader label="Median length" k="median" sort={sort} onSort={setSort} align="right" />
                  <th scope="col" className="text-right">
                    Time in incidents
                  </th>
                  <th scope="col">Status page</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.id} className="hover:bg-hover">
                    <td className="min-w-0">
                      <span className="flex items-center gap-2.5">
                        <HealthGlyph health={r.health} size={13} />
                        <Link href={`/s/${r.id}/`} className="quiet-link font-medium text-ink">
                          {r.name}
                        </Link>
                      </span>
                    </td>
                    <td className="text-ink-soft">{categoryLabel(r.category)}</td>
                    <td className={r.health === "operational" ? "text-ink-soft" : "text-ink"}>{healthLabel(r.health)}</td>
                    <td className="text-right">{r.open || <span className="text-ink-soft">0</span>}</td>
                    <td className="text-right">{formatCount(r.stats_90d.incidents)}</td>
                    <td className="text-right">{r.stats_90d.median_min != null ? formatDuration(r.stats_90d.median_min) : <span className="text-ink-soft">none</span>}</td>
                    <td className="text-right text-ink-soft">{r.stats_90d.minutes > 0 ? formatDuration(r.stats_90d.minutes) : "none"}</td>
                    <td>
                      <a href={r.status_url} className="quiet-link text-ink-soft hover:text-ink" rel="noreferrer">
                        {host(r.status_url)}
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          ) : (
          <ul className="mt-6 grid grid-cols-1 gap-2">
            {shown.map((r) => (
              <li key={r.id} className="ledger px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="flex min-w-0 items-center gap-2.5">
                    <HealthGlyph health={r.health} size={14} />
                    <Link href={`/s/${r.id}/`} className="quiet-link truncate font-medium text-ink">
                      {r.name}
                    </Link>
                  </span>
                  <span className={`flex-none text-xs ${r.health === "operational" ? "text-ink-soft" : "text-ink"}`}>{healthLabel(r.health)}</span>
                </div>
                <p className="mt-1 text-xs text-ink-soft">
                  {categoryLabel(r.category)}. {plural(r.stats_90d.incidents, "incident")} in 90 days
                  {r.stats_90d.median_min != null ? `, median ${formatDuration(r.stats_90d.median_min)}` : ""}.
                  {r.open ? ` ${plural(r.open, "open incident")} now.` : ""}
                </p>
              </li>
            ))}
          </ul>
          )}
          {shown.length === 0 ? <p className="mt-6 text-ink-soft">No service matches that search.</p> : null}
        </div>
      )}
    </>
  );
}

function SortHeader({ label, k, sort, onSort, align }: { label: string; k: SortKey; sort: SortKey; onSort: (k: SortKey) => void; align?: "right" }) {
  const active = sort === k;
  return (
    <th scope="col" aria-sort={active ? (k === "name" ? "ascending" : "descending") : "none"} className={align === "right" ? "text-right" : undefined}>
      <button
        type="button"
        onClick={() => onSort(k)}
        className={`inline-flex items-center gap-1 font-medium ${active ? "text-ink" : "text-ink-soft hover:text-ink"}`}
      >
        {label}
        <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true" className={active ? "opacity-100" : "opacity-0"}>
          <path d={k === "name" ? "M1 5.5L4 2.5l3 3" : "M1 2.5L4 5.5l3-3"} fill="none" stroke="currentColor" strokeWidth="1.3" />
        </svg>
      </button>
    </th>
  );
}
