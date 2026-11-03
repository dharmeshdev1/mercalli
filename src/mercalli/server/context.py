"""State shared by the routes and background tasks of one server process."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import text

from mercalli.catalog import load_services
from mercalli.domain import utcnow
from mercalli.engine import Engine
from mercalli.notify.config import Config
from mercalli.notify.notifier import Notifier
from mercalli.server.documents import ArtifactStore, DocumentSet, LiveBuilder, build_documents
from mercalli.server.metrics import IMPACTS, Metrics
from mercalli.server.scheduler import LAST_TICK_KEY, EngineLike, Scheduler, default_client, load_hooks
from mercalli.server.settings import ServerSettings
from mercalli.server.stream import EventHub
from mercalli.store import Database

log = logging.getLogger("mercalli.server")


class ServerContext:
    def __init__(
        self,
        settings: ServerSettings,
        config: Config,
        *,
        clock: Callable[[], datetime] = utcnow,
        engine_factory: Callable[[], EngineLike] | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.config = config
        self.clock = clock
        self.db = Database(settings.db_url)
        self.store = ArtifactStore(settings.artifacts)
        self.metrics = Metrics(settings.edition)
        self.hub = self._new_hub()
        self.metrics.stream_clients.set_function(lambda: self.hub.clients)
        self.engine_factory: Callable[[], EngineLike] = engine_factory or self._default_engine
        self.client_factory: Callable[[], Any] = client_factory or default_client
        self.docs: DocumentSet | None = None
        self.last_tick: dict[str, Any] | None = None
        self.ticks_completed = 0
        self.leader_tick_at: datetime | None = None
        self.leading = False
        self.started_at = utcnow()
        self.db_ready: asyncio.Event | None = None
        self.scheduler: Scheduler | None = None
        self._docs_lock = threading.Lock()
        self._tasks: list[asyncio.Task[None]] = []
        self._scheduler_task: asyncio.Task[None] | None = None

    def _new_hub(self) -> EventHub:
        s = self.settings
        return EventHub(self.db, self.builder, poll=s.stream_poll, max_clients=s.max_stream_clients)

    def _default_engine(self) -> Engine:
        services = load_services()
        self.db.sync_services(services)
        hooks = load_hooks(self.settings.artifacts, self.settings.cache_dir)
        return Engine(self.db, services, hooks=hooks, ticks_per_hour=3600.0 / self.settings.interval)

    # Documents ------------------------------------------------------------------------------------

    def builder(self) -> LiveBuilder:
        return LiveBuilder(self.db, self.store, now=self.clock(), edition=self.settings.edition)

    def tick_report(self) -> dict[str, Any] | None:
        return (self.last_tick or {}).get("report")

    def documents(self) -> DocumentSet:
        docs = self.docs
        if docs is not None:
            return docs
        with self._docs_lock:  # one build for a burst of first requests, not one each
            if self.docs is None:
                self.docs = self._build_documents()
            return self.docs

    def refresh_documents(self) -> DocumentSet:
        docs = self._build_documents()
        self.docs = docs
        return docs

    def _build_documents(self) -> DocumentSet:
        docs = build_documents(
            self.db, self.store, now=self.clock(), edition=self.settings.edition, tick=self.tick_report()
        )
        for impact in IMPACTS:
            self.metrics.open_incidents.labels(impact).set(docs.open_by_impact.get(impact, 0))
        return docs

    def make_notifier(self) -> Notifier | None:
        if not self.config.active_channels:
            return None
        edition = self.settings.edition
        return Notifier(
            self.db, self.config, builder_factory=lambda now: LiveBuilder(self.db, self.store, now=now, edition=edition)
        )

    # Lifecycle ------------------------------------------------------------------------------------

    async def start(self) -> None:
        self.started_at = utcnow()
        self.db_ready = asyncio.Event()
        self.hub = self._new_hub()
        self._describe_config()
        try:
            # One quick attempt inline, so a reachable database is ready before the first request.
            await asyncio.wait_for(asyncio.to_thread(self._init_database), 5.0)
            self.db_ready.set()
        except Exception as exc:
            log.warning("database not ready yet (%s); retrying in the background", _short(exc))
            self._tasks.append(asyncio.create_task(self._init_until_ready(), name="mercalli-init"))
        self._tasks.append(asyncio.create_task(self.hub.run(), name="mercalli-stream"))
        self.scheduler = Scheduler(self)
        self._scheduler_task = asyncio.create_task(self.scheduler.run(), name="mercalli-scheduler")

    def begin_shutdown(self) -> None:
        """End live streams and start no new tick. Safe to call more than once."""
        self.hub.close()
        if self.scheduler is not None:
            self.scheduler.stop()

    async def stop(self) -> None:
        self.begin_shutdown()
        task, self._scheduler_task = self._scheduler_task, None
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), self.settings.shutdown_grace)
            except TimeoutError:
                log.warning("the running tick did not finish within %.0fs; cancelling it", self.settings.shutdown_grace)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            except Exception:
                log.exception("the scheduler stopped with an error")
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(self.db.engine.dispose)

    def _init_database(self) -> None:
        self.db.create()
        self.last_tick = self.db.kv_get(LAST_TICK_KEY)

    async def _init_until_ready(self) -> None:
        assert self.db_ready is not None
        delay = 1.0
        while True:
            try:
                await asyncio.to_thread(self._init_database)
            except Exception as exc:  # the database may still be starting up
                log.warning("database not ready (%s); retrying in %.0fs", _short(exc), delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            self.db_ready.set()
            log.info("database ready")
            return

    def _describe_config(self) -> None:
        try:
            known = {s.id for s in load_services()}
        except Exception:
            known = set()
        unknown = [s for s in self.config.stack if known and s not in known]
        if unknown:
            log.warning("the stack names services the catalog does not have: %s", ", ".join(unknown))
        on = [ch.name for ch in self.config.channels if ch.active]
        off = [ch.name for ch in self.config.channels if not ch.active]
        if self.config.channels:
            log.info("notification channels on: %s; off: %s", ", ".join(on) or "none", ", ".join(off) or "none")
        if on and not self.config.public_url:
            log.warning("public_url is not set, so alerts will not link to this server's incident pages")

    # Health ---------------------------------------------------------------------------------------

    async def readiness(self) -> tuple[bool, dict[str, Any]]:
        database = "ok"
        try:
            await asyncio.wait_for(asyncio.to_thread(self._ping), 3.0)
        except TimeoutError:
            database = "timed out"
        except Exception as exc:
            database = f"error: {_short(exc)}"
        schema = self.db_ready is not None and self.db_ready.is_set()
        if not self.settings.scheduler:
            ticked = True  # serving a database that another process keeps current
        else:
            leader_ticked = self.leader_tick_at is not None and self.leader_tick_at >= self.started_at
            ticked = self.ticks_completed > 0 or leader_ticked
        ready = database == "ok" and schema and ticked
        return ready, {
            "ready": ready,
            "database": database,
            "schema": schema,
            "ticked": ticked,
            "ticks": self.ticks_completed,
            "last_tick_at": (self.last_tick or {}).get("finished_at"),
            "leader": self.leading,
        }

    def _ping(self) -> None:
        with self.db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))


def _short(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return (lines[0] if lines else type(exc).__name__)[:200]
