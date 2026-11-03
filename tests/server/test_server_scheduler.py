"""The background loop: timing, failures, timeouts, graceful shutdown, notify and prune."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mercalli.notify.config import Config
from mercalli.store import Database, PollRow

from .support import GITHUB, NOW, FakeEngine, make_incident, wait_until


def test_ticks_repeat_and_never_overlap(make_app: Callable[..., FastAPI], db: Database) -> None:
    engine = FakeEngine(durations=[0.12])  # each tick outlasts the 0.05s interval
    with TestClient(make_app(scheduler=True, interval=0.05, engine_factory=lambda: engine)):
        wait_until(lambda: engine.finished >= 4)
    assert engine.max_active == 1


def test_a_failing_tick_is_logged_and_the_loop_goes_on(
    make_app: Callable[..., FastAPI], db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    engine = FakeEngine(fail_first=1)
    with (
        caplog.at_level(logging.ERROR, logger="mercalli.server"),
        TestClient(make_app(scheduler=True, engine_factory=lambda: engine)) as client,
    ):
        wait_until(lambda: engine.finished >= 1)
        wait_until(lambda: client.get("/readyz").status_code == 200)
        metrics = client.get("/metrics").text
    assert any("tick failed" in r.getMessage() for r in caplog.records)
    assert 'mercalli_ticks_total{result="error"} 1.0' in metrics


def test_a_hung_tick_is_cancelled_after_the_timeout(make_app: Callable[..., FastAPI], db: Database) -> None:
    engine = FakeEngine(durations=[30.0, 0.0])
    with TestClient(make_app(scheduler=True, tick_timeout=0.3, engine_factory=lambda: engine)) as client:
        wait_until(lambda: engine.finished >= 1)
        assert 'mercalli_ticks_total{result="timeout"} 1.0' in client.get("/metrics").text
    assert engine.cancelled == 1


def test_shutdown_lets_a_running_tick_finish(make_app: Callable[..., FastAPI], db: Database) -> None:
    engine = FakeEngine(durations=[0.8])
    with TestClient(make_app(scheduler=True, engine_factory=lambda: engine)):
        wait_until(lambda: engine.active == 1)
    assert (engine.finished, engine.cancelled) == (1, 0)


def test_shutdown_cancels_a_tick_past_the_grace_period(make_app: Callable[..., FastAPI], db: Database) -> None:
    engine = FakeEngine(durations=[60.0])
    started = time.monotonic()
    with TestClient(make_app(scheduler=True, shutdown_grace=0.3, engine_factory=lambda: engine)):
        wait_until(lambda: engine.active == 1)
    assert time.monotonic() - started < 10
    assert (engine.finished, engine.cancelled) == (0, 1)


def test_each_cycle_notifies_then_refreshes(make_app: Callable[..., FastAPI], db: Database) -> None:
    def on_tick(n: int) -> None:
        if n == 2:  # an update arrives after the notifier has taken its starting point
            update = GITHUB.model_copy(
                update={
                    "updates": [
                        *GITHUB.updates,
                        GITHUB.updates[0].model_copy(update={"at": NOW, "body": "We have identified the cause."}),
                    ]
                }
            )
            db.upsert_incident(update, seen_at=NOW, seen_open=True)

    engine = FakeEngine(on_tick=on_tick)
    config = Config.model_validate(
        {
            "public_url": "https://mercalli.example.com",
            "stack": ["github"],
            "channels": [{"type": "webhook", "name": "hook", "url": "https://hooks.example.com/m", "secret": "s3cret"}],
        }
    )
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://hooks.example.com/m").mock(return_value=httpx.Response(204))
        with TestClient(make_app(scheduler=True, config=config, engine_factory=lambda: engine)) as client:
            wait_until(lambda: route.call_count >= 1)

            def refreshed() -> bool:
                active = client.get("/api/v1/overview.json").json()["active"]
                github = next(d for d in active if d["id"] == "github:gh1")
                return github["latest"]["body"] == "We have identified the cause."

            wait_until(refreshed)
    request = route.calls[0].request
    expected = "sha256=" + hmac.new(b"s3cret", request.content, hashlib.sha256).hexdigest()
    assert request.headers["x-mercalli-signature"] == expected
    body = json.loads(request.content)
    assert (body["event"], body["incident"]["id"]) == ("incident.updated", "github:gh1")
    assert body["links"]["mercalli"] == "https://mercalli.example.com/incident/?id=github:gh1"
    assert route.call_count == 1


def test_prune_runs_once_a_day(make_app: Callable[..., FastAPI], db: Database) -> None:
    old = datetime.now(UTC) - timedelta(days=30)

    def old_polls() -> int:
        with db.session() as s:
            cutoff = datetime.now(UTC) - timedelta(days=8)
            return int(s.scalar(select(func.count()).where(PollRow.at < cutoff)) or 0)

    with db.session() as s:
        s.add(PollRow(service_id="github", at=old, ok=True, latency_ms=10.0, health="operational", open_incidents=0))
    assert old_polls() == 1
    engine = FakeEngine()
    with TestClient(make_app(scheduler=True, engine_factory=lambda: engine)):
        wait_until(lambda: db.kv_get("server:last_prune") is not None)
        assert old_polls() == 0
        with db.session() as s:
            s.add(PollRow(service_id="github", at=old, ok=True, latency_ms=1.0, health="operational", open_incidents=0))
        calls = engine.calls
        wait_until(lambda: engine.finished >= calls + 2)
        assert old_polls() == 1  # not due again for a day


def test_a_follower_serves_what_the_leader_wrote(make_app: Callable[..., FastAPI], db: Database) -> None:
    with TestClient(make_app(scheduler=False)) as client:
        assert client.get("/api/v1/meta.json").json()["tick"] is None
        report = {
            "started_at": NOW.isoformat(),
            "duration_s": 12.5,
            "polled": 4,
            "ok": 4,
            "failed": 0,
            "changes": {},
            "refreshed": 0,
            "models": {},
        }
        db.upsert_incident(
            make_incident("stripe", "st2", title="API latency", created=NOW, bodies=["Investigating."]),
            seen_at=NOW,
            seen_open=True,
        )
        db.kv_set("server:last_tick", {"report": report, "finished_at": datetime.now(UTC).isoformat()})
        wait_until(lambda: client.get("/api/v1/meta.json").json()["tick"] == report)
        assert "stripe:st2" in {d["id"] for d in client.get("/api/v1/overview.json").json()["active"]}
        assert client.get("/readyz").json()["leader"] is False
