# Operations

How the public deployment runs, what to look at when something looks wrong, and how to fix it.
Everything here runs on GitHub: Actions for compute, the Actions cache for state, releases for
artifacts, and Pages for hosting.

## What runs when

| Workflow | Schedule | Does | State it touches |
| --- | --- | --- | --- |
| Live pipeline (`pipeline.yml`) | Every 5 minutes | One engine tick, publish the API, deploy Pages | `state-*` cache, reads `artifacts` and `site` releases |
| Models (`models.yml`) | Daily at 03:17 UTC; weekly steps on Sundays | Refresh history; weekly: discovery, census, train, publish artifacts | `raw-*` cache, writes the `artifacts` release, may commit `catalog/services.yaml` |
| Web (`web.yml`) | On changes under `web/` | Build the web app, publish it to the `site` release, trigger a deploy | Writes the `site` release |
| CI (`ci.yml`) | Every push and pull request | Lint, tests on SQLite and PostgreSQL, web build, container build | Nothing |
| Release (`release.yml`) | On `v*` tags | Multi-arch container image to GHCR, wheel and sdist on the release | GHCR, the tagged release |

GitHub runs scheduled workflows on a best effort basis: they can start late, and for some
repositories they do not start at all for long stretches. So the pipeline does not depend on the
scheduler alone. Its last step checks whether a scheduled run has started in the past fifteen
minutes; if not, it waits until about five minutes have passed since it began and dispatches the
next run itself. While the scheduler works, that step does nothing. The Models workflow also
restarts the pipeline once a day if nothing is running, so the chain can never stay broken. The
site shows its own data age, and `meta.json` records the last tick.

## Health at a glance

- `api/v1/meta.json` has `generated_at`, `services_ok` against `services`, and the last tick report:
  how long it took, how many polls failed, and what changed.
- A falling `services_ok` across many vendors at once usually means the runner's network had a bad
  moment, not that the internet broke. One vendor failing for hours usually means its page moved
  or changed format.
- The model card (`api/v1/model.json`) carries the version and training date of the models in use.

## Runbook

**The site is stale.** Check the Live pipeline runs. If none is running or queued, start one with
`gh workflow run pipeline.yml`; it keeps itself going from there. If runs fail at "Deploy", check
that Pages is enabled with GitHub Actions as the source.

**One service is always `unknown`.** Its status page failed for 30 minutes or more. Run
`mercalli catalog discover --top 0` locally to re-verify the seed list; if the page moved, update
`catalog/seed.yaml`.

**A vendor resolved an incident but Mercalli still shows it open.** The vendor removed it from its
summary without resolving it in its recent list, and the refresh has not reached it yet. The
rotation re-reads every service about hourly. If it persists, the incident is still open on the
vendor's own page.

**Forecasts disappeared.** The `artifacts` release is missing or failed to load. The pipeline log
says which (`no artifacts release yet`, or an exception from `mercalli.ml`). Trigger the Models
workflow with `weekly: true` to rebuild.

**The engine state was evicted.** The next run starts a fresh database. Current incidents reappear
on the first tick, recent history within an hour through the refresh rotation. Forecast history
for incidents open at the time of the eviction is lost; nothing else is.

**The raw history cache was evicted.** The next Models run downloads the history again from every
vendor's page. Expect it to take one to three hours.

## Rolling back a model

Every training run keeps a dated copy in the `artifacts` release (`artifacts-YYYYMMDD.tar.gz`). To
roll back, download the copy you want and upload it as `artifacts.tar.gz`:

```sh
gh release download artifacts --pattern 'artifacts-20261101.tar.gz'
cp artifacts-20261101.tar.gz artifacts.tar.gz
gh release upload artifacts artifacts.tar.gz --clobber
```

The next pipeline run picks it up.

## Being a good citizen

Mercalli requests each status page once per tick, identifies itself with a User-Agent that links to
this repository, never sends more than two requests at a time to one host, and backs off on `429`
and `5xx` responses. If a vendor asks to be removed, add its Statuspage page id to
`catalog/exclude.yaml`.
