"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { ForecastHistory } from "@/components/charts/forecast-history";
import { ShareBars } from "@/components/charts/share-bars";
import { useOverview } from "@/components/overview-context";
import { ImpactTag } from "@/components/ui/glyph";
import { Pending, Section } from "@/components/ui/section";
import { Ago, Elapsed, Time } from "@/components/ui/time";
import { incidentPath } from "@/lib/api/client";
import type { IncidentDoc, IncidentUpdate, Meta } from "@/lib/api/types";
import { useDoc } from "@/lib/api/use-doc";
import { useNow } from "@/lib/clock";
import { formatClockNear, formatDuration, formatShare, formatTimeRange, MINUTE, parseTime, plural, possessive } from "@/lib/format";
import { forecastTimes } from "@/lib/forecast";
import { useZone } from "@/lib/prefs";
import { categoryLabel, statusLabel } from "@/lib/severity";
import { ForecastBar } from "./forecast-bar";
import { cleanBody } from "./latest-update";

export function IncidentView() {
  const params = useSearchParams();
  const id = params.get("id") ?? "";
  const path = incidentPath(id);
  const doc = useDoc<IncidentDoc>(path, { pollMs: 60_000 });
  const serviceId = id.includes(":") ? id.slice(0, id.indexOf(":")) : null;

  if (!path) {
    return (
      <div className="py-16">
        <Pending title="That link does not name an incident.">
          Incident links look like /incident/?id=service:incident. Find one from the <Link href="/">open incidents</Link> or a{" "}
          <Link href="/services/">service page</Link>.
        </Pending>
      </div>
    );
  }
  if (doc.status === "loading") return <div className="h-[60vh]" aria-busy="true" />;
  if (!doc.data) {
    return (
      <div className="py-16">
        <Pending title={doc.status === "missing" ? "This incident is no longer published." : "This incident could not be loaded."}>
          {doc.status === "missing" ? (
            <>
              Incident records are kept for 14 days after an incident ends.{" "}
              {serviceId ? (
                <>
                  The <Link href={`/s/${serviceId}/`}>service page</Link> still lists it with its timing.
                </>
              ) : null}
            </>
          ) : (
            "The API did not answer. This page will try again in a minute."
          )}
        </Pending>
      </div>
    );
  }
  return <IncidentBody inc={doc.data} stale={doc.error != null} />;
}

function IncidentBody({ inc, stale }: { inc: IncidentDoc; stale: boolean }) {
  const { data: overview } = useOverview();
  const names = new Map((overview?.services ?? []).map((s) => [s.id, s.name]));
  const now = useNow(MINUTE);
  const [zone] = useZone();
  const open = inc.resolved_at == null;
  const actual = !open && inc.duration_min != null && inc.duration_min >= 0 ? inc.duration_min : null;
  const firstSeen = parseTime(inc.first_seen_at);
  const started = parseTime(inc.started_at);
  const late = firstSeen != null && started != null && firstSeen - started > 15 * MINUTE;
  const times = inc.forecast && now ? forecastTimes(inc.started_at, inc.forecast, now) : null;

  return (
    <>
      <header className="pt-10 pb-10 md:pt-14 md:pb-12">
        <h1 className="display vendor-text max-w-[28ch] text-2xl font-normal md:text-3xl">{inc.title}</h1>
        <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2">
          <span>
            <Link href={`/s/${inc.service_id}/`} className="font-medium">
              {inc.service_name}
            </Link>
            <span className="ml-2 text-ink-soft">{categoryLabel(inc.category)}</span>
          </span>
          <ImpactTag impact={inc.impact} className="font-medium" />
          <span>{statusLabel(inc.status)}</span>
          <span className="text-ink-soft">
            {open ? (
              <>
                Open for <Elapsed iso={inc.started_at} className="text-ink" />
              </>
            ) : (
              <>Lasted {formatDuration(actual)}</>
            )}
          </span>
        </div>
        <dl className="mt-6 grid max-w-3xl grid-cols-2 gap-x-6 gap-y-4 text-xs sm:grid-cols-4">
          <div>
            <dt className="text-ink-soft">Started</dt>
            <dd className="mt-0.5 text-ink">
              <Time iso={inc.started_at} style="datetime" />
            </dd>
          </div>
          <div>
            <dt className="text-ink-soft">First seen by Mercalli</dt>
            <dd className="mt-0.5 text-ink">{inc.first_seen_at ? <Time iso={inc.first_seen_at} style="datetime" /> : "Unknown"}</dd>
          </div>
          <div>
            <dt className="text-ink-soft">{open ? "Last update" : inc.resolved_inferred ? "Ended, inferred" : "Resolved"}</dt>
            <dd className="mt-0.5 text-ink">
              {open ? inc.latest ? <Ago iso={inc.latest.at} /> : "None" : <Time iso={inc.resolved_at} style="datetime" />}
            </dd>
          </div>
          <div>
            <dt className="text-ink-soft">Vendor updates</dt>
            <dd className="mt-0.5 text-ink">{inc.updates.length || inc.updates_count}</dd>
          </div>
        </dl>
        {late ? (
          <p className="prose-measure mt-4 text-xs text-ink-soft">
            The posted start is earlier than when Mercalli first saw this incident. That happens when a vendor backdates the start, or when the
            incident began before Mercalli was watching.
          </p>
        ) : null}
        {inc.resolved_inferred ? (
          <p className="prose-measure mt-2 text-xs text-ink-soft">
            The vendor removed this incident without a resolution time; the end shown is when Mercalli noticed.
          </p>
        ) : null}
        {inc.url ? (
          <p className="mt-4 text-xs">
            <a href={inc.url} rel="noreferrer">
              Read it on {possessive(inc.service_name)} status page
            </a>
          </p>
        ) : null}
        {stale ? (
          <p className="mt-4 text-xs text-ink" role="status">
            The latest refresh failed. What you see may be out of date.
          </p>
        ) : null}
      </header>

      <Section id="forecast" title="Forecast" lead={<p>The time left until the vendor marks it resolved, from the model trained on past incidents.</p>}>
        {open && inc.forecast && times ? (
          <div>
            <p className="display text-3xl font-light md:text-4xl">Likely resolved by {formatClockNear(times.p50, now, zone)}</p>
            <p className="mt-2 text-lg text-ink-soft">80% range: {formatTimeRange(times.p10, times.p90, zone, now)}</p>
            <div className="mt-4 max-w-3xl">
              <ForecastBar startedAt={inc.started_at} forecast={inc.forecast} size="large" />
            </div>
          </div>
        ) : open ? (
          <NoForecast />
        ) : (
          <p className="text-ink-soft">
            Resolved after {formatDuration(actual)}.{" "}
            {inc.forecast_history.length ? "Below, how the forecasts compared with the real end." : "No forecasts were made while it was open."}
          </p>
        )}
        {inc.forecast_history.length ? (
          <div className="mt-10">
            <h3 className="mb-4 text-base font-medium">Forecast history</h3>
            <ForecastHistory points={inc.forecast_history} startedAt={inc.started_at} actualMin={actual} />
          </div>
        ) : null}
      </Section>

      <Section id="updates" title="Updates" lead={<p>{plural(inc.updates.length, "vendor update")}, as posted, with every change of status marked.</p>}>
        <Timeline updates={inc.updates} startedAt={inc.started_at} />
      </Section>

      <Section
        id="blast"
        title="Blast radius"
        lead={<p>Services likely to post an incident of their own because of this one, with the evidence for each.</p>}
      >
        {inc.blast && inc.blast.length ? (
          <ul className="space-y-4">
            {inc.blast.map((b) => (
              <li key={b.service_id} className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]">
                <div>
                  <ShareBars label={b.service_name} rows={[{ id: b.service_id, name: b.service_name, share: b.probability }]} max={1} />
                  <Link href={`/s/${b.service_id}/`} className="text-xs">
                    {b.service_name} service page
                  </Link>
                </div>
                <p className="font-mono text-[0.7rem] text-ink-soft">{b.evidence.join("  ")}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-ink-soft">
            {inc.blast
              ? "No other service is likely enough to post an incident because of this one to list here."
              : "No blast radius: it is only estimated for incidents at providers that other services depend on."}
          </p>
        )}
      </Section>

      <Section id="similar" title="Similar past incidents" lead={<p>Incidents whose reports read most like this one, and how long they lasted.</p>}>
        {inc.similar.length ? (
          <ul className="text-xs">
            {inc.similar.map((s) => (
              <li key={s.id} className="flex flex-wrap items-baseline justify-between gap-x-4 border-t border-grid py-2.5 first:border-t-0">
                <span className="min-w-0">
                  <span className="font-medium text-ink">{s.service_name ?? names.get(s.service_id ?? "") ?? s.service_id ?? s.id}</span>{" "}
                  <span className="text-ink-soft">{s.title ?? ""}</span>
                </span>
                <span className="text-ink-soft">
                  {s.started_at ? <Time iso={s.started_at} style="date" /> : null}
                  {s.duration_min != null ? `, lasted ${formatDuration(s.duration_min)}` : ""}
                  {s.similarity != null ? `, ${formatShare(s.similarity)} similar` : ""}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-ink-soft">None found yet.</p>
        )}
      </Section>
    </>
  );
}

function NoForecast() {
  const meta = useDoc<Meta>("meta.json");
  if (meta.status === "loading") return <p className="text-ink-soft">&nbsp;</p>;
  return (
    <p className="text-ink-soft">
      {meta.data && meta.data.models == null
        ? "No forecast yet. Forecasts start once the recovery model is published."
        : "No forecast: the model could not score this incident."}
    </p>
  );
}

function sinceStart(minutes: number): string {
  return minutes < 1 ? "at the start" : `+${formatDuration(minutes)}`;
}

function Timeline({ updates, startedAt }: { updates: IncidentUpdate[]; startedAt: string }) {
  const [newestFirst, setNewestFirst] = useState(false);
  const start = parseTime(startedAt) ?? 0;
  const list = updates.slice().sort((a, b) => (parseTime(a.at) ?? 0) - (parseTime(b.at) ?? 0));
  const ordered = newestFirst ? list.slice().reverse() : list;
  if (!list.length) return <p className="text-ink-soft">The vendor has posted no updates.</p>;
  return (
    <div>
      <button type="button" className="chip mb-5" aria-pressed={newestFirst} onClick={() => setNewestFirst((v) => !v)}>
        Newest first
      </button>
      <ol className="relative ml-1.5 border-l border-rule">
        {ordered.map((u, i) => {
          const chrono = list.indexOf(u);
          const prev = chrono > 0 ? list[chrono - 1] : null;
          const changed = !prev || prev.status !== u.status;
          const at = parseTime(u.at) ?? start;
          return (
            <li key={`${u.at}-${i}`} className="relative pb-7 pl-6 last:pb-0">
              <span
                aria-hidden="true"
                className={`absolute top-1.5 -left-[5px] size-[9px] rounded-full ${changed ? "bg-ink" : "border border-rule bg-paper"}`}
              />
              <p className="text-xs text-ink-soft">
                <Time iso={u.at} style="datetime" />
                <span className="ml-3">{sinceStart(Math.max(0, (at - start) / MINUTE))}</span>
              </p>
              <p className={`mt-1 ${changed ? "font-medium text-ink" : "text-ink-soft"}`}>
                {changed && prev ? `${statusLabel(prev.status)} to ${statusLabel(u.status).toLowerCase()}` : statusLabel(u.status)}
              </p>
              <p className="prose-measure vendor-text mt-1.5 whitespace-pre-line text-[0.9375rem] leading-relaxed">{cleanBody(u.body)}</p>
              {u.components.length ? <p className="vendor-text mt-1.5 text-xs text-ink-soft">Affects {u.components.join(", ")}</p> : null}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
