# Self-hosting Mercalli

The public edition at <https://dharmeshdev1.github.io/mercalli/> is a static site rebuilt every
few minutes. The self-hosted edition is a long running server you run yourself: `mercalli serve`.

## Why run your own

- **One minute polling.** Every status page is read every 60 seconds (configurable), so you hear
  about an incident minutes before the public edition shows it, and short incidents that open and
  close between public rebuilds are not missed.
- **Private alerts for your stack.** List the services you depend on and Mercalli tells Slack,
  Discord, Teams, email, PagerDuty or your own webhook when one of them has an incident, when it
  changes and when it ends. With the ML artifacts in place it also warns when an incident at a
  provider is likely to spill over onto a service you use, and forecasts how long incidents last.
- **Your own data.** Polls, incidents, forecasts and deliveries live in your PostgreSQL. The same
  JSON API the public site uses is served live from it, plus a server-sent event stream, Atom
  feeds and Prometheus metrics.


## Forecasts and blast radius

The models are trained every week by this repository's Models workflow and published as one archive.
`mercalli pull-artifacts --dir ./artifacts` downloads and unpacks it; point `--artifacts` (or
`MERCALLI_ARTIFACTS`) at that directory. Docker Compose does this for you: its `artifacts` service
pulls the archive into the shared volume before the server starts, and restarting it
(`docker compose restart artifacts mercalli`) picks up a newer model. A failed download never stops the
server; it keeps collecting status and serves `forecast: null` until models are available.
Set `MERCALLI_ARTIFACTS_URL` to use artifacts you trained yourself with `mercalli train`.

Memory: with the models loaded the server uses about 1.1 GiB (measured with Docker Compose, polling
813 services); without them, about 150 MiB. The Helm chart requests 1280 MiB by default.

## Quickstart with Docker Compose

You need Docker with Compose v2.

```sh
git clone https://github.com/dharmeshdev1/mercalli && cd mercalli
MERCALLI_IMAGE=ghcr.io/dharmeshdev1/mercalli:0.1.0 docker compose -f deploy/docker-compose.yml up -d --no-build
```

That pulls the published image (amd64 and arm64). To build from your checkout instead, run
`docker compose -f deploy/docker-compose.yml up --build -d`.

This starts PostgreSQL 17 and the server, which creates its tables, polls every status page and is
ready about a minute later:

```sh
curl -s localhost:8080/readyz          # {"ready": true, ...}
open http://localhost:8080             # the web app
curl -s localhost:8080/api/v1/overview.json | head -c 400
curl -sN localhost:8080/api/v1/stream  # live events as they happen
```

To add Prometheus (port 9090) and Grafana (port 3000, with the Mercalli dashboard as its home
page):

```sh
docker compose -f deploy/docker-compose.yml --profile observability up -d
```

Settings come from the environment or from a `deploy/.env` file next to the compose file:

```sh
# deploy/.env
POSTGRES_PASSWORD=a-long-random-password
MERCALLI_PUBLIC_URL=https://mercalli.example.com
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T000/B000/XXXX
```

The server reads `deploy/mercalli.example.yaml`. To use your own copy, set
`MERCALLI_CONFIG_FILE=./mercalli.yaml` in `deploy/.env` (the path is relative to `deploy/`).

Stop with `docker compose -f deploy/docker-compose.yml down`. Add `-v` to also delete the
database.

### Without Docker

From a checkout:

```sh
pip install ".[server]"                   # ".[ml,server]" to run the forecasting models too
mercalli serve --db postgresql://user:pass@localhost/mercalli --config mercalli.yaml
```

SQLite works too (`--db sqlite:///mercalli.db`) for a single process on one machine.

## Configuration

### Server options

| Flag | Environment | Default | Meaning |
| --- | --- | --- | --- |
| `--db` | `MERCALLI_DB`, `DATABASE_URL` | `sqlite:///mercalli.db` | `postgresql://...` or `sqlite:///...` |
| `--host`, `--port` | `MERCALLI_HOST`, `MERCALLI_PORT` | `127.0.0.1`, `8080` | Where to listen (the image uses `0.0.0.0`) |
| `--interval` | `MERCALLI_INTERVAL` | `60` | Seconds between polls; at least 10 |
| `--jitter` | | `0.1` | Each tick starts up to this fraction of the interval early or late |
| `--config` | `MERCALLI_CONFIG` | none | The YAML file described below |
| `--public-url` | `MERCALLI_PUBLIC_URL` | from the config file | The URL people reach this server at |
| `--web` | `MERCALLI_WEB` | none (image: bundled app) | Directory of the exported web app |
| `--artifacts` | `MERCALLI_ARTIFACTS` | none | Model, graph and census artifacts |
| `--cache` | `MERCALLI_CACHE` | none | State kept between runs, like embeddings |
| `--no-scheduler` | | | Serve the API only, from a database another process keeps current |
| `--log-level` | `MERCALLI_LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error` |
| `--access-log` | | off | Log every request |

### The config file

```yaml
public_url: https://mercalli.example.com
stack: [github, cloudflare, aws, stripe, openai]
blast_threshold: 0.3
max_event_age_minutes: 360
channels: [...]
retention:
  polls_days: 7
  predictions_days: 180
  events_days: 30
  deliveries_days: 30
```

- `stack` lists the service ids you depend on. Incident alerts are only sent for these. The ids are
  the ones in `/api/v1/services.json`; the server logs a warning for any it does not know.
- `blast_threshold` is the smallest probability, from 0 to 1, at which a predicted spill over from
  an upstream incident onto a stack service becomes an alert.
- `max_event_age_minutes`: events older than this when a channel reaches them (say, after the
  server was down) are skipped rather than sent late.
- `retention` sets how long raw history is kept. Incidents themselves are kept forever. Pruning
  runs once a day.

**Secrets.** Any string can read the environment: `${NAME}` (startup fails if `NAME` is unset),
`${NAME:-fallback}` (the fallback when unset or empty) or `${NAME-fallback}` (the fallback when
unset). `$$` is a literal `$`. Keep webhook URLs, keys and passwords in the environment, not in the
file. Error messages about the config never print values.

### Channels

Every channel has these fields:

| Field | Default | Meaning |
| --- | --- | --- |
| `type` | required | `slack`, `discord`, `teams`, `webhook`, `email` or `pagerduty` |
| `name` | required | Unique; it names the channel in logs, metrics and the deliveries table |
| `events` | all five (PagerDuty: opened and resolved) | Which of `incident.opened`, `incident.updated`, `incident.resolved`, `forecast`, `blast` to send |
| `min_impact` | `minor` | `none`, `maintenance`, `minor`, `major` or `critical` |
| `services` | the whole stack | A subset of `stack` for this channel only |
| `rate_limit` | per type | `{per_minute: 30, burst: 5}`; events over the limit wait for the next tick |
| `timeout` | `10` | Seconds per delivery attempt |
| `enabled` | on when its destination is set | Set `false` to switch a channel off, or `true` to fail fast when it is not set up |

A channel whose URL, routing key or SMTP host is empty stays off, which is how the example file
ships every channel off until its variable is set.

**Slack** (an [incoming webhook](https://api.slack.com/messaging/webhooks)). Messages use Block
Kit: a header, the incident title and latest update, a field grid and links.

```yaml
- type: slack
  name: slack
  url: ${SLACK_WEBHOOK_URL}
  events: [incident.opened, incident.resolved, blast]
  min_impact: minor
```

**Discord** (Server settings, Integrations, Webhooks). One embed per message, colored by impact.
Mentions are disabled, so text from a vendor can never ping your server.

```yaml
- type: discord
  name: discord
  url: ${DISCORD_WEBHOOK_URL}
  username: Mercalli
  min_impact: major
```

**Microsoft Teams.** An Adaptive Card 1.4, accepted both by a Workflows webhook trigger ("When a
Teams webhook request is received", then "Post card in a chat or channel") and by a classic incoming
webhook.

```yaml
- type: teams
  name: teams
  url: ${TEAMS_WEBHOOK_URL}
  min_impact: major
```

**Webhook.** A JSON `POST` to any URL:

```yaml
- type: webhook
  name: incident-bot
  url: https://bots.internal.example.com/mercalli
  secret: ${MERCALLI_WEBHOOK_SECRET}
  headers: {Authorization: "Bearer ${BOT_TOKEN}"}
  min_impact: none
```

```json
{
  "event": "incident.opened",
  "event_id": 4812,
  "occurred_at": "2026-11-03T12:00:04Z",
  "sent_at": "2026-11-03T12:00:31Z",
  "service": {"id": "github", "name": "GitHub"},
  "incident": {"id": "github:db4x8hh362t8", "title": "Elevated errors on Actions", "...": "the incident summary from docs/api.md"},
  "data": {"title": "Elevated errors on Actions", "impact": "minor", "status": "investigating"},
  "message": {"title": "New incident: GitHub", "text": "New incident: GitHub\nElevated errors on Actions\n..."},
  "links": {"vendor": "https://stspg.io/...", "mercalli": "https://mercalli.example.com/incident/?id=github:db4x8hh362t8"}
}
```

Blast events add `"target": {"id", "name"}`, the service that may be affected. Headers:
`X-Mercalli-Event` (the event kind), `X-Mercalli-Delivery` (the event id, the same on every
retry, so receivers can drop repeats) and, when `secret` is set,
`X-Mercalli-Signature: sha256=<hex>`, an HMAC SHA-256 of the raw body. Verify it before parsing:

```python
import hashlib, hmac


def verified(body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

Use `sent_at` to reject old replays.

**Email** over SMTP, as plain text with an HTML alternative. Messages about one incident thread
together in mail clients.

```yaml
- type: email
  name: email
  events: [incident.opened, incident.resolved]
  min_impact: major
  settings:
    host: smtp.example.com
    port: 587                 # default: 587 for starttls, 465 for ssl, 25 for none
    security: starttls        # starttls, ssl or none
    username: ${SMTP_USERNAME}
    password: ${SMTP_PASSWORD}
    from: mercalli@example.com
    to: [oncall@example.com]  # or one comma separated string, handy from the environment
```

**PagerDuty** (Events API v2). An incident that opens triggers an alert keyed by the incident id,
and its resolution resolves that alert. Updates and forecasts (if you add them to `events`) update
the open alert and never open a new one for an incident that is already over.

```yaml
- type: pagerduty
  name: pagerduty
  min_impact: major
  events: [incident.opened, incident.resolved, blast]
  settings:
    routing_key: ${PAGERDUTY_ROUTING_KEY}
    # url: https://events.eu.pagerduty.com/v2/enqueue   # EU service region
```

Severity follows impact: critical is `critical`, major is `error`, minor is `warning`. A blast
warning raises a `warning` alert of its own, which is resolved when the upstream incident ends,
even though that upstream provider is not in your stack.

### What gets sent, and when

After each tick the notifier delivers new events to each channel, in order:

- `incident.opened`, `incident.updated`, `incident.resolved` for stack services at or above the
  channel's `min_impact`. A channel that was told about an incident also hears how it ends, even if
  its impact dropped below the bar.
- `forecast`: a new estimate of when an incident ends. Only sent again when the likely end moves
  by about half an hour.
- `blast`: an upstream incident that may spill over onto a stack service, at or above
  `blast_threshold`. Sent once per upstream incident and service.

Each message names the service, the incident title, impact and status, the latest vendor update,
when it started, the forecast when there is one, and links to the vendor's incident page and to the
incident's page on your server.

Delivery is at least once with duplicates suppressed: every attempt is recorded in the
`deliveries` table, failures are retried with backoff (and on the following ticks, up to 12
attempts), a channel never skips ahead of an event it could not deliver, and an event already
delivered to a channel is never sent to it again, even after a crash. A new channel starts with
the next event; it does not replay history. Two updates to one incident that pile up (because an
endpoint was down, say) go out as one message with the latest state.

## The live API

Everything in [docs/api.md](api.md) is served at the same paths, built from your database:
`/api/v1/meta.json`, `overview.json`, `services.json`, `services/<id>.json`,
`incidents/<service>/<incident>.json`, the artifacts (`graph.json`, `model.json`,
`census-summary.json`, `replays.json`) when `--artifacts` holds them, and Atom feeds at
`/feeds/all.xml` and `/feeds/<id>.xml`. `meta.json` reports `"edition": "self-hosted"`.
`overview.json` and `services.json` are rebuilt after every tick and served from memory with
ETags and a gzip copy.

`GET /api/v1/stream` is a server-sent event stream. Each event has `id:` (the event id),
`event:` (the kind) and `data:` (the incident summary). A comment line arrives every 15 seconds
to keep proxies from closing the connection. Reconnecting clients send `Last-Event-ID` (browsers
do it for you, or pass `?last_event_id=`) and get what they missed first.

```js
const stream = new EventSource("/api/v1/stream");
stream.addEventListener("incident.opened", (e) => console.log(JSON.parse(e.data).title));
```

Behind nginx or ingress-nginx, the server sends `X-Accel-Buffering: no` so events are not held
in a buffer; raise the proxy read timeout (for example
`nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"`) so idle streams stay open.

## Kubernetes with Helm

The chart is in `deploy/helm/mercalli`; its [README](../deploy/helm/mercalli/README.md) has a
complete example. In short:

```sh
kubectl create namespace mercalli
kubectl -n mercalli create secret generic mercalli-db \
  --from-literal=DATABASE_URL='postgresql://mercalli:CHANGE_ME@postgres:5432/mercalli'
helm upgrade --install mercalli deploy/helm/mercalli -n mercalli \
  --set database.existingSecret=mercalli-db \
  --set server.publicUrl=https://mercalli.example.com
```

Replicas share the work safely: all of them serve the API, and a PostgreSQL advisory lock makes
exactly one of them poll and send alerts. Readiness (`/readyz`) waits for the database and a
first completed tick; liveness (`/healthz`) only asks whether the process answers.

## Metrics

`GET /metrics` serves Prometheus metrics:

| Metric | Type | Labels |
| --- | --- | --- |
| `mercalli_ticks_total` | counter | `result`: ok, error, timeout |
| `mercalli_tick_duration_seconds` | histogram | |
| `mercalli_last_tick_timestamp_seconds` | gauge | |
| `mercalli_services_polled` | gauge, last tick | `result`: ok, failed |
| `mercalli_incident_changes_total` | counter | `kind` |
| `mercalli_open_incidents` | gauge | `impact` |
| `mercalli_notifications_total` | counter | `channel_type`, `result`: sent, failed |
| `mercalli_http_requests_total` | counter | `method`, `route`, `status` |
| `mercalli_http_request_duration_seconds` | histogram, time to first byte | `method`, `route` |
| `mercalli_stream_clients` | gauge | |
| `mercalli_scheduler_leader` | gauge | 1 on the replica that polls |
| `process_*`, `python_*` | standard process and runtime metrics | |

`route` is the route template (for example `/api/v1/services/{service_id}.json`), never a raw
path. Useful alerts:

```yaml
- alert: MercalliTicksStalled
  expr: time() - max(mercalli_last_tick_timestamp_seconds) > 600
- alert: MercalliNotificationsFailing
  expr: sum(increase(mercalli_notifications_total{result="failed"}[30m])) > 0
```

The Grafana dashboard `deploy/grafana/dashboards/mercalli.json` shows tick health, failed status
pages, open incidents by impact, notification deliveries and API latency.

## Backups

All state is in the database. With Compose:

```sh
docker compose -f deploy/docker-compose.yml exec -T db \
  pg_dump -U mercalli -Fc mercalli > mercalli-$(date +%F).dump
# restore into an empty database
docker compose -f deploy/docker-compose.yml exec -T db \
  pg_restore -U mercalli -d mercalli --clean --if-exists < mercalli-2026-11-03.dump
```

Incidents, updates, forecasts and the delivery history are what is worth keeping. Polls are the
bulk of the database and are pruned after `retention.polls_days` anyway. With SQLite, copy the
file while the server is stopped, or use `sqlite3 mercalli.db ".backup backup.db"`.

## Upgrades

```sh
git pull
docker compose -f deploy/docker-compose.yml up --build -d
```

The server creates any missing tables at startup. It does not alter existing tables: there is no
migration tool yet, so a release that changes a table's columns says so in its notes, along with
the SQL to run. Take a backup before every upgrade. On shutdown the server stops starting new ticks, ends live streams, and gives a tick in progress up
to 20 seconds to finish; notification cursors live in the database, so nothing is lost or sent
twice across a restart. On Kubernetes, `helm upgrade` performs a rolling update; the advisory lock
hands the poller from the old pod to the new one.

## Troubleshooting

- **`/readyz` stays 503.** The body says why: `database` shows the connection error, and
  `ticked: false` means the first tick has not finished (it takes a minute or so).
- **No alerts.** The log lists which channels are on at startup. A channel without its URL or
  key is off. Check the service is in `stack`, the event kind is in `events` and the incident
  meets `min_impact`. The `deliveries` table records every attempt with its result.
- **Alerts without links to your server.** Set `public_url`.
