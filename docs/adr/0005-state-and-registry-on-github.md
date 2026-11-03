# 5. Public edition state and models live on GitHub

Status: accepted

## Context

The public edition must run with no paid infrastructure and no secrets beyond the repository's own
token.

## Decision

- Engine state is a SQLite file carried between scheduled runs in the Actions cache.
- The raw incident history cache also lives in the Actions cache and is refreshed daily, which keeps
  it from being evicted.
- Trained models, the dependency graph, census results and the public dataset are published as
  assets of an `artifacts` release, which acts as the model registry with dated copies.
- The built web app is published as an asset of a `site` release.

## Consequences

- Anyone can fork the repository and get a working copy of the whole system with no accounts.
- Caches can be evicted. Every piece of state can be rebuilt from the vendors' pages, and the
  failure modes are documented in the architecture notes.
- The schedule is best effort. Runs can be delayed by many minutes at busy times, which is why the
  self-hosted edition exists for teams that need tighter freshness.
