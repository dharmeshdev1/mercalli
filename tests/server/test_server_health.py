"""Liveness, readiness and Prometheus metrics."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mercalli.store import Database

from .support import FakeEngine, wait_until


def test_healthz_answers_while_the_database_is_away(make_app: Callable[..., FastAPI], tmp_path: Path) -> None:
    app = make_app(db_url=f"sqlite:///{tmp_path}/no/such/dir/x.db")
    with TestClient(app) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        ready = client.get("/readyz")
        assert ready.status_code == 503
        assert ready.json()["database"].startswith("error")
        assert ready.headers["cache-control"] == "no-store"


def test_readyz_waits_for_the_first_tick(make_app: Callable[..., FastAPI], db: Database) -> None:
    gate = threading.Event()
    engine = FakeEngine(gate=gate)
    with TestClient(make_app(scheduler=True, engine_factory=lambda: engine)) as client:
        first = client.get("/readyz")
        assert first.status_code == 503
        assert first.json()["database"] == "ok"
        assert first.json()["ticked"] is False
        gate.set()
        wait_until(lambda: client.get("/readyz").status_code == 200)
        body = client.get("/readyz").json()
        assert body["leader"] is True
        assert body["ticks"] >= 1


def test_readyz_without_a_scheduler_needs_only_the_database(make_app: Callable[..., FastAPI], db: Database) -> None:
    with TestClient(make_app(scheduler=False)) as client:
        assert client.get("/readyz").status_code == 200


def test_metrics_cover_ticks_requests_incidents_and_streams(make_app: Callable[..., FastAPI], db: Database) -> None:
    engine = FakeEngine()
    with TestClient(make_app(scheduler=True, engine_factory=lambda: engine)) as client:
        wait_until(lambda: engine.finished >= 1)
        wait_until(lambda: 'mercalli_ticks_total{result="ok"}' in client.get("/metrics").text)
        client.get("/api/v1/services/github.json")
        client.get("/api/v1/services/nope.json")
        text = client.get("/metrics").text
    route = 'method="GET",route="/api/v1/services/{service_id}.json"'
    assert f'mercalli_http_requests_total{{{route},status="200"}} 1.0' in text
    assert f'mercalli_http_requests_total{{{route},status="404"}} 1.0' in text
    assert f"mercalli_http_request_duration_seconds_count{{{route}}} 2.0" in text
    assert 'mercalli_services_polled{result="failed"} 1.0' in text
    assert 'mercalli_open_incidents{impact="major"} 1.0' in text
    assert 'mercalli_open_incidents{impact="minor"} 1.0' in text
    assert 'mercalli_incident_changes_total{kind="incident.updated"}' in text
    assert 'mercalli_notifications_total{channel_type="slack",result="failed"} 0.0' in text
    assert "mercalli_tick_duration_seconds_bucket" in text
    assert "mercalli_stream_clients 0.0" in text
    assert 'mercalli_build_info{edition="self-hosted"' in text
