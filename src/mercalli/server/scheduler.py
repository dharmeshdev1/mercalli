"""The background loop of a server process.

The replica holding the scheduler lock runs a cycle every interval: an engine tick, then the
notifier, then a rebuild of the cached documents, and once a day a prune. Cycles never overlap,
because the next one is only scheduled when the last has finished. The other replicas follow: they
watch for the leader's last tick in the kv table and rebuild their own caches when it changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import delete

from mercalli.domain import utcnow
from mercalli.engine import ModelHook, TickReport
from mercalli.http import PoliteClient
from mercalli.notify.config import Retention
from mercalli.notify.notifier import Notifier
from mercalli.server.leader import Leadership
from mercalli.server.worker import WorkerLoop
from mercalli.store import Database, DeliveryRow

if TYPE_CHECKING:
    from mercalli.server.context import ServerContext

log = logging.getLogger("mercalli.server")

LAST_TICK_KEY = "server:last_tick"
LAST_PRUNE_KEY = "server:last_prune"


class EngineLike(Protocol):
    async def tick(self, client: Any) -> TickReport: ...


def default_client() -> PoliteClient:
    # The same manners as `mercalli tick`: most status pages sit on a few shared hosts.
    return PoliteClient(concurrency=48, per_host=2, timeout=10, retries=1)


def load_hooks(artifacts: Path | None, cache: Path | None) -> list[ModelHook]:
    """Model hooks, loaded the way `mercalli tick` loads them. A server keeps polling without them."""
    if not artifacts or not (artifacts / "model").exists():
        return []
    try:
        from mercalli.ml.live import build_hooks
    except ImportError as exc:
        log.error("%s has a model/ directory but the ML layer is not importable (%s); no forecasts", artifacts, exc)
        return []
    try:
        hooks = list(build_hooks(artifacts, cache))
    except Exception:
        log.exception("could not load the models in %s; running without forecasts", artifacts)
        return []
    log.info("loaded %d model hooks from %s", len(hooks), artifacts)
    return hooks


def prune(db: Database, retention: Retention, now: datetime) -> int:
    """Drop old polls, events and forecasts, and deliveries the store's own prune does not cover."""
    db.prune(
        keep_polls=timedelta(days=retention.polls_days),
        keep_events=timedelta(days=retention.events_days),
        keep_predictions=timedelta(days=retention.predictions_days),
    )
    with db.session() as s:
        result = s.execute(delete(DeliveryRow).where(DeliveryRow.at < now - timedelta(days=retention.deliveries_days)))
        return int(getattr(result, "rowcount", 0) or 0)


def _short(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return (text[0] if text else type(exc).__name__)[:200]


class Scheduler:
    def __init__(self, ctx: ServerContext, *, rng: random.Random | None = None) -> None:
        self.ctx = ctx
        self.settings = ctx.settings
        self._rng = rng or random.Random()
        self._stop = asyncio.Event()
        self._leadership = Leadership(ctx.db, enabled=ctx.settings.scheduler)
        self._worker: WorkerLoop | None = None
        self._engine: EngineLike | None = None
        self._client: Any = None
        self._notifier: Notifier | None = None
        self._seen_tick: str | None = None
        self._last_prune: datetime | None = None

    def stop(self) -> None:
        """Start no new cycle. One in progress finishes, within the shutdown grace period."""
        self._stop.set()

    async def run(self) -> None:
        try:
            if not await self._until_database_ready():
                return
            while not self._stop.is_set():
                started = time.monotonic()
                delay = self.settings.follower_poll
                try:
                    if await self._lead():
                        await self._cycle()
                        delay = self._delay(started)
                    else:
                        await self._follow()
                except Exception:  # the loop must outlive any single failure
                    log.exception("scheduler round failed")
                await self._pause(delay)
        finally:
            await self._teardown()

    # Roles ----------------------------------------------------------------------------------------

    async def _until_database_ready(self) -> bool:
        ready = self.ctx.db_ready
        assert ready is not None
        waiters = {asyncio.create_task(ready.wait()), asyncio.create_task(self._stop.wait())}
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()
        return ready.is_set() and not self._stop.is_set()

    async def _lead(self) -> bool:
        try:
            leading = await asyncio.to_thread(self._leadership.hold)
        except Exception as exc:
            log.warning("could not reach the database for the scheduler lock: %s", _short(exc))
            leading = False
        if leading and self._engine is None:
            leading = await self._take_over()
        elif not leading and self._engine is not None:
            await self._step_down()
        return leading

    async def _take_over(self) -> bool:
        try:
            engine = await asyncio.to_thread(self.ctx.engine_factory)
            if self._worker is None:
                self._worker = WorkerLoop()
            self._client = await self._worker.run(_construct(self.ctx.client_factory), 30)
        except Exception:
            log.exception("could not start the scheduler; trying again next round")
            return False
        self._engine = engine
        self._notifier = self.ctx.make_notifier()
        self.ctx.leading = True
        self.ctx.metrics.leader.set(1)
        log.info("this process runs the scheduler, every %.0fs", self.settings.interval)
        return True

    async def _step_down(self) -> None:
        log.warning("another process took over the scheduler; following it")
        await self._close_worker_resources()
        self._engine = None
        self.ctx.leading = False
        self.ctx.metrics.leader.set(0)

    async def _follow(self) -> None:
        try:
            state = await asyncio.to_thread(self.ctx.db.kv_get, LAST_TICK_KEY)
        except Exception as exc:
            log.debug("could not read the leader's last tick: %s", _short(exc))
            return
        finished = (state or {}).get("finished_at")
        if finished and finished != self._seen_tick:
            self._seen_tick = finished
            self.ctx.last_tick = state
            with contextlib.suppress(ValueError):
                self.ctx.leader_tick_at = datetime.fromisoformat(finished)
            await self._refresh()
        elif self.ctx.docs is None:
            await self._refresh()

    # One cycle ------------------------------------------------------------------------------------

    async def _cycle(self) -> None:
        report = await self._tick()
        if report is not None:
            await self._record_tick(report)
        if self._stop.is_set():
            # The cursor and the cache survive in the database: the next leader carries on from here.
            return
        # A notifier first started before any tick would treat the first sync's backlog as news.
        if self._notifier is not None and self.ctx.ticks_completed > 0:
            await self._notify()
        await self._refresh()
        await self._prune_if_due()

    async def _tick(self) -> TickReport | None:
        assert self._worker is not None and self._engine is not None
        metrics = self.ctx.metrics
        try:
            report = await self._worker.run(self._engine.tick(self._client), self.settings.effective_tick_timeout)
        except TimeoutError:
            metrics.ticks.labels("timeout").inc()
            log.error("tick did not finish within %.0fs and was cancelled", self.settings.effective_tick_timeout)
            return None
        except Exception:
            metrics.ticks.labels("error").inc()
            log.exception("tick failed")
            return None
        metrics.ticks.labels("ok").inc()
        return report

    async def _record_tick(self, report: TickReport) -> None:
        metrics = self.ctx.metrics
        finished = utcnow()
        state = {"report": report.as_dict(), "finished_at": finished.isoformat()}
        metrics.tick_duration.observe(report.duration_s)
        metrics.last_tick.set(finished.timestamp())
        metrics.services_polled.labels("ok").set(report.ok)
        metrics.services_polled.labels("failed").set(len(report.failed))
        for kind, n in report.changes.items():
            metrics.incident_changes.labels(kind).inc(n)
        self.ctx.ticks_completed += 1
        self.ctx.last_tick = state
        try:
            await asyncio.to_thread(self.ctx.db.kv_set, LAST_TICK_KEY, state)
        except Exception as exc:
            log.warning("could not record the tick for other replicas: %s", _short(exc))
        changes = ", ".join(f"{k} {v}" for k, v in sorted(report.changes.items())) or "none"
        log.info(
            "tick took %.1fs: %d polled, %d ok, %d failed; changes: %s",
            report.duration_s,
            report.polled,
            report.ok,
            len(report.failed),
            changes,
        )
        if report.failed:
            log.debug("failed: %s", ", ".join(f"{k} ({v})" for k, v in sorted(report.failed.items())))

    async def _notify(self) -> None:
        assert self._worker is not None and self._notifier is not None
        try:
            rep = await self._worker.run(self._notifier.run(), self.settings.effective_notify_timeout)
        except TimeoutError:
            log.warning(
                "notifications did not finish within %.0fs; they resume from their cursors next tick",
                self.settings.effective_notify_timeout,
            )
            return
        except Exception:
            log.exception("notifier failed")
            return
        for ctype, counts in rep.by_type().items():
            for result in ("sent", "failed"):
                if counts[result]:
                    self.ctx.metrics.notifications.labels(ctype, result).inc(counts[result])
        if rep.sent or rep.failed:
            log.info("notifications: %d sent, %d failed", rep.sent, rep.failed)

    async def _refresh(self) -> None:
        try:
            await asyncio.to_thread(self.ctx.refresh_documents)
        except Exception:
            log.exception("could not rebuild the cached documents")
        self.ctx.hub.poke()

    async def _prune_if_due(self) -> None:
        now = utcnow()
        try:
            if self._last_prune is None:
                at = ((await asyncio.to_thread(self.ctx.db.kv_get, LAST_PRUNE_KEY)) or {}).get("at")
                if isinstance(at, str):
                    with contextlib.suppress(ValueError):
                        self._last_prune = datetime.fromisoformat(at)
            if self._last_prune is not None and (now - self._last_prune).total_seconds() < self.settings.prune_every:
                return
            removed = await asyncio.to_thread(prune, self.ctx.db, self.ctx.config.retention, now)
            await asyncio.to_thread(self.ctx.db.kv_set, LAST_PRUNE_KEY, {"at": now.isoformat()})
        except Exception:
            log.exception("pruning failed; trying again next cycle")
            return
        self._last_prune = now
        log.info("pruned old polls, events and forecasts, and %d old deliveries", removed)

    # Timing and teardown --------------------------------------------------------------------------

    def _delay(self, started: float) -> float:
        # Jitter spreads many self-hosted servers across the minute instead of all polling at :00.
        spread = self.settings.interval * self.settings.jitter
        target = started + self.settings.interval + self._rng.uniform(-spread, spread)
        return max(0.0, target - time.monotonic())

    async def _pause(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), seconds)

    async def _close_worker_resources(self) -> None:
        if self._worker is None:
            return
        for resource in (self._client, self._notifier):
            closer = getattr(resource, "aclose", None)
            if closer is not None:
                with contextlib.suppress(Exception):
                    await self._worker.run(closer(), 5)
        self._client = None
        self._notifier = None

    async def _teardown(self) -> None:
        await self._close_worker_resources()
        if self._worker is not None:
            await self._worker.aclose()
            self._worker = None
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._leadership.release)
        self._engine = None
        self.ctx.leading = False
        self.ctx.metrics.leader.set(0)


async def _construct(factory: Callable[[], Any]) -> Any:
    # Called on the worker loop, so whatever the factory creates binds to that loop.
    return factory()
