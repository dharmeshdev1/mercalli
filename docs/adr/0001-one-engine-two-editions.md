# 1. One engine, two editions

Status: accepted

## Context

Mercalli needs to be live for anyone who visits, for free, with no server to keep running. Teams
that depend on it also need what a static site cannot give: one minute freshness, alerts and their
own data.

## Decision

Build one engine (`Engine.tick`) and one API builder (`ApiBuilder`), and run them two ways:

- The public edition runs one tick per scheduled GitHub Actions job, against SQLite, and publishes
  the API as static files to GitHub Pages.
- The self-hosted edition runs ticks in a long-lived server against PostgreSQL and serves the same
  documents live.

## Consequences

- One code path to test for collection, reconciliation and modelling.
- The public edition has nothing running between ticks, so it costs nothing and has no attack
  surface, but its freshness is bounded by the scheduler (five minutes at best, often more).
- The store must stay within features SQLite and PostgreSQL share.
