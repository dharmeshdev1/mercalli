"""Shared helpers: a seeded database, fake engine pieces and a threaded uvicorn server."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from mercalli.catalog import Service
from mercalli.domain import Category, Component, Health, Impact, Incident, Phase, Snapshot, Update, utcnow
from mercalli.engine import TickReport
from mercalli.server.settings import ServerSettings
from mercalli.store import BlastRow, Database, EventRow, PredictionRow

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
SITE = "https://mercalli.example.com"
TICK = {
    "started_at": "2026-11-03T11:59:30+00:00",
    "duration_s": 21.4,
    "polled": 4,
    "ok": 4,
    "failed": 0,
    "changes": {"incident.opened": 2},
    "refreshed": 1,
    "models": {},
}

SERVICES = [
    Service(
        id="github",
        name="GitHub",
        category=Category.DEVTOOLS,
        adapter="statuspage",
        base_url="https://www.githubstatus.com",
        domains=["github.com"],
    ),
    Service(
        id="cloudflare",
        name="Cloudflare",
        category=Category.EDGE,
        adapter="statuspage",
        base_url="https://www.cloudflarestatus.com",
        domains=["cloudflare.com"],
    ),
    Service(
        id="vercel",
        name="Vercel",
        category=Category.HOSTING,
        adapter="statuspage",
        base_url="https://www.vercel-status.com",
        domains=["vercel.com"],
    ),
    Service(
        id="stripe",
        name="Stripe",
        category=Category.PAYMENTS,
        adapter="statuspage",
        base_url="https://status.stripe.com",
        domains=["stripe.com"],
    ),
]


def make_incident(
    service_id: str,
    source_id: str,
    *,
    title: str,
    created: datetime,
    bodies: list[str],
    impact: Impact = Impact.MINOR,
    status: Phase = Phase.INVESTIGATING,
    resolved: datetime | None = None,
    components: list[str] | None = None,
) -> Incident:
    updates = [
        Update(
            at=created + timedelta(minutes=5 * i),
            status=status if i == len(bodies) - 1 else Phase.INVESTIGATING,
            body=b,
            components=components or [],
        )
        for i, b in enumerate(bodies)
    ]
    return Incident(
        service_id=service_id,
        source_id=source_id,
        title=title,
        impact=impact,
        status=status,
        created_at=created,
        resolved_at=resolved,
        updated_at=updates[-1].at if updates else created,
        url=f"https://stspg.io/{source_id}",
        components=components or [],
        updates=updates,
    )


GITHUB = make_incident(
    "github",
    "gh1",
    title="Elevated errors on Actions",
    created=NOW - timedelta(minutes=40),
    bodies=["We are investigating reports of degraded performance for Actions."],
    components=["Actions"],
)
CLOUDFLARE = make_incident(
    "cloudflare",
    "cf1",
    title="Elevated 5xx errors in Frankfurt",
    created=NOW - timedelta(minutes=25),
    impact=Impact.MAJOR,
    bodies=["We are seeing elevated errors in FRA.", "A fix is being rolled out."],
    status=Phase.IDENTIFIED,
    components=["CDN/Cache"],
)


def seed(db: Database) -> None:
    db.create()
    db.sync_services(SERVICES)
    for svc in SERVICES:
        comps = [Component(id="c1", name="API", status=Health.OPERATIONAL)]
        db.record_snapshot(Snapshot(service_id=svc.id, fetched_at=NOW, components=comps))
    db.upsert_incident(GITHUB, seen_at=NOW - timedelta(minutes=35), seen_open=True)
    db.upsert_incident(CLOUDFLARE, seen_at=NOW - timedelta(minutes=20), seen_open=True)
    vercel_open = make_incident(
        "vercel",
        "vc1",
        title="Build delays",
        created=NOW - timedelta(hours=3),
        bodies=["Builds are queued longer than usual."],
    )
    db.upsert_incident(vercel_open, seen_at=NOW - timedelta(hours=3), seen_open=True)
    vercel_done = make_incident(
        "vercel",
        "vc1",
        title="Build delays",
        created=NOW - timedelta(hours=3),
        bodies=["Builds are queued longer than usual.", "This incident has been resolved."],
        status=Phase.RESOLVED,
        resolved=NOW - timedelta(hours=1),
    )
    db.upsert_incident(vercel_done, seen_at=NOW - timedelta(hours=1), seen_open=False)
    stripe_old = make_incident(
        "stripe",
        "st1",
        title="Delayed payouts",
        created=NOW - timedelta(days=2),
        bodies=["Resolved."],
        status=Phase.RESOLVED,
        resolved=NOW - timedelta(days=2, hours=-1),
    )
    db.upsert_incident(stripe_old, seen_at=NOW - timedelta(days=1), seen_open=False)
    with db.session() as s:
        s.add(
            PredictionRow(
                incident_id="github:gh1",
                made_at=NOW - timedelta(minutes=5),
                model_version="recovery-test",
                elapsed_min=35.0,
                phase="investigating",
                p10=10.0,
                p50=30.0,
                p90=90.0,
            )
        )
        s.add(
            BlastRow(
                upstream_incident_id="cloudflare:cf1",
                made_at=NOW - timedelta(minutes=10),
                model_version="blast-test",
                service_id="vercel",
                probability=0.42,
                evidence=["edge:cloudflare", "co-failed 3 of 41 times"],
            )
        )


def add_event(
    db: Database, kind: str, service_id: str, incident_id: str | None, payload: dict[str, Any], at: datetime = NOW
) -> int:
    with db.session() as s:
        row = EventRow(at=at, kind=kind, service_id=service_id, incident_id=incident_id, payload=payload)
        s.add(row)
        s.flush()
        return row.id


def settings_for(db_path: Path, **overrides: Any) -> ServerSettings:
    base: dict[str, Any] = {
        "db_url": f"sqlite:///{db_path}",
        "scheduler": False,
        "stream_poll": 0.05,
        "stream_heartbeat": 0.3,
        "follower_poll": 0.1,
        "interval": 0.2,
        "jitter": 0.0,
        "shutdown_grace": 5.0,
    }
    base.update(overrides)
    return ServerSettings(**base)


class FakeClient:
    """Stands in for PoliteClient where the engine under test never touches the network."""

    closed = False

    async def aclose(self) -> None:
        self.closed = True


def wait_until(predicate: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.02)


@contextmanager
def serve_in_thread(app: FastAPI) -> Iterator[str]:
    """A real uvicorn server on a free port, for tests that need true streaming."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_until(lambda: server.started)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        app.state.ctx.begin_shutdown()
        server.should_exit = True
        thread.join(15)


class FakeEngine:
    """An engine whose ticks take a set time and record how they were run. Runs on the worker thread."""

    def __init__(
        self,
        *,
        durations: list[float] | None = None,
        fail_first: int = 0,
        gate: threading.Event | None = None,
        on_tick: Callable[[int], None] | None = None,
    ) -> None:
        self.durations = durations or [0.0]
        self.fail_first = fail_first
        self.gate = gate
        self.on_tick = on_tick
        self.calls = self.active = self.max_active = self.finished = self.cancelled = 0

    async def tick(self, client: Any) -> TickReport:
        self.calls += 1
        n = self.calls
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.gate is not None:
                await asyncio.to_thread(self.gate.wait)
            await asyncio.sleep(self.durations[min(n, len(self.durations)) - 1])
            if n <= self.fail_first:
                raise RuntimeError("status pages on fire")
            if self.on_tick is not None:
                self.on_tick(n)
            self.finished += 1
            return TickReport(
                started_at=utcnow(),
                duration_s=0.01,
                polled=4,
                ok=3,
                failed={"stripe": "HTTP 503"},
                changes=Counter({"incident.updated": 1}),
            )
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1
