"use client";

import { useState, type ReactNode } from "react";
import { useOverview } from "@/components/overview-context";
import { Glyph } from "@/components/ui/glyph";
import { PageHeader } from "@/components/ui/page-header";
import { Section } from "@/components/ui/section";
import { apiUrl, ApiError, fetchDoc, siteUrl } from "@/lib/api/client";
import { formatCount } from "@/lib/format";
import { HEALTH_GLYPH, HEALTH_LABEL, IMPACT_GLYPH, IMPACT_LABEL, IMPACT_WEIGHT, STATUS_LABEL } from "@/lib/severity";
import type { Health, Impact, Status } from "@/lib/api/types";

const PUBLIC_BASE = "https://dharmeshdev1.github.io/mercalli/api/v1/";

/** Shorten long arrays and strings so a live response fits on the page. */
function preview(value: unknown, depth = 0): unknown {
  if (Array.isArray(value)) {
    const keep = depth === 0 ? 3 : 2;
    const head = value.slice(0, keep).map((v) => preview(v, depth + 1));
    return value.length > keep ? [...head, `(${formatCount(value.length - keep)} more)`] : head;
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, preview(v, depth + 1)]));
  }
  if (typeof value === "string" && value.length > 140) return `${value.slice(0, 140)}...`;
  return value;
}

function TryIt({ path }: { path: string | null }) {
  const [state, setState] = useState<{ status: "idle" | "loading" | "ok" | "error"; text?: string }>({ status: "idle" });
  if (!path) return <span className="text-xs text-ink-soft">No example yet</span>;
  const load = async () => {
    setState({ status: "loading" });
    try {
      const { data } = await fetchDoc<unknown>(path);
      setState({ status: "ok", text: JSON.stringify(preview(data), null, 2) });
    } catch (err) {
      setState({
        status: "error",
        text: err instanceof ApiError && err.status === 404 ? "Not published yet: the API answered 404." : "The API did not answer.",
      });
    }
  };
  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="button min-h-8 px-3" onClick={() => (state.status === "ok" ? setState({ status: "idle" }) : void load())} aria-expanded={state.status === "ok"}>
          {state.status === "loading" ? "Loading" : state.status === "ok" ? "Hide response" : "Try it"}
        </button>
        <a href={apiUrl(path)} className="font-mono text-[0.72rem]">
          {path}
        </a>
      </div>
      {state.status === "ok" ? (
        <pre className="mt-3 max-h-96 overflow-auto rounded-sm border border-rule bg-paper p-3 font-mono text-[0.72rem] leading-relaxed text-ink">{state.text}</pre>
      ) : state.status === "error" ? (
        <p className="mt-2 text-xs text-ink" role="status">
          {state.text}
        </p>
      ) : null}
    </div>
  );
}

function Fields({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">Meaning</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([f, m]) => (
            <tr key={f}>
              <td className="font-mono text-[0.72rem] whitespace-nowrap text-ink">{f}</td>
              <td className="text-ink-soft">{m}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ApiDocs() {
  const { data } = useOverview();
  const sample = data?.active[0] ?? data?.recent[0] ?? null;
  const docs: { path: string | null; label: string; what: string }[] = [
    { path: "meta.json", label: "meta.json", what: "Version, edition, counts, the last pipeline run, model and census versions" },
    {
      path: "overview.json",
      label: "overview.json",
      what: "Everything the home page needs: counts, every service's health, open incidents with forecasts and blast radius, the last 48 hours of resolved incidents, and the seismograph series",
    },
    { path: "services.json", label: "services.json", what: "Every watched service with health, status page, domains and 90 day reliability" },
    {
      path: sample ? `services/${sample.service_id}.json` : null,
      label: "services/<id>.json",
      what: "One service: components, its incidents over 90 days, what it depends on and what depends on it",
    },
    {
      path: sample ? sample.path : null,
      label: "incidents/<service>/<incident>.json",
      what: "One incident: every update, every forecast made while it was open, the blast radius and similar past incidents. Kept for 14 days after it ends",
    },
    { path: "graph.json", label: "graph.json", what: "The dependency graph: services and providers as nodes, dependencies as edges with evidence" },
    { path: "model.json", label: "model.json", what: "The model card: how the forecasters were trained and how accurate they are" },
    {
      path: "census-summary.json",
      label: "census-summary.json",
      what: "Shares of the top domains by DNS, edge, hosting provider, region, certificate authority and mail provider",
    },
    { path: "replays.json", label: "replays.json", what: "Timelines of past cascades, reconstructed from status page history" },
  ];

  return (
    <>
      <PageHeader
        title="API"
        lead={
          <p>
            One JSON API, served two ways. The public edition is static files rewritten every few minutes, with no key and open CORS, so
            a browser can call it directly. A self-hosted server answers the same paths live from your own database.
          </p>
        }
        meta={
          <>
            Public base URL <code className="break-all text-ink">{PUBLIC_BASE}</code>
          </>
        }
      />

      <Section id="conventions" title="Conventions">
        <ul className="prose-measure list-disc space-y-2 pl-5 marker:text-ink-faint">
          <li>
            Every document carries <code>generated_at</code>.
          </li>
          <li>Times are UTC in ISO 8601 with a Z. Durations are minutes.</li>
          <li>Fields are only ever added within v1; nothing is renamed or removed.</li>
          <li>
            Self-hosted servers also stream changes as server-sent events at <code>/api/v1/stream</code>.
          </li>
        </ul>
      </Section>

      <Section id="documents" title="Documents" lead={<p>Each one can be fetched live from here. Responses are shortened for display.</p>}>
        <ul className="space-y-6">
          {docs.map((d) => (
            <li key={d.label} className="border-t border-grid pt-5 first:border-t-0 first:pt-0">
              <p className="font-mono text-[0.8rem] font-medium text-ink">{d.label}</p>
              <p className="prose-measure mt-1 text-ink-soft">{d.what}.</p>
              <div className="mt-3">
                <TryIt path={d.path} />
              </div>
            </li>
          ))}
        </ul>
        <p className="mt-8 text-ink-soft">
          Atom feeds live at <a href={siteUrl("feeds/all.xml")}>feeds/all.xml</a> and <code>feeds/&lt;service&gt;.xml</code>.
        </p>
      </Section>

      <Section id="vocabulary" title="Health, impact and status">
        <div className="grid grid-cols-1 gap-x-8 gap-y-8 md:grid-cols-3">
          <div>
            <h3 className="text-base font-medium">health</h3>
            <p className="mt-1 text-xs text-ink-soft">The worst current state of a service&apos;s components. Unknown when its status page could not be read for 30 minutes.</p>
            <ul className="mt-3 space-y-1.5 text-xs">
              {(Object.keys(HEALTH_LABEL) as Health[]).map((h) => (
                <li key={h} className="flex items-center gap-2">
                  <Glyph spec={HEALTH_GLYPH[h]} size={13} />
                  <code className="text-ink">{h}</code>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="text-base font-medium">impact</h3>
            <p className="mt-1 text-xs text-ink-soft">The vendor&apos;s own label for an incident, mapped from every provider&apos;s scale.</p>
            <ul className="mt-3 space-y-1.5 text-xs">
              {(Object.keys(IMPACT_LABEL) as Impact[]).map((i) => (
                <li key={i} className="flex items-center gap-2">
                  <Glyph spec={IMPACT_GLYPH[i]} size={13} />
                  <code className="text-ink">{i}</code>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="text-base font-medium">status</h3>
            <p className="mt-1 text-xs text-ink-soft">Where an incident is in its lifecycle.</p>
            <ul className="mt-3 space-y-1.5 text-xs">
              {(["investigating", "identified", "monitoring", "resolved", "postmortem"] as Status[]).map((s) => (
                <li key={s}>
                  <code className="text-ink">{s}</code> <span className="text-ink-soft">{STATUS_LABEL[s]}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Section>

      <Section id="incident-summary" title="Incident summary" lead={<p>Used in overview.json and services/&lt;id&gt;.json.</p>}>
        <Fields
          rows={[
            ["id", "service:incident, unique across Mercalli"],
            ["path", "Where the full incident document lives"],
            ["impact, status", "See the vocabulary above"],
            ["started_at", "Start, using the vendor's backdated start when it gave one"],
            ["resolved_at", "End, or null while open"],
            ["resolved_inferred", "True when the incident disappeared from the vendor's page without a resolution time, and Mercalli recorded the time it noticed"],
            ["duration_min, elapsed_min", "Length once resolved; time open so far"],
            ["latest", "The most recent vendor update: at, status, body"],
            ["first_seen_at", "When Mercalli first saw it, which can be later than started_at when a vendor backdates the start"],
            [
              "forecast",
              "The time left until the vendor marks it resolved: p10, p50 and p90 in minutes from made_at, with the matching clock times. The 10th to 90th range is an 80% interval, calibrated on held out data. Null for resolved incidents and incidents the model cannot score",
            ],
            ["blast", "Services that may post an incident of their own because of this one, most likely first, with probability and evidence. Only computed for incidents at providers others depend on, null otherwise"],
          ]}
        />
      </Section>

      <Section id="seismograph" title="Seismograph" lead={<p>Part of overview.json: 48 hours of activity in 10 minute buckets.</p>}>
        <Fields
          rows={[
            ["start, bucket_minutes, points", "When the first bucket begins, how long each bucket is, and how many there are"],
            ["series", "Per provider: the weight of its worst open incident in each bucket"],
            ["total", "The weights of every open incident across all services, summed per bucket"],
          ]}
        />
        <p className="mt-4 text-ink-soft">
          Weights:{" "}
          {(Object.keys(IMPACT_WEIGHT) as Impact[]).map((k, i, all) => (
            <span key={k}>
              <code className="text-ink">{k}</code> {IMPACT_WEIGHT[k]}
              {i < all.length - 1 ? ", " : "."}
            </span>
          ))}
        </p>
      </Section>

      <Section id="graph" title="Graph" lead={<p>graph.json. An edge points from a dependent to what it depends on.</p>}>
        <Fields
          rows={[
            ["nodes", "id, name, category, kind (service or provider), health for watched services, and dependents"],
            ["edges", "source, target, kinds, weight, evidence"],
            ["kinds", "dns, edge or hosting from the census; named when the dependent's own reports name the provider; cofailure when the two fail together more often than chance"],
          ]}
        />
      </Section>

      <Section id="stream" title="Stream" lead={<p>Self-hosted only.</p>}>
        <p className="prose-measure text-ink-soft">
          <code className="text-ink">GET /api/v1/stream</code> is a server-sent event stream. Each event is one of{" "}
          <code className="text-ink">incident.opened</code>, <code className="text-ink">incident.updated</code>,{" "}
          <code className="text-ink">incident.resolved</code> or <code className="text-ink">forecast</code>, with the incident summary as data.
        </p>
      </Section>
    </>
  );
}
