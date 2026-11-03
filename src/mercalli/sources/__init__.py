"""Source adapters: one per kind of status page software, all producing the same domain types."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from mercalli.catalog import Service
from mercalli.domain import Incident, Snapshot
from mercalli.http import PoliteClient
from mercalli.sources import statuspage, vendors

log = logging.getLogger("mercalli.sources")

SnapshotFn = Callable[[PoliteClient, Service], Awaitable[Snapshot]]
RecentFn = Callable[[PoliteClient, Service], Awaitable[list[Incident]]]

SNAPSHOT: dict[str, SnapshotFn] = {
    "statuspage": statuspage.snapshot,
    "aws": vendors.aws_snapshot,
    "gcp": vendors.gcp_snapshot,
    "azure": vendors.azure_snapshot,
    "slack": vendors.slack_snapshot,
    "heroku": vendors.heroku_snapshot,
    "statusio": vendors.statusio_snapshot,
}

RECENT: dict[str, RecentFn] = {
    "statuspage": statuspage.recent_incidents,
    "aws": vendors.aws_history,
    "gcp": vendors.gcp_history,
    "slack": vendors.slack_history,
    "heroku": vendors.heroku_history,
}


async def poll(client: PoliteClient, service: Service) -> Snapshot:
    """Poll one service, turning any failure into a snapshot that says so instead of raising."""
    fn = SNAPSHOT.get(service.adapter)
    if fn is None:
        return Snapshot(service_id=service.id, ok=False, error=f"no adapter for {service.adapter}")
    t0 = time.perf_counter()
    try:
        return await fn(client, service)
    except httpx.HTTPStatusError as exc:
        error = f"HTTP {exc.response.status_code}"
    except httpx.HTTPError as exc:
        error = type(exc).__name__
    except Exception as exc:  # a page that changed shape must fail alone, never the whole tick
        log.warning("could not read %s: %r", service.id, exc)
        error = f"unreadable response: {type(exc).__name__}"
    return Snapshot(service_id=service.id, ok=False, error=error, latency_ms=(time.perf_counter() - t0) * 1000)


async def recent(client: PoliteClient, service: Service) -> list[Incident]:
    fn = RECENT.get(service.adapter)
    if fn is None:
        return []
    return await fn(client, service)
