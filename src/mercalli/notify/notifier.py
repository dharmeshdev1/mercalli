"""Deliver new events to every configured channel, exactly once and in order.

Each channel keeps its own cursor (the last event id it has dealt with) in the kv table, so one
broken endpoint never holds the others back. A channel stops at the first event it could not
deliver and tries it again on the next run, which keeps its messages in order. Every attempt is
written to the deliveries table, and that table is also what prevents a second send after a crash
between delivering and saving the cursor.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, or_, select

from mercalli.api import ApiBuilder
from mercalli.domain import utcnow
from mercalli.http import USER_AGENT
from mercalli.notify.channels import Outcome, Transport, transport_for
from mercalli.notify.config import EVENT_KINDS, IMPACT_RANK, ChannelBase, Config
from mercalli.notify.events import Summaries, events_after, head_event_id, subject_incident
from mercalli.notify.message import Message, as_float, build_message
from mercalli.store import Database, DeliveryRow, EventRow

log = logging.getLogger("mercalli.notify")

CURSOR_PREFIX = "notify:cursor:"
SEEN_PREFIX = "notify:seen:"
SEEN_TTL = timedelta(days=7)
SEEN_MAX = 1000
MAX_RETRY_AFTER = 30.0  # a longer wait than this is better spent on the next run


class TokenBucket:
    def __init__(self, per_minute: float, burst: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.rate = per_minute / 60.0
        self.capacity = float(burst)
        self.tokens = float(burst)
        self.clock = clock
        self.stamp = clock()

    def take(self) -> bool:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


@dataclass
class ChannelReport:
    type: str
    cursor: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    deferred: int = 0


@dataclass
class NotifyReport:
    channels: dict[str, ChannelReport] = field(default_factory=dict)

    @property
    def sent(self) -> int:
        return sum(c.sent for c in self.channels.values())

    @property
    def failed(self) -> int:
        return sum(c.failed for c in self.channels.values())

    def by_type(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for rep in self.channels.values():
            row = out.setdefault(rep.type, {"sent": 0, "failed": 0})
            row["sent"] += rep.sent
            row["failed"] += rep.failed
        return out

    def as_dict(self) -> dict[str, Any]:
        return {name: vars(rep).copy() for name, rep in self.channels.items()}


@dataclass(frozen=True)
class History:
    told: bool  # this channel already delivered something about the incident
    blast_targets: tuple[str, ...]  # services it was warned about because of the incident


class Notifier:
    def __init__(
        self,
        db: Database,
        config: Config,
        *,
        data_dir: Path | None = None,
        builder_factory: Callable[[datetime], ApiBuilder] | None = None,
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        batch: int = 500,
        attempts_per_run: int = 3,
        max_attempts: int = 12,
    ) -> None:
        self.db = db
        self.config = config
        self.builder_factory = builder_factory or (
            lambda now: ApiBuilder(db, data_dir=data_dir, now=now, edition="self-hosted")
        )
        self.clock = clock
        self.sleep = sleep
        self.monotonic = monotonic
        self.batch = batch
        self.attempts_per_run = attempts_per_run
        self.max_attempts = max_attempts
        self._http = http
        self._owns_http = http is None
        self._transports: dict[str, Transport] = {}
        self._buckets: dict[str, TokenBucket] = {}

    async def aclose(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None
        self._transports.clear()

    async def run(self) -> NotifyReport:
        """Deliver everything new since each channel's cursor. Safe to call after every tick."""
        report = NotifyReport()
        channels = self.config.active_channels
        if not channels:
            return report
        now = self.clock()
        head = head_event_id(self.db)
        for ch in channels:
            report.channels[ch.name] = ChannelReport(type=ch.type, cursor=self._cursor(ch.name, head))
        low = min(rep.cursor for rep in report.channels.values())
        events = events_after(self.db, low, limit=self.batch) if low < head else []
        if not events:
            return report
        summaries = Summaries(self.db, self.builder_factory(now))
        await asyncio.gather(
            *(
                self._run_channel(
                    ch,
                    [e for e in events if e.id > report.channels[ch.name].cursor],
                    summaries,
                    report.channels[ch.name],
                    now,
                )
                for ch in channels
            )
        )
        return report

    # Per channel ----------------------------------------------------------------------------------

    async def _run_channel(
        self, ch: ChannelBase, events: list[EventRow], summaries: Summaries, rep: ChannelReport, now: datetime
    ) -> None:
        transport = self._transport(ch)
        bucket = self._buckets.get(ch.name)
        if bucket is None:
            bucket = self._buckets[ch.name] = TokenBucket(ch.rate.per_minute, ch.rate.burst, self.monotonic)
        seen = self._load_seen(ch.name, now)
        start_cursor, start_seen = rep.cursor, len(seen)
        max_age = timedelta(minutes=self.config.max_event_age_minutes)
        try:
            for i, ev in enumerate(events):
                msg = self._message(ch, ev, summaries, now)
                if msg is None or not transport.wants(msg) or msg.dedupe_key in seen or self._delivered(ev.id, ch.name):
                    rep.skipped += 1
                    rep.cursor = ev.id
                    continue
                if now - ev.at > max_age:
                    log.warning("channel %s skips event %d: it is older than %s", ch.name, ev.id, max_age)
                    rep.skipped += 1
                    rep.cursor = ev.id
                    continue
                if not bucket.take():
                    rep.deferred = len(events) - i
                    log.info(
                        "channel %s reached its rate limit; %d events wait for the next run", ch.name, rep.deferred
                    )
                    break
                outcome = await self._deliver(ch, transport, ev, msg)
                if outcome.ok:
                    rep.sent += 1
                    seen[msg.dedupe_key] = now
                    rep.cursor = ev.id
                    continue
                rep.failed += 1
                if outcome.retryable and self._attempts(ev.id, ch.name) < self.max_attempts:
                    rep.deferred = len(events) - i
                    log.warning(
                        "channel %s could not deliver event %d (%s); it tries again next run",
                        ch.name,
                        ev.id,
                        outcome.detail,
                    )
                    break
                log.error("channel %s gave up on event %d: %s", ch.name, ev.id, outcome.detail)
                rep.cursor = ev.id
        except Exception:  # one broken channel must not stop the others
            log.exception("channel %s failed", ch.name)
        finally:
            if rep.cursor != start_cursor or len(seen) != start_seen:
                self._save(ch.name, rep.cursor, seen)

    def _message(self, ch: ChannelBase, ev: EventRow, summaries: Summaries, now: datetime) -> Message | None:
        """The message this channel should get for an event, or None when it is not for this channel."""
        if ev.kind not in EVENT_KINDS or ev.kind not in ch.events:
            return None
        payload = ev.payload or {}
        allowed = set(ch.services if ch.services is not None else self.config.stack)
        incident_id = subject_incident(ev)
        summary = summaries.get(incident_id)
        history: History | None = None
        if ev.kind == "blast":
            target = str(payload.get("service_id") or "")
            probability = as_float(payload.get("probability"))
            if target not in allowed or probability is None or probability < self.config.blast_threshold:
                return None
        else:
            impact = str(payload.get("impact") or (summary or {}).get("impact") or "none")
            relevant = ev.service_id in allowed and IMPACT_RANK.get(impact, 0) >= IMPACT_RANK[ch.min_impact]
            if not relevant:
                # Follow up on anything this channel was told about, even below its impact bar or
                # outside its stack: a warning about an upstream incident deserves its all clear.
                if ev.kind == "incident.opened" or not incident_id:
                    return None
                history = self._history(ch.name, incident_id)
                if not history.told:
                    return None
        msg = build_message(ev, summary, name_of=summaries.service_name, public_url=self.config.public_url, now=now)
        if ev.kind == "incident.resolved" and ch.type == "pagerduty" and incident_id:
            history = history or self._history(ch.name, incident_id)
            keys = tuple(f"blast:{incident_id}:{target}" for target in history.blast_targets)
            msg = replace(msg, resolve_keys=keys)
        return msg

    async def _deliver(self, ch: ChannelBase, transport: Transport, ev: EventRow, msg: Message) -> Outcome:
        outcome = Outcome(False, "not attempted", retryable=True)
        for attempt in range(1, self.attempts_per_run + 1):
            try:
                outcome = await transport.send(msg)
            except Exception as exc:  # a formatting bug must not take the notifier down
                log.exception("channel %s could not build or send event %d", ch.name, ev.id)
                outcome = Outcome(False, f"error {type(exc).__name__}")
            self._record(ev.id, ch, attempt, outcome)
            if outcome.ok or not outcome.retryable or attempt == self.attempts_per_run:
                return outcome
            delay = self._backoff(attempt, outcome.retry_after)
            if delay is None:
                return outcome
            await self.sleep(delay)
        return outcome

    @staticmethod
    def _backoff(attempt: int, retry_after: float | None) -> float | None:
        base = min(8.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.25)
        if retry_after is None:
            return base
        return None if retry_after > MAX_RETRY_AFTER else max(base, retry_after)

    def _transport(self, ch: ChannelBase) -> Transport:
        transport = self._transports.get(ch.name)
        if transport is None:
            if self._http is None:
                # Created here, inside the loop that runs deliveries, which is the loop it must use.
                self._http = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=False)
            transport = self._transports[ch.name] = transport_for(ch, self._http)
        return transport

    # Storage --------------------------------------------------------------------------------------

    def _cursor(self, name: str, head: int) -> int:
        state = self.db.kv_get(CURSOR_PREFIX + name)
        if state is None:
            # A new channel starts at the newest event. It should hear about what happens next, not
            # about every incident that was already open when it was set up.
            self.db.kv_set(CURSOR_PREFIX + name, {"event_id": head})
            log.info("channel %s starts after event %d", name, head)
            return head
        return int(state.get("event_id", 0))

    def _load_seen(self, name: str, now: datetime) -> dict[str, datetime]:
        state = self.db.kv_get(SEEN_PREFIX + name) or {}
        out: dict[str, datetime] = {}
        for key, stamp in (state.get("keys") or {}).items():
            try:
                at = datetime.fromisoformat(stamp)
            except (TypeError, ValueError):
                continue
            if now - at < SEEN_TTL:
                out[key] = at
        return out

    def _save(self, name: str, cursor: int, seen: dict[str, datetime]) -> None:
        newest = sorted(seen.items(), key=lambda kv: kv[1])[-SEEN_MAX:]
        self.db.kv_set(CURSOR_PREFIX + name, {"event_id": cursor})
        self.db.kv_set(SEEN_PREFIX + name, {"keys": {k: at.isoformat() for k, at in newest}})

    def _record(self, event_id: int, ch: ChannelBase, attempt: int, outcome: Outcome) -> None:
        detail = f"{ch.type} attempt {attempt}: {outcome.detail}"
        with self.db.session() as s:
            s.add(DeliveryRow(event_id=event_id, channel=ch.name, at=self.clock(), ok=outcome.ok, detail=detail[:1000]))

    def _delivered(self, event_id: int, channel: str) -> bool:
        with self.db.session() as s:
            q = select(DeliveryRow.id).where(
                DeliveryRow.event_id == event_id, DeliveryRow.channel == channel, DeliveryRow.ok.is_(True)
            )
            return s.scalar(q.limit(1)) is not None

    def _attempts(self, event_id: int, channel: str) -> int:
        with self.db.session() as s:
            q = select(func.count()).where(DeliveryRow.event_id == event_id, DeliveryRow.channel == channel)
            return int(s.scalar(q) or 0)

    def _history(self, channel: str, incident_id: str) -> History:
        with self.db.session() as s:
            rows = s.execute(
                select(EventRow.kind, EventRow.incident_id, EventRow.payload)
                .join(DeliveryRow, DeliveryRow.event_id == EventRow.id)
                .where(
                    DeliveryRow.channel == channel,
                    DeliveryRow.ok.is_(True),
                    or_(EventRow.incident_id == incident_id, EventRow.kind == "blast"),
                )
            ).all()
        told = False
        targets: set[str] = set()
        for kind, iid, payload in rows:
            if kind == "blast":
                p = payload or {}
                if (p.get("upstream_incident_id") or iid) == incident_id:
                    told = True
                    targets.add(str(p.get("service_id")))
            elif iid == incident_id:
                told = True
        return History(told=told, blast_targets=tuple(sorted(targets)))
