"""The whole loop with the real engine: status pages (mocked) to events to a signed webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mercalli.catalog import Service
from mercalli.domain import Category
from mercalli.engine import Engine
from mercalli.notify.config import Config
from mercalli.server.scheduler import default_client
from mercalli.store import Database

from .support import wait_until

ALPHA = Service(
    id="alpha", name="Alpha", category=Category.DEVTOOLS, adapter="statuspage", base_url="https://status.alpha.test"
)
BETA = Service(
    id="beta", name="Beta", category=Category.DATA, adapter="statuspage", base_url="https://status.beta.test"
)
HOOK = "https://hooks.example.com/mercalli"
T0 = datetime.now(UTC) - timedelta(minutes=30)


def sp_incident(bodies: list[str], *, resolved: bool = False) -> dict[str, Any]:
    updates = [
        {
            "status": "resolved" if resolved and i == len(bodies) - 1 else "investigating",
            "body": b,
            "created_at": (T0 + timedelta(minutes=5 * i)).isoformat(),
            "display_at": (T0 + timedelta(minutes=5 * i)).isoformat(),
            "affected_components": [{"name": "API"}],
        }
        for i, b in enumerate(bodies)
    ]
    return {
        "id": "a1",
        "name": "API errors",
        "status": updates[-1]["status"],
        "impact": "major",
        "created_at": T0.isoformat(),
        "updated_at": updates[-1]["created_at"],
        "started_at": T0.isoformat(),
        "resolved_at": updates[-1]["created_at"] if resolved else None,
        "shortlink": "https://stspg.io/a1",
        "incident_updates": list(reversed(updates)),
        "components": [{"name": "API"}],
    }


def summary(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    status = "degraded_performance" if incidents else "operational"
    return {
        "status": {"indicator": "major" if incidents else "none", "description": "x"},
        "components": [{"id": "c1", "name": "API", "status": status, "group": False}],
        "incidents": incidents,
        "scheduled_maintenances": [],
    }


def test_status_page_changes_reach_a_signed_webhook(make_app: Callable[..., FastAPI], db: Database) -> None:
    page: dict[str, Any] = {"open": [sp_incident(["Investigating API errors."])], "recent": []}

    def engine() -> Engine:
        db.sync_services([ALPHA, BETA])
        return Engine(db, [ALPHA, BETA], ticks_per_hour=3600 / 0.3)

    config = Config.model_validate(
        {
            "public_url": "https://m.example.com",
            "stack": ["alpha"],
            "channels": [{"type": "webhook", "name": "hook", "url": HOOK, "secret": "k3y", "min_impact": "none"}],
        }
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ALPHA.base_url}/api/v2/summary.json").mock(
            side_effect=lambda _: httpx.Response(200, json=summary(page["open"]))
        )
        mock.get(f"{ALPHA.base_url}/api/v2/incidents.json").mock(
            side_effect=lambda _: httpx.Response(200, json={"incidents": page["recent"]})
        )
        mock.get(f"{BETA.base_url}/api/v2/summary.json").mock(return_value=httpx.Response(200, json=summary([])))
        quiet = httpx.Response(200, json={"incidents": []})
        mock.get(f"{BETA.base_url}/api/v2/incidents.json").mock(return_value=quiet)
        hook = mock.post(HOOK).mock(return_value=httpx.Response(200))
        app = make_app(
            scheduler=True, interval=0.3, config=config, engine_factory=engine, client_factory=default_client
        )
        with TestClient(app) as client:
            # The first tick finds the incident already open; the notifier starts after it.
            wait_until(lambda: db.kv_get("notify:cursor:hook") is not None)
            page["open"] = [sp_incident(["Investigating API errors.", "Fix deployed, monitoring."])]
            wait_until(lambda: hook.call_count >= 1)
            page["open"] = []
            page["recent"] = [
                sp_incident(["Investigating API errors.", "Fix deployed, monitoring.", "Resolved."], resolved=True)
            ]
            wait_until(lambda: hook.call_count >= 2)
            wait_until(lambda: "alpha:a1" in {d["id"] for d in client.get("/api/v1/overview.json").json()["recent"]})
            doc = client.get("/api/v1/incidents/alpha/a1.json").json()
            metrics = client.get("/metrics").text
        calls = list(hook.calls)
    events = [json.loads(c.request.content) for c in calls]
    assert [(e["event"], e["incident"]["status"]) for e in events] == [
        ("incident.updated", "investigating"),
        ("incident.resolved", "resolved"),
    ]
    for c in calls:
        digest = hmac.new(b"k3y", c.request.content, hashlib.sha256).hexdigest()
        assert c.request.headers["x-mercalli-signature"] == f"sha256={digest}"
    assert events[1]["links"] == {
        "vendor": "https://stspg.io/a1",
        "mercalli": "https://m.example.com/incident/?id=alpha:a1",
    }
    assert [u["body"] for u in doc["updates"]] == [
        "Investigating API errors.",
        "Fix deployed, monitoring.",
        "Resolved.",
    ]
    assert 'mercalli_notifications_total{channel_type="webhook",result="sent"} 2.0' in metrics
    assert 'mercalli_services_polled{result="ok"} 2.0' in metrics
