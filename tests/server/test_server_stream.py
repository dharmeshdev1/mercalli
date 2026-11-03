"""The live stream, against a real uvicorn server so responses really stream."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from datetime import timedelta

import httpx
from fastapi import FastAPI
from httpx_sse import ServerSentEvent, connect_sse

from mercalli.api import ApiBuilder
from mercalli.notify.events import Summaries
from mercalli.store import Database

from .support import CLOUDFLARE, GITHUB, NOW, add_event, serve_in_thread


def _events(source: Iterator[ServerSentEvent], n: int) -> list[ServerSentEvent]:
    """The next n events that carry data, skipping the opening retry hint."""
    out = []
    for sse in source:
        if sse.data:
            out.append(sse)
            if len(out) == n:
                break
    return out


def _update(db: Database) -> None:
    update = GITHUB.updates[0].model_copy(update={"at": NOW, "body": "Mitigation in progress."})
    db.upsert_incident(GITHUB.model_copy(update={"updates": [*GITHUB.updates, update]}), seen_at=NOW, seen_open=True)


def test_new_events_arrive_with_id_kind_and_incident_summary(make_app: Callable[..., FastAPI], db: Database) -> None:
    with (
        serve_in_thread(make_app()) as base,
        httpx.Client(timeout=10) as client,
        connect_sse(client, "GET", f"{base}/api/v1/stream") as source,
    ):
        assert source.response.headers["content-type"].startswith("text/event-stream")
        assert source.response.headers["x-accel-buffering"] == "no"
        events = source.iter_sse()
        hello = next(events)
        assert hello.retry == 5000
        _update(db)
        add_event(db, "forecast", "cloudflare", "cloudflare:cf1", {"p10": 5, "p50": 20, "p90": 60})
        got = _events(events, 2)
    expected = Summaries(db, ApiBuilder(db, now=NOW, edition="self-hosted"))
    assert [e.event for e in got] == ["incident.updated", "forecast"]
    assert [int(e.id) for e in got] == [5, 6]
    assert json.loads(got[0].data) == expected.get("github:gh1")
    assert json.loads(got[1].data) == expected.get("cloudflare:cf1")


def test_last_event_id_replays_what_was_missed(make_app: Callable[..., FastAPI], db: Database) -> None:
    with serve_in_thread(make_app()) as base, httpx.Client(timeout=10) as client:
        with connect_sse(client, "GET", f"{base}/api/v1/stream", headers={"Last-Event-ID": "1"}) as source:
            replayed = _events(source.iter_sse(), 3)
        assert [(int(e.id), e.event) for e in replayed] == [
            (2, "incident.opened"),
            (3, "incident.opened"),
            (4, "incident.resolved"),
        ]
        assert json.loads(replayed[0].data)["id"] == CLOUDFLARE.id
        with connect_sse(client, "GET", f"{base}/api/v1/stream?last_event_id=3") as source:
            assert [int(e.id) for e in _events(source.iter_sse(), 1)] == [4]


def test_quiet_streams_get_heartbeats(make_app: Callable[..., FastAPI], db: Database) -> None:
    with (
        serve_in_thread(make_app(stream_heartbeat=0.2)) as base,
        httpx.Client(timeout=10) as client,
        client.stream("GET", f"{base}/api/v1/stream") as resp,
    ):
        lines = resp.iter_lines()
        assert next(line for line in lines if line.startswith(":")) == ": keep-alive"


def test_events_without_an_incident_still_stream(make_app: Callable[..., FastAPI], db: Database) -> None:
    payload = {"upstream_incident_id": "aws:gone", "service_id": "vercel", "probability": 0.5, "evidence": []}
    with (
        serve_in_thread(make_app()) as base,
        httpx.Client(timeout=10) as client,
        connect_sse(client, "GET", f"{base}/api/v1/stream") as source,
    ):
        events = source.iter_sse()
        next(events)
        add_event(db, "blast", "vercel", None, payload, at=NOW + timedelta(minutes=1))
        (got,) = _events(events, 1)
    data = json.loads(got.data)
    assert (got.event, data["id"], data["payload"]["service_id"]) == ("blast", "aws:gone", "vercel")


def test_shutdown_ends_open_streams(make_app: Callable[..., FastAPI], db: Database) -> None:
    app = make_app()
    ended = threading.Event()
    with serve_in_thread(app) as base, httpx.Client(timeout=10) as client:

        def listen() -> None:
            with connect_sse(client, "GET", f"{base}/api/v1/stream") as source:
                for _ in source.iter_sse():
                    pass
            ended.set()

        listener = threading.Thread(target=listen, daemon=True)
        listener.start()
        assert not ended.wait(0.5)
        app.state.ctx.begin_shutdown()
        assert ended.wait(5)
