"""Delivery semantics: cursors, no duplicates, retries in order, routing rules and rate limits."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from mercalli.domain import Phase
from mercalli.notify.config import Config
from mercalli.notify.notifier import Notifier
from mercalli.store import Database, DeliveryRow

from .support import CLOUDFLARE, GITHUB, NOW, add_event

SLACK = "https://hooks.slack.com/services/T0/B0/x"
HOOK = "https://hooks.example.com/mercalli"
PD = "https://events.pagerduty.com/v2/enqueue"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def make(db: Database, config: dict[str, Any], clock: Clock | None = None, **kw: Any) -> Notifier:
    clock = clock or Clock()
    return Notifier(
        db, Config.model_validate(config), clock=lambda: NOW, sleep=clock.sleep, monotonic=clock.monotonic, **kw
    )


def slack_config(**channel: Any) -> dict[str, Any]:
    return {
        "stack": ["github", "cloudflare", "vercel"],
        "channels": [{"type": "slack", "name": "ops", "url": SLACK, **channel}],
    }


def github_update(db: Database, *bodies: str) -> None:
    """Post GitHub's original update plus these, as the vendor's page would show them."""
    extra = [
        GITHUB.updates[0].model_copy(update={"at": NOW + timedelta(minutes=i), "body": b}) for i, b in enumerate(bodies)
    ]
    db.upsert_incident(GITHUB.model_copy(update={"updates": GITHUB.updates + extra}), seen_at=NOW, seen_open=True)


def deliveries(db: Database) -> list[tuple[int, str, bool, str | None]]:
    with db.session() as s:
        rows = s.scalars(select(DeliveryRow).order_by(DeliveryRow.id))
        return [(r.event_id, r.channel, r.ok, r.detail) for r in rows]


async def test_the_first_run_starts_after_the_newest_event(db: Database) -> None:
    with respx.mock() as mock:
        notifier = make(db, slack_config())
        report = await notifier.run()
        await notifier.aclose()
    assert report.sent == 0
    assert not mock.calls
    assert db.kv_get("notify:cursor:ops") == {"event_id": 4}


async def test_new_events_are_delivered_exactly_once(db: Database) -> None:
    notifier = make(db, slack_config())
    with respx.mock() as mock:
        route = mock.post(SLACK).mock(return_value=httpx.Response(200, text="ok"))
        await notifier.run()
        github_update(db, "Mitigation is rolling out.")
        assert (await notifier.run()).sent == 1
        assert (await notifier.run()).sent == 0
        # A crash after sending but before saving the cursor must not send the event again.
        db.kv_set("notify:cursor:ops", {"event_id": 4})
        assert (await notifier.run()).sent == 0
    await notifier.aclose()
    assert route.call_count == 1
    sent = json.loads(route.calls[0].request.content)
    assert sent["blocks"][0]["text"]["text"] == "Incident update: GitHub"
    assert "Mitigation is rolling out." in sent["blocks"][1]["text"]["text"]
    assert deliveries(db) == [(5, "ops", True, "slack attempt 1: HTTP 200")]
    assert db.kv_get("notify:cursor:ops") == {"event_id": 5}


async def test_retries_back_off_then_succeed(db: Database) -> None:
    clock = Clock()
    notifier = make(db, slack_config(), clock)
    await notifier.run()
    github_update(db, "Still investigating.")
    with respx.mock() as mock:
        mock.post(SLACK).mock(side_effect=[httpx.Response(503), httpx.Response(200)])
        report = await notifier.run()
    await notifier.aclose()
    assert (report.sent, report.failed) == (1, 0)
    assert [(ok, detail) for _, _, ok, detail in deliveries(db)] == [
        (False, "slack attempt 1: HTTP 503"),
        (True, "slack attempt 2: HTTP 200"),
    ]
    assert len(clock.sleeps) == 1
    assert 0.5 <= clock.sleeps[0] <= 0.75


async def test_a_down_endpoint_holds_its_events_in_order_until_it_returns(db: Database) -> None:
    notifier = make(db, slack_config(min_impact="none"))
    await notifier.run()
    github_update(db, "First.")  # 5
    add_event(db, "incident.updated", "cloudflare", "cloudflare:cf1", {"title": "x", "impact": "major"})  # 6
    github_update(db, "First.", "Second.")  # 7, the same incident as 5
    with respx.mock() as mock:
        mock.post(SLACK).mock(return_value=httpx.Response(502))
        report = await notifier.run()
        assert (report.sent, report.failed, report.channels["ops"].deferred) == (0, 1, 3)
        assert db.kv_get("notify:cursor:ops") == {"event_id": 4}
        mock.post(SLACK).mock(return_value=httpx.Response(200))
        report = await notifier.run()
        first = json.loads(mock.calls[-2].request.content)["blocks"][1]["text"]["text"]
    await notifier.aclose()
    # 5 and 7 describe one incident and both carry its latest state, so one message covers them.
    assert (report.sent, report.channels["ops"].skipped, report.channels["ops"].cursor) == (2, 1, 7)
    assert [event for event, _, ok, _ in deliveries(db) if ok] == [5, 6]
    assert "Second." in first


async def test_permanent_errors_are_recorded_and_passed_over(db: Database) -> None:
    notifier = make(db, slack_config(min_impact="none"))
    await notifier.run()
    github_update(db, "One.")
    db.upsert_incident(
        CLOUDFLARE.model_copy(update={"title": "Elevated 5xx errors in FRA and AMS"}), seen_at=NOW, seen_open=True
    )
    add_event(
        db,
        "incident.updated",
        "cloudflare",
        "cloudflare:cf1",
        {"title": "Elevated 5xx errors in FRA and AMS", "impact": "critical", "status": "identified"},
    )
    with respx.mock() as mock:
        mock.post(SLACK).mock(side_effect=[httpx.Response(400, text="invalid_blocks"), httpx.Response(200)])
        report = await notifier.run()
    await notifier.aclose()
    assert (report.sent, report.failed) == (1, 1)
    assert deliveries(db) == [
        (5, "ops", False, "slack attempt 1: HTTP 400 invalid_blocks"),
        (6, "ops", True, "slack attempt 1: HTTP 200"),
    ]
    assert db.kv_get("notify:cursor:ops") == {"event_id": 6}


async def test_routing_by_stack_services_impact_and_kind(db: Database) -> None:
    config = {
        "stack": ["github", "cloudflare"],
        "channels": [
            {"type": "webhook", "name": "everything", "url": HOOK, "min_impact": "none"},
            {"type": "webhook", "name": "major-only", "url": HOOK, "min_impact": "major"},
            {"type": "webhook", "name": "github-only", "url": HOOK, "services": ["github"], "min_impact": "none"},
            {"type": "webhook", "name": "opened-only", "url": HOOK, "events": ["incident.opened"]},
        ],
    }
    notifier = make(db, config)
    await notifier.run()
    github_update(db, "GitHub minor update.")  # event 5
    add_event(
        db,
        "incident.updated",
        "cloudflare",
        "cloudflare:cf1",
        {"title": "x", "impact": "major", "status": "identified"},
    )  # event 6
    add_event(db, "incident.opened", "stripe", "stripe:st9", {"title": "Not our stack", "impact": "critical"})  # 7
    with respx.mock() as mock:
        mock.post(HOOK).mock(return_value=httpx.Response(204))
        report = await notifier.run()
    await notifier.aclose()
    got = {(event, channel) for event, channel, ok, _ in deliveries(db) if ok}
    assert got == {(5, "everything"), (6, "everything"), (6, "major-only"), (5, "github-only")}
    assert report.channels["opened-only"].skipped == 3


async def test_channels_hear_how_incidents_they_were_told_about_end(db: Database) -> None:
    config = {
        "stack": ["github", "cloudflare", "vercel"],
        "channels": [{"type": "webhook", "name": "major", "url": HOOK, "min_impact": "major"}],
    }
    notifier = make(db, config)
    await notifier.run()
    add_event(db, "incident.updated", "cloudflare", "cloudflare:cf1", {"title": "x", "impact": "major"})  # 5
    add_event(
        db,
        "incident.updated",
        "cloudflare",
        "cloudflare:cf1",
        {"title": "x", "impact": "minor", "status": "monitoring"},
    )  # 6: downgraded
    add_event(db, "incident.updated", "github", "github:gh1", {"title": "x", "impact": "minor"})  # 7: never told
    with respx.mock() as mock:
        mock.post(HOOK).mock(return_value=httpx.Response(204))
        await notifier.run()
    await notifier.aclose()
    assert [event for event, _, ok, _ in deliveries(db) if ok] == [5, 6]


async def test_blast_warnings_page_and_clear_with_the_upstream_incident(db: Database) -> None:
    config = {
        "stack": ["vercel"],
        "blast_threshold": 0.3,
        "channels": [
            {
                "type": "pagerduty",
                "name": "pager",
                "events": ["incident.opened", "incident.resolved", "blast"],
                "settings": {"routing_key": "R0UTE"},
            }
        ],
    }
    notifier = make(db, config)
    await notifier.run()
    weak = {"upstream_incident_id": "cloudflare:cf1", "service_id": "vercel", "probability": 0.1, "evidence": []}
    strong = {**weak, "probability": 0.42, "evidence": ["edge:cloudflare"]}
    add_event(db, "blast", "vercel", "cloudflare:cf1", weak)  # 5: under the threshold
    add_event(db, "blast", "vercel", "cloudflare:cf1", strong)  # 6
    add_event(db, "blast", "vercel", "cloudflare:cf1", strong)  # 7: same warning again
    with respx.mock() as mock:
        route = mock.post(PD).mock(return_value=httpx.Response(202, json={"status": "success"}))
        warned = await notifier.run()
        # A later tick: the upstream incident ends. Cloudflare is not in the stack, but the pager
        # was warned because of it, so it hears the all clear and the warning is resolved too.
        over = CLOUDFLARE.model_copy(update={"resolved_at": NOW + timedelta(minutes=30), "status": Phase.RESOLVED})
        db.upsert_incident(over, seen_at=NOW + timedelta(minutes=30), seen_open=False)  # 8
        cleared = await notifier.run()
    await notifier.aclose()
    sent = [json.loads(c.request.content) for c in route.calls]
    assert [(e["event_action"], e["dedup_key"]) for e in sent] == [
        ("trigger", "blast:cloudflare:cf1:vercel"),
        ("resolve", "cloudflare:cf1"),
        ("resolve", "blast:cloudflare:cf1:vercel"),
    ]
    summary = sent[0]["payload"]["summary"]
    assert summary == "May be affected: Vercel: Cloudflare incident: Elevated 5xx errors in Frankfurt"
    assert (warned.sent, warned.channels["pager"].skipped) == (1, 2)
    assert (cleared.sent, cleared.channels["pager"].skipped) == (1, 0)


async def test_a_warning_about_an_incident_that_already_ended_is_not_paged(db: Database) -> None:
    config = {
        "stack": ["vercel"],
        "channels": [
            {
                "type": "pagerduty",
                "name": "pager",
                "events": ["blast", "incident.resolved"],
                "settings": {"routing_key": "R0UTE"},
            }
        ],
    }
    notifier = make(db, config)
    await notifier.run()
    blast = {"upstream_incident_id": "cloudflare:cf1", "service_id": "vercel", "probability": 0.9, "evidence": []}
    add_event(db, "blast", "vercel", "cloudflare:cf1", blast)
    over = CLOUDFLARE.model_copy(update={"resolved_at": NOW + timedelta(minutes=30), "status": Phase.RESOLVED})
    db.upsert_incident(over, seen_at=NOW + timedelta(minutes=30), seen_open=False)
    with respx.mock() as mock:
        report = await notifier.run()
        assert not mock.calls
    await notifier.aclose()
    assert report.channels["pager"].skipped == 2


async def test_rate_limits_defer_events_to_the_next_run_in_order(db: Database) -> None:
    clock = Clock()
    notifier = make(db, slack_config(min_impact="none", rate_limit={"per_minute": 2, "burst": 1}), clock)
    await notifier.run()
    for i in range(3):
        add_event(
            db, "incident.updated", "github", "github:gh1", {"title": f"t{i}", "impact": "minor", "status": f"s{i}"}
        )
    with respx.mock() as mock:
        mock.post(SLACK).mock(return_value=httpx.Response(200))
        first = await notifier.run()
        clock.t += 30  # one more token at two a minute
        second = await notifier.run()
        clock.t += 60
        third = await notifier.run()
    await notifier.aclose()
    assert [(r.sent, r.channels["ops"].deferred) for r in (first, second, third)] == [(1, 2), (1, 1), (1, 0)]
    assert [event for event, *_ in deliveries(db)] == [5, 6, 7]


async def test_identical_updates_collapse_into_one_message(db: Database) -> None:
    notifier = make(db, slack_config())
    await notifier.run()
    payload = {"title": "Elevated errors on Actions", "impact": "minor", "status": "investigating"}
    add_event(db, "incident.updated", "github", "github:gh1", payload)
    add_event(db, "incident.updated", "github", "github:gh1", payload)
    with respx.mock() as mock:
        mock.post(SLACK).mock(return_value=httpx.Response(200))
        report = await notifier.run()
    await notifier.aclose()
    assert (report.sent, report.channels["ops"].skipped, report.channels["ops"].cursor) == (1, 1, 6)


async def test_stale_events_are_skipped_and_new_channels_start_fresh(db: Database) -> None:
    notifier = make(db, slack_config())
    await notifier.run()
    add_event(db, "incident.updated", "github", "github:gh1", {"impact": "minor"}, at=NOW - timedelta(hours=7))
    with respx.mock() as mock:
        report = await notifier.run()
        assert not mock.calls
    assert (report.sent, report.channels["ops"].skipped, report.channels["ops"].cursor) == (0, 1, 5)
    two = make(
        db,
        {
            "stack": ["github"],
            "channels": [
                {"type": "slack", "name": "ops", "url": SLACK},
                {"type": "slack", "name": "new", "url": SLACK},
            ],
        },
    )
    github_update(db, "Anything.")
    with respx.mock() as mock:
        mock.post(SLACK).mock(return_value=httpx.Response(200))
        report = await two.run()
    await two.aclose()
    await notifier.aclose()
    assert (report.channels["ops"].sent, report.channels["new"].sent) == (1, 0)
    assert db.kv_get("notify:cursor:new") == {"event_id": 6}


@pytest.mark.parametrize("bad", [None, "abc", float("nan")])
async def test_malformed_blast_payloads_are_ignored(db: Database, bad: Any) -> None:
    notifier = make(db, {"stack": ["vercel"], "channels": [{"type": "webhook", "name": "h", "url": HOOK}]})
    await notifier.run()
    add_event(db, "blast", "vercel", None, {"upstream_incident_id": "x:y", "service_id": "vercel", "probability": bad})
    with respx.mock() as mock:
        report = await notifier.run()
        assert not mock.calls
    await notifier.aclose()
    assert report.channels["h"].skipped == 1
