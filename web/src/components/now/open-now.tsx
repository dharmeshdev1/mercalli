"use client";

import Link from "next/link";
import { useState } from "react";
import { IncidentCard, incidentHref } from "@/components/incident/incident-card";
import { useOverview } from "@/components/overview-context";
import { ImpactGlyph } from "@/components/ui/glyph";
import { Section } from "@/components/ui/section";
import { Elapsed, Time } from "@/components/ui/time";
import type { IncidentSummary, Meta } from "@/lib/api/types";
import { useDoc } from "@/lib/api/use-doc";
import { plural } from "@/lib/format";
import { impactLabel } from "@/lib/severity";

const FIRST_CARDS = 6;

function servicesIn(list: readonly IncidentSummary[]): number {
  return new Set(list.map((i) => i.service_id)).size;
}

/** What the open list says about forecasts, from the published model state. */
function forecastStatus(active: readonly IncidentSummary[], meta: Meta | null): { lead: string | null; perCard: string | undefined } {
  const scored = active.filter((i) => i.forecast).length;
  if (scored === active.length) return { lead: null, perCard: undefined };
  if (scored === 0) {
    if (meta && meta.models == null) {
      return { lead: "Forecasts of how long each incident will last appear here once the recovery model is published.", perCard: undefined };
    }
    return { lead: "None of these incidents has a forecast yet.", perCard: undefined };
  }
  return { lead: null, perCard: "No forecast: the model could not score this incident." };
}

export function OpenNow() {
  const ov = useOverview();
  const meta = useDoc<Meta>("meta.json");
  const [showAll, setShowAll] = useState(false);
  const data = ov.data;

  if (!data) {
    return (
      <Section id="open-now" title="Open now" lead={ov.status === "loading" ? "Reading the latest record." : "The latest record could not be loaded."}>
        <div className="h-48" />
      </Section>
    );
  }

  const active = data.active;
  const fs = forecastStatus(active, meta.data);
  const shown = showAll ? active : active.slice(0, FIRST_CARDS);
  const lead =
    active.length === 0 ? (
      <p>No incidents are open right now, apart from the long-running ones and notices listed below.</p>
    ) : (
      <p>
        {plural(active.length, "active incident")} across {plural(servicesIn(active), "service")}, worst first.
      </p>
    );

  return (
    <Section id="open-now" title="Open now" lead={lead} aside={fs.lead}>
      {active.length === 0 ? (
        <EmptyNow recent={data.recent[0] ?? null} />
      ) : (
        <>
          <ul className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            {shown.map((inc) => (
              <li key={inc.id} className="min-w-0">
                <IncidentCard incident={inc} isNew={ov.arrivals.has(inc.id)} forecastNote={fs.perCard} />
              </li>
            ))}
          </ul>
          {active.length > FIRST_CARDS ? (
            <button type="button" className="button mt-4" aria-expanded={showAll} onClick={() => setShowAll((v) => !v)}>
              {showAll ? "Show fewer" : `Show all ${active.length} active incidents`}
            </button>
          ) : null}
        </>
      )}
      <div className="mt-10 grid grid-cols-1 gap-x-6 gap-y-8 xl:grid-cols-2">
        <LongRunning list={data.long_running} />
        <Notices list={data.notices} />
      </div>
    </Section>
  );
}

function EmptyNow({ recent }: { recent: IncidentSummary | null }) {
  if (!recent) return <p className="text-ink-soft">No incidents have ended in the last 48 hours either.</p>;
  return (
    <div>
      <p className="mb-3 text-xs text-ink-soft">The most recent incident to end:</p>
      <IncidentCard incident={recent} />
    </div>
  );
}

function CompactRow({ inc, open }: { inc: IncidentSummary; open: boolean }) {
  return (
    <li className="grid grid-cols-[14px_minmax(0,1fr)_auto] items-baseline gap-x-3 border-t border-grid py-2.5 first:border-t-0">
      <span className="translate-y-[2px]">
        <ImpactGlyph impact={inc.impact} size={13} />
      </span>
      <span className="vendor-text min-w-0">
        <Link href={`/s/${inc.service_id}/`} className="quiet-link font-medium text-ink">
          {inc.service_name}
        </Link>{" "}
        <Link href={incidentHref(inc.id)} className="quiet-link text-ink-soft">
          {inc.title}
        </Link>
      </span>
      <span className="text-right text-ink-soft whitespace-nowrap">
        {open ? <Elapsed iso={inc.started_at} /> : <Time iso={inc.started_at} style="date" />}
      </span>
    </li>
  );
}

function LongRunning({ list }: { list: IncidentSummary[] }) {
  if (list.length === 0) return <div />;
  return (
    <details className="group ledger self-start">
      <summary className="flex cursor-pointer list-none items-start justify-between gap-4 px-4 py-3.5 md:px-5 [&::-webkit-details-marker]:hidden">
        <span>
          <span className="font-medium">{plural(list.length, "long-running incident")}</span>
          <span className="mt-0.5 block text-xs text-ink-soft">
            Open for more than 7 days. Usually incidents a vendor has not closed, so they are kept apart from the list above.
          </span>
        </span>
        <span aria-hidden="true" className="mt-1 text-ink-soft transition-transform group-open:rotate-45">
          <svg width="12" height="12" viewBox="0 0 12 12">
            <path d="M6 1v10M1 6h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        </span>
      </summary>
      <ul className="border-t border-rule px-4 pb-2 text-xs md:px-5">
        {list.map((inc) => (
          <CompactRow key={inc.id} inc={inc} open />
        ))}
      </ul>
    </details>
  );
}

function Notices({ list }: { list: IncidentSummary[] }) {
  const [all, setAll] = useState(false);
  if (list.length === 0) return <div />;
  const shown = all ? list : list.slice(0, 4);
  return (
    <div className="self-start px-1">
      <p className="font-medium">{plural(list.length, "notice")}</p>
      <p className="mt-0.5 text-xs text-ink-soft">Advisories and maintenance that vendors list with no reported impact.</p>
      <ul className="mt-2 text-xs">
        {shown.map((inc) => (
          <CompactRow key={inc.id} inc={inc} open={false} />
        ))}
      </ul>
      {list.length > shown.length || all ? (
        <button type="button" className="mt-1 min-h-8 text-xs text-accent hover:underline" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${list.length} notices`}
        </button>
      ) : null}
      <p className="sr-only">Impact levels: {Array.from(new Set(list.map((i) => impactLabel(i.impact)))).join(", ")}.</p>
    </div>
  );
}
