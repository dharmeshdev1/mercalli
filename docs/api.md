# API

Mercalli serves one JSON API in two ways:

- **Public edition.** Static files at `https://dharmeshdev1.github.io/mercalli/api/v1/`, rewritten
  every few minutes by a scheduled pipeline. No key, no rate limit beyond GitHub Pages' own, and CORS
  is open, so a browser can call it directly.
- **Self-hosted.** The same paths, answered live by `mercalli serve` from your own database, plus a
  server-sent event stream at `/api/v1/stream`.

Every document carries `generated_at`. Times are UTC in ISO 8601 with a `Z`. Durations are minutes.
Fields are only ever added within `v1`; nothing is renamed or removed.

## Documents

| Path | What it holds |
| --- | --- |
| `meta.json` | Version, edition, counts, the last pipeline run, model and census versions |
| `overview.json` | Everything the home page needs: counts, every service's health, open incidents with forecasts and blast radius, the last 48 hours of resolved incidents, and the seismograph series |
| `services.json` | Every watched service with health, status page, domains and 90 day reliability |
| `services/<id>.json` | One service: components, its incidents over 90 days, what it depends on and what depends on it |
| `incidents/<service>/<incident>.json` | One incident: every update, every forecast made while it was open, the blast radius and similar past incidents. Kept for 14 days after it ends |
| `graph.json` | The dependency graph: services and providers as nodes, dependencies as edges with evidence |
| `model.json` | The model card: how the forecasters were trained and how accurate they are |
| `census-summary.json` | Shares of the top domains by DNS, edge, hosting provider, region, certificate authority and mail provider |
| `replays.json` | Timelines of past cascades, reconstructed from status page history |

Atom feeds live at `/feeds/all.xml` and `/feeds/<service>.xml`.

## Health and impact

`health` is the worst current state of a service's components:
`operational`, `maintenance`, `degraded`, `partial_outage`, `major_outage`, or `unknown` when its
status page could not be read for 30 minutes.

`impact` is the vendor's own label for an incident: `none`, `minor`, `major`, `critical`,
or `maintenance`. Mercalli maps every provider's scale onto these.

`status` is where an incident is in its lifecycle: `investigating`, `identified`, `monitoring`,
`resolved`, or `postmortem`.

## Incident summary

Used in `overview.json` and `services/<id>.json`.

```json
{
  "id": "github:db4x8hh362t8",
  "path": "incidents/github/db4x8hh362t8.json",
  "service_id": "github",
  "service_name": "GitHub",
  "category": "devtools",
  "title": "Elevated errors on Actions",
  "impact": "minor",
  "status": "investigating",
  "started_at": "2026-11-02T17:46:23Z",
  "resolved_at": null,
  "resolved_inferred": false,
  "duration_min": null,
  "elapsed_min": 31.5,
  "components": ["Actions"],
  "regions": [],
  "url": "https://stspg.io/ns6pj9qy9glb",
  "updates_count": 2,
  "latest": {"at": "2026-11-02T18:10:34Z", "status": "investigating", "body": "We are investigating..."},
  "first_seen_at": "2026-11-02T17:50:02Z",
  "forecast": {
    "p10": 12.0, "p50": 41.0, "p90": 180.0,
    "eta_p10": "2026-11-02T18:30:00Z", "eta_p50": "2026-11-02T18:59:00Z", "eta_p90": "2026-11-02T21:18:00Z",
    "made_at": "2026-11-02T18:18:00Z", "elapsed_min": 31.5, "model_version": "recovery-20261103-1200"
  },
  "blast": [
    {"service_id": "vercel", "service_name": "Vercel", "probability": 0.31,
     "evidence": ["edge:cloudflare", "named in 4 past incidents", "co-failed 3 of 41 times"]}
  ]
}
```

- `forecast` is the time **left** until the vendor marks the incident resolved: the 10th, 50th and
  90th percentile, in minutes from `made_at`, with the matching clock times. The 10th to 90th range
  is an 80% interval, calibrated on held out data. It is `null` for resolved incidents and for
  incidents the model cannot score.
- `blast` lists services that may post an incident of their own because of this one, most likely
  first. It is only computed for incidents at providers others depend on, and is `null` otherwise.
- `resolved_inferred` is `true` when the incident disappeared from the vendor's page without a
  resolution time, and Mercalli recorded the time it noticed.
- `first_seen_at` is when Mercalli first saw the incident, which can be later than `started_at`
  when a vendor backdates the start.

## Seismograph

`overview.json` includes `seismograph`: 48 hours of activity in 10 minute buckets.

```json
{
  "start": "2026-11-01T18:00:00Z",
  "bucket_minutes": 10,
  "points": 289,
  "series": [{"id": "aws", "name": "Amazon Web Services", "values": [0, 0, 1.0]}],
  "total": [3.5, 3.5, 4.0],
  "arrivals": [0, 0, 1.0]
}
```

A bucket's value for one provider is the weight of its worst open incident (critical 3, major 2,
minor 1, none 0.5). `total` sums the weights of every open incident across all services, and
`arrivals` sums the weights of incidents that began in that bucket, which is what shows a new
outage as a burst. Planned maintenance is left out of all three.

## Graph

```json
{
  "generated_at": "...",
  "nodes": [{"id": "cloudflare", "name": "Cloudflare", "category": "edge", "kind": "service", "dependents": 87}],
  "edges": [{"source": "discord", "target": "cloudflare", "kinds": ["edge", "dns"], "weight": 0.9,
             "evidence": ["dns:ns.cloudflare.com", "header:cf-ray"]}]
}
```

An edge points from a dependent to what it depends on. `kinds` says how the dependency was found:
`dns`, `edge` or `hosting` from the census, `named` when the dependent's own incident reports name
the provider, and `cofailure` when the two have failed together more often than chance.

## Stream (self-hosted only)

`GET /api/v1/stream` is a server-sent event stream. Each event is one of `incident.opened`,
`incident.updated`, `incident.resolved`, `forecast` (a first forecast for an incident) or `blast`
(a service in your view is likely to be affected by an upstream incident), with the incident summary
as data. Reconnect with `Last-Event-ID` to resume where you left off.
