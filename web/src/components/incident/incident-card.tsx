import Link from "next/link";
import { ImpactGlyph } from "@/components/ui/glyph";
import { Elapsed, Time } from "@/components/ui/time";
import type { IncidentSummary } from "@/lib/api/types";
import { formatDuration } from "@/lib/format";
import { impactLabel, impactRank, statusLabel } from "@/lib/severity";
import { BlastRow } from "./blast-row";
import { ForecastBar } from "./forecast-bar";
import { LatestUpdate } from "./latest-update";
import { Ripple } from "./ripple";

export function incidentHref(id: string): string {
  return `/incident/?id=${encodeURIComponent(id)}`;
}

/**
 * One incident as a ruled ledger entry: the severity glyph hangs in the margin, then the service,
 * the vendor's title, timing, the latest update, the forecast and the blast radius.
 */
export function IncidentCard({
  incident,
  isNew = false,
  showService = true,
  forecastNote,
}: {
  incident: IncidentSummary;
  isNew?: boolean;
  showService?: boolean;
  /** Said when there is no forecast, if anything should be. */
  forecastNote?: string;
}) {
  const i = incident;
  const resolved = i.resolved_at != null;
  return (
    <article className="ledger grid h-full min-w-0 grid-cols-[18px_minmax(0,1fr)] gap-x-3 px-4 pt-4 pb-4 md:px-5" aria-label={`${i.service_name}: ${i.title}`}>
      <span className="relative mt-[3px] flex h-4 items-center justify-center">
        <ImpactGlyph impact={i.impact} size={16} />
        {isNew ? <Ripple color={impactRank(i.impact) >= 3 ? "var(--sev-major)" : "var(--accent)"} /> : null}
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
          {showService ? (
            <Link href={`/s/${i.service_id}/`} className="quiet-link font-semibold text-ink">
              {i.service_name}
            </Link>
          ) : null}
          <span className="text-xs text-ink-soft">
            {impactLabel(i.impact)}, {statusLabel(i.status).toLowerCase()}
          </span>
        </div>
        <h3 className="vendor-text mt-1 text-base leading-snug font-normal">
          <Link href={incidentHref(i.id)} className="quiet-link text-ink">
            {i.title}
          </Link>
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          {resolved ? (
            <>
              Lasted {formatDuration(i.duration_min)}, ended <Time iso={i.resolved_at} style="datetime" />
            </>
          ) : (
            <>
              Open for <Elapsed iso={i.started_at} className="text-ink" />, since <Time iso={i.started_at} />
            </>
          )}
        </p>
        <LatestUpdate latest={i.latest} />
        {i.forecast && !resolved ? (
          <ForecastBar startedAt={i.started_at} forecast={i.forecast} />
        ) : forecastNote && !resolved ? (
          <p className="mt-3 text-xs text-ink-soft">{forecastNote}</p>
        ) : null}
        <BlastRow blast={i.blast} />
      </div>
    </article>
  );
}
