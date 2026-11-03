"use client";

import Link from "next/link";
import { AnimatePresence, LayoutGroup, LazyMotion, m } from "motion/react";
import { useMemo, useState } from "react";
import { useOverview } from "@/components/overview-context";
import { HealthGlyph } from "@/components/ui/glyph";
import { Section } from "@/components/ui/section";
import type { BoardService, Health } from "@/lib/api/types";
import { plural } from "@/lib/format";
import { useReducedMotionPref } from "@/lib/prefs";
import { healthLabel, healthRank } from "@/lib/severity";
import { BoardFilters, matches, type FilterState } from "./filters";

const loadFeatures = () => import("./motion-features").then((mod) => mod.default);

const HEALTH_ORDER: Health[] = ["major_outage", "partial_outage", "degraded", "unknown", "maintenance", "operational"];

export function problemsFirst(a: BoardService, b: BoardService): number {
  return healthRank(b.health) - healthRank(a.health) || b.open - a.open || a.name.localeCompare(b.name);
}

export function healthSummary(counts: Partial<Record<Health, number>>): string {
  return HEALTH_ORDER.filter((h) => counts[h])
    .map((h) => `${counts[h]} ${healthLabel(h).toLowerCase()}`)
    .join(", ");
}

/** The whole board as a dense grid of tiles, problems first, filterable, animated with Motion layout. */
export function ServiceBoard() {
  const { data } = useOverview();
  const reduced = useReducedMotionPref();
  const [filter, setFilter] = useState<FilterState>({ query: "", category: null });
  const [allHealthy, setAllHealthy] = useState(false);
  const services = useMemo(() => (data ? data.services.slice().sort(problemsFirst) : []), [data]);
  const matching = useMemo(() => services.filter((s) => matches(s, filter)), [services, filter]);
  const isProblem = (s: BoardService) => s.health !== "operational" || s.open > 0;
  const filtering = filter.query.trim() !== "" || filter.category != null;
  const healthy = matching.filter((s) => !isProblem(s)).length;
  // Every match shows while filtering; otherwise the healthy majority waits behind one button.
  const shown = filtering || allHealthy ? matching : matching.filter(isProblem);

  return (
    <Section
      id="board"
      title="Services at a glance"
      lead={
        data ? (
          <p>
            {plural(data.counts.services, "service")} watched: {healthSummary(data.counts)}.
          </p>
        ) : (
          <p>Every service Mercalli watches, problems first.</p>
        )
      }
      aside={<Link href="/services/">Compare reliability over 90 days</Link>}
    >
      {data ? (
        <>
          <BoardFilters items={services} state={filter} onChange={setFilter} shown={matching.length} />
          <LazyMotion features={loadFeatures} strict>
            <LayoutGroup>
              <ul className="mt-6 grid grid-cols-2 gap-1 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5">
                <AnimatePresence initial={false} mode="popLayout">
                  {shown.map((s) => {
                    const problem = s.health !== "operational" || s.open > 0;
                    return (
                      <m.li
                        key={s.id}
                        layout={reduced ? false : "position"}
                        initial={reduced ? false : { opacity: 0, scale: 0.96 }}
                        animate={{ opacity: 1, scale: 1 }}
                        exit={reduced ? undefined : { opacity: 0, scale: 0.96 }}
                        transition={{ duration: reduced ? 0 : 0.32, ease: [0.2, 0, 0, 1] }}
                        className="min-w-0"
                      >
                        <Link
                          href={`/s/${s.id}/`}
                          className={`quiet-link flex min-h-9 items-center gap-2 rounded-sm px-2.5 py-1 text-xs transition-colors hover:bg-hover ${
                            problem ? "border border-rule bg-paper-raised text-ink" : "border border-transparent text-ink-soft"
                          }`}
                          title={`${s.name}: ${healthLabel(s.health)}${s.open ? `, ${plural(s.open, "open incident")}` : ""}`}
                        >
                          <HealthGlyph health={s.health} size={13} />
                          <span className="truncate">{s.name}</span>
                          {s.open > 0 ? <span className="ml-auto flex-none text-ink-soft">{s.open}</span> : null}
                          <span className="sr-only">, {healthLabel(s.health)}</span>
                        </Link>
                      </m.li>
                    );
                  })}
                </AnimatePresence>
              </ul>
            </LayoutGroup>
          </LazyMotion>
          {matching.length === 0 ? <p className="mt-6 text-ink-soft">No service matches that search.</p> : null}
          {!filtering && healthy > 0 ? (
            <button type="button" className="button mt-4" aria-expanded={allHealthy} onClick={() => setAllHealthy((v) => !v)}>
              {allHealthy ? "Show only services with problems" : `Show the ${healthy.toLocaleString("en-US")} operational services too`}
            </button>
          ) : null}
          <p className="mt-4 text-xs text-ink-soft">The number on a tile is its open incidents.</p>
        </>
      ) : (
        <div className="h-64" aria-busy="true" />
      )}
    </Section>
  );
}
