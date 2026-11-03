"""Server-sent events of new rows in the events table.

One pump per process follows the table and fans each new event out to every connected client, so
a thousand dashboards cost one query a second rather than a thousand. A client that reconnects
with Last-Event-ID first gets what it missed from the database, then joins the live fan-out at
exactly the event the replay stopped at.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import orjson

from mercalli.api import ApiBuilder
from mercalli.notify.events import Summaries, events_after, head_event_id, subject_incident
from mercalli.store import Database, EventRow

log = logging.getLogger("mercalli.server")

QUEUE_BATCHES = 256
BATCH = 500
MAX_REPLAY = 10_000
RETRY_MS = 5000

Frame = tuple[int, bytes]  # event id, encoded event


@dataclass(eq=False)
class Subscription:
    start: int
    queue: asyncio.Queue[list[Frame] | None] = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_BATCHES))
    closed: bool = False

    def offer(self, batch: list[Frame] | None) -> None:
        if self.closed:
            return
        if batch is None:
            self._end()
            return
        try:
            self.queue.put_nowait(batch)
        except asyncio.QueueFull:
            # A client this far behind is better off reconnecting and replaying from the database.
            self._end()

    def _end(self) -> None:
        self.closed = True
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(None)


class EventHub:
    def __init__(self, db: Database, builder: Callable[[], ApiBuilder], *, poll: float, max_clients: int) -> None:
        self.db = db
        self.builder = builder
        self.poll = poll
        self.max_clients = max_clients
        self._subs: set[Subscription] = set()
        self._head: int | None = None
        self._lock: asyncio.Lock | None = None
        self._wake: asyncio.Event | None = None
        self._closed = False

    @property
    def clients(self) -> int:
        return len(self._subs)

    @property
    def full(self) -> bool:
        return len(self._subs) >= self.max_clients

    def _sync(self) -> tuple[asyncio.Lock, asyncio.Event]:
        # Created on first use so they belong to the loop that serves requests.
        if self._lock is None or self._wake is None:
            self._lock, self._wake = asyncio.Lock(), asyncio.Event()
        return self._lock, self._wake

    async def run(self) -> None:
        _, wake = self._sync()
        failures = 0
        while not self._closed:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), self.poll)
            wake.clear()
            if self._closed:
                return
            try:
                await self._pump()
                failures = 0
            except Exception as exc:  # the database may be briefly away; clients keep their connections
                failures += 1
                if failures in (1, 10) or failures % 100 == 0:
                    log.warning("live stream could not read events (%d in a row): %s", failures, exc)

    def poke(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def _pump(self) -> None:
        lock, _ = self._sync()
        async with lock:
            if not self._subs:
                self._head = None  # the next subscriber starts from whatever is newest then
                return
            if self._head is None:
                self._head = await asyncio.to_thread(head_event_id, self.db)
                return
            rows = await asyncio.to_thread(events_after, self.db, self._head, limit=BATCH)
            if not rows:
                return
            frames = await asyncio.to_thread(self.frames, rows)
            self._head = rows[-1].id
            for sub in list(self._subs):
                sub.offer(frames)

    async def subscribe(self) -> Subscription:
        lock, _ = self._sync()
        async with lock:
            if self._head is None:
                self._head = await asyncio.to_thread(head_event_id, self.db)
            sub = Subscription(start=self._head)
            self._subs.add(sub)
        if self._closed:
            sub.offer(None)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    def close(self) -> None:
        """End every stream, so shutdown does not wait on connections that never finish."""
        self._closed = True
        for sub in list(self._subs):
            sub.offer(None)
        self.poke()

    def frames(self, rows: list[EventRow]) -> list[Frame]:
        summaries = Summaries(self.db, self.builder())
        out: list[Frame] = []
        for ev in rows:
            iid = subject_incident(ev)
            data = summaries.get(iid) or {
                "id": iid,
                "service_id": ev.service_id,
                "service_name": summaries.service_name(ev.service_id),
                "event": ev.kind,
                "payload": ev.payload,
            }
            kind = "".join(ch for ch in ev.kind if ch not in "\r\n").encode()
            data_json = orjson.dumps(data, option=orjson.OPT_NON_STR_KEYS)
            out.append((ev.id, b"id: %d\nevent: %s\ndata: %s\n\n" % (ev.id, kind, data_json)))
        return out

    async def replay(self, after: int, upto: int) -> AsyncIterator[Frame]:
        sent = 0
        while after < upto and sent < MAX_REPLAY:
            rows = await asyncio.to_thread(events_after, self.db, after, limit=BATCH, upto=upto)
            if not rows:
                return
            for frame in await asyncio.to_thread(self.frames, rows):
                yield frame
            after = rows[-1].id
            sent += len(rows)


def parse_last_event_id(value: str | None) -> int | None:
    if not value:
        return None
    try:
        n = int(value.strip())
    except ValueError:
        return None
    return n if n >= 0 else None


async def event_stream(hub: EventHub, last_id: int | None, *, heartbeat: float) -> AsyncIterator[bytes]:
    sub = await hub.subscribe()
    try:
        yield b"retry: %d\n\n" % RETRY_MS
        if last_id is not None and last_id < sub.start:
            async for _, frame in hub.replay(last_id, sub.start):
                yield frame
        while True:
            try:
                batch = await asyncio.wait_for(sub.queue.get(), heartbeat)
            except TimeoutError:
                # A comment line keeps proxies from closing a quiet connection.
                yield b": keep-alive\n\n"
                continue
            if batch is None:
                return
            for event_id, frame in batch:
                # Another replica's pump may run a little ahead of this one; never repeat an event.
                if last_id is None or event_id > last_id:
                    yield frame
    finally:
        hub.unsubscribe(sub)
