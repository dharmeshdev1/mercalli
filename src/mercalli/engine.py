"""One tick of the engine: poll every status page, reconcile incidents, run the models.

The same `Engine.tick` runs every five minutes in the public edition and every minute in a
self-hosted server. It is idempotent: running it twice in a row changes nothing the second time.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from mercalli.catalog import Service
from mercalli.domain import Incident, Snapshot, utcnow
from mercalli.http import PoliteClient
from mercalli.sources import RECENT, poll, recent
from mercalli.store import Database

log = logging.getLogger("mercalli.engine")

# A hook the ML layer plugs into. It receives the open incidents after reconciliation.
ModelHook = Callable[[Database, list[Incident], datetime], dict[str, int]]


@dataclass
class TickReport:
    started_at: datetime
    duration_s: float = 0.0
    polled: int = 0
    ok: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    changes: Counter[str] = field(default_factory=Counter)
    refreshed: int = 0
    models: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at.isoformat(),
            "duration_s": round(self.duration_s, 2),
            "polled": self.polled,
            "ok": self.ok,
            "failed": len(self.failed),
            "changes": dict(self.changes),
            "refreshed": self.refreshed,
            "models": self.models,
        }


class Engine:
    def __init__(
        self,
        db: Database,
        services: list[Service],
        *,
        hooks: list[ModelHook] | None = None,
        refresh_per_hour: float = 1.0,
        ticks_per_hour: float = 12.0,
    ) -> None:
        self.db = db
        self.services = services
        self.by_id = {s.id: s for s in services}
        self.hooks = hooks or []
        # How many services get their recent incident list re-read each tick, so every service is
        # refreshed about `refresh_per_hour` times an hour. This catches incidents that were posted
        # after they ended and never appeared as open.
        self.refresh_batch = max(1, math.ceil(len(services) * refresh_per_hour / ticks_per_hour))

    async def tick(self, client: PoliteClient) -> TickReport:
        t0 = time.perf_counter()
        now = utcnow()
        report = TickReport(started_at=now)
        snaps: list[Snapshot] = await asyncio.gather(*(poll(client, s) for s in self.services))
        report.polled = len(snaps)

        open_before: dict[str, set[str]] = {}
        for row in self.db.open_incidents():
            open_before.setdefault(row.service_id, set()).add(row.id)

        to_reconcile: list[tuple[Service, set[str]]] = []
        for snap in snaps:
            self.db.record_snapshot(snap)
            if not snap.ok:
                report.failed[snap.service_id] = snap.error or "unknown error"
                continue
            report.ok += 1
            seen = set()
            for inc in snap.incidents:
                seen.add(inc.id)
                change = self.db.upsert_incident(inc, seen_at=now, seen_open=True)
                if change:
                    report.changes[change] += 1
            gone = open_before.get(snap.service_id, set()) - seen
            if gone:
                to_reconcile.append((self.by_id[snap.service_id], gone))

        # Incidents that dropped off a page: read the page's recent list to learn when they ended.
        results = await asyncio.gather(*(self._reconcile(client, svc, gone, now) for svc, gone in to_reconcile))
        for changes in results:
            report.changes.update(changes)

        report.refreshed = await self._refresh_rotation(client, now, report)

        open_now = [r.to_domain() for r in self.db.open_incidents()]
        for hook in self.hooks:
            try:
                report.models.update(hook(self.db, open_now, now))
            except Exception:  # a model failure must never stop status collection
                log.exception("model hook %s failed", getattr(hook, "__name__", hook))
                report.models[f"{getattr(hook, '__name__', 'hook')}_failed"] = 1

        report.duration_s = time.perf_counter() - t0
        return report

    async def _reconcile(self, client: PoliteClient, svc: Service, gone: set[str], now: datetime) -> Counter[str]:
        changes: Counter[str] = Counter()
        found: dict[str, Incident] = {}
        if svc.adapter in RECENT:
            try:
                for inc in await recent(client, svc):
                    found[inc.id] = inc
            except Exception as exc:  # network or parse failure: fall back to inferring
                log.info("could not read recent incidents for %s: %s", svc.id, exc)
        for iid in gone:
            inc = found.get(iid)
            if inc is not None and inc.resolved_at is not None:
                change = self.db.upsert_incident(inc, seen_at=now, seen_open=False)
                if change:
                    changes[change] += 1
            elif inc is not None:
                # The summary skipped it this time, but the vendor still lists it as open. Pages
                # sit behind CDNs and feeds flap; trust the fuller list and keep it open.
                change = self.db.upsert_incident(inc, seen_at=now, seen_open=False)
                if change:
                    changes[change] += 1
            else:
                self.db.mark_resolved(iid, now)
                changes["incident.resolved"] += 1
        return changes

    async def _refresh_rotation(self, client: PoliteClient, now: datetime, report: TickReport) -> int:
        eligible = [s for s in self.services if s.adapter in RECENT and s.id not in report.failed]
        if not eligible:
            return 0
        state = self.db.kv_get("refresh_rotation") or {"offset": 0}
        offset = int(state.get("offset", 0)) % len(eligible)
        batch = (eligible + eligible)[offset : offset + self.refresh_batch]
        self.db.kv_set("refresh_rotation", {"offset": (offset + len(batch)) % len(eligible)})

        async def one(svc: Service) -> int:
            try:
                incidents = await recent(client, svc)
            except Exception:
                return 0
            n = 0
            for inc in incidents:
                change = self.db.upsert_incident(inc, seen_at=now, seen_open=False)
                if change:
                    report.changes[change] += 1
                    n += 1
            return n

        await asyncio.gather(*(one(s) for s in batch))
        return len(batch)
