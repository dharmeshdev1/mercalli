from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import timedelta

import httpx
import respx

from mercalli.api import ApiBuilder
from mercalli.domain import Impact, Incident, Phase, Update, utcnow
from mercalli.engine import Engine
from mercalli.http import PoliteClient
from mercalli.publish import publish
from tests.conftest import load

SUMMARY = "https://www.githubstatus.com/api/v2/summary.json"
RECENT = "https://www.githubstatus.com/api/v2/incidents.json"


def _summary_without_incidents() -> dict:
    data = load("statuspage_summary.json")
    data["incidents"] = []  # type: ignore[index]
    return data  # type: ignore[return-value]


@respx.mock
async def test_incident_lifecycle_open_then_resolved(db, github):
    db.sync_services([github])
    engine = Engine(db, [github])
    summary = load("statuspage_summary.json")
    respx.get(SUMMARY).mock(return_value=httpx.Response(200, json=summary))
    # While the incident is open, the vendor's recent list agrees with its summary.
    respx.get(RECENT).mock(return_value=httpx.Response(200, json={"incidents": summary["incidents"]}))  # type: ignore[index]
    async with PoliteClient(retries=0) as c:
        r1 = await engine.tick(c)
        assert r1.changes["incident.opened"] == 1
        assert [i.id for i in db.open_incidents()] == ["github:inc1"]

        r2 = await engine.tick(c)
        assert r2.changes.get("incident.opened", 0) == 0, "a second identical tick changes nothing"

        respx.get(SUMMARY).mock(return_value=httpx.Response(200, json=_summary_without_incidents()))
        respx.get(RECENT).mock(return_value=httpx.Response(200, json=load("statuspage_incidents.json")))
        r3 = await engine.tick(c)
    assert r3.changes["incident.resolved"] == 1
    assert db.open_incidents() == []
    doc = ApiBuilder(db).incident_doc("github:inc1")
    assert doc is not None
    assert doc["resolved_at"] == "2026-11-03T11:00:00Z", "the vendor's resolution time, not ours"
    assert doc["resolved_inferred"] is False
    assert len(doc["updates"]) == 3


@respx.mock
async def test_resolution_is_inferred_when_vendor_history_is_unavailable(db, github):
    db.sync_services([github])
    engine = Engine(db, [github])
    respx.get(SUMMARY).mock(return_value=httpx.Response(200, json=load("statuspage_summary.json")))
    respx.get(RECENT).mock(return_value=httpx.Response(500))
    async with PoliteClient(retries=0) as c:
        await engine.tick(c)
        respx.get(SUMMARY).mock(return_value=httpx.Response(200, json=_summary_without_incidents()))
        await engine.tick(c)
    doc = ApiBuilder(db).incident_doc("github:inc1")
    assert doc is not None and doc["resolved_inferred"] is True


@respx.mock
async def test_failed_poll_does_not_resolve_anything(db, github):
    db.sync_services([github])
    engine = Engine(db, [github])
    respx.get(SUMMARY).mock(return_value=httpx.Response(200, json=load("statuspage_summary.json")))
    respx.get(RECENT).mock(return_value=httpx.Response(200, json={"incidents": []}))
    async with PoliteClient(retries=0) as c:
        await engine.tick(c)
        respx.get(SUMMARY).mock(return_value=httpx.Response(502))
        report = await engine.tick(c)
    assert report.failed == {"github": "HTTP 502"}
    assert len(db.open_incidents()) == 1


def _incident(sid: str, src: str, impact: Impact, age: timedelta, status: Phase = Phase.INVESTIGATING) -> Incident:
    start = utcnow() - age
    return Incident(
        service_id=sid,
        source_id=src,
        title=f"{sid} {src}",
        impact=impact,
        status=status,
        created_at=start,
        updates=[Update(at=start, status=status, body="Looking into it.")],
    )


def test_overview_separates_live_long_running_and_notices(db, github):
    db.sync_services([github])
    now = utcnow()
    for inc in (
        _incident("github", "live", Impact.MAJOR, timedelta(minutes=30)),
        _incident("github", "stale", Impact.MINOR, timedelta(days=30)),
        _incident("github", "advisory", Impact.NONE, timedelta(hours=2)),
    ):
        db.upsert_incident(inc, seen_at=now, seen_open=True)
    ov = ApiBuilder(db).overview()
    assert [a["id"] for a in ov["active"]] == ["github:live"]
    assert [a["id"] for a in ov["long_running"]] == ["github:stale"]
    assert [a["id"] for a in ov["notices"]] == ["github:advisory"]
    assert ov["intensity"]["score"] == 2.0, "one major incident weighs 2"
    assert ov["intensity"]["numeral"] == "I"
    sg = ov["seismograph"]
    assert sg["points"] == len(sg["total"]) == 289
    assert sg["total"][-1] >= 2.0 + 1.0 + 0.5


def test_publish_writes_api_and_valid_feeds(db, github, tmp_path):
    db.sync_services([github])
    db.upsert_incident(
        _incident("github", "live", Impact.MAJOR, timedelta(minutes=30)), seen_at=utcnow(), seen_open=True
    )
    out = tmp_path / "site"
    stats = publish(ApiBuilder(db), out)
    assert stats["files"] > 4
    overview = json.loads((out / "api/v1/overview.json").read_text())
    assert overview["active"][0]["path"] == "incidents/github/live.json"
    assert (out / "api/v1/incidents/github/live.json").exists()
    assert (out / "api/v1/services/github.json").exists()
    root = ET.fromstring((out / "feeds/github.xml").read_text())
    assert root.tag.endswith("feed") and len(root.findall("{http://www.w3.org/2005/Atom}entry")) == 1
    # Publishing again swaps the tree atomically and leaves no staging directory behind.
    publish(ApiBuilder(db), out)
    assert not (tmp_path / "site.staging").exists()


def test_prune_keeps_incidents_and_drops_what_expires(db, github):
    from mercalli.store import BlastRow, EventRow, PollRow, PredictionRow

    db.sync_services([github])
    old = utcnow() - timedelta(days=90)
    gone = _incident("github", "gone", Impact.MAJOR, timedelta(days=91))
    gone.resolved_at, gone.status = old, Phase.RESOLVED
    db.upsert_incident(gone, seen_at=old, seen_open=False)
    db.upsert_incident(
        _incident("github", "live", Impact.MINOR, timedelta(minutes=5)), seen_at=utcnow(), seen_open=True
    )
    with db.session() as s:
        s.add(PollRow(service_id="github", at=old, ok=True, health="operational"))
        s.add(EventRow(at=old, kind="incident.opened", service_id="github", incident_id="github:gone"))
        s.add(
            BlastRow(
                upstream_incident_id="github:gone", made_at=old, model_version="t", service_id="x", probability=0.5
            )
        )
        s.add(
            PredictionRow(
                incident_id="github:gone",
                made_at=old - timedelta(days=200),
                model_version="t",
                elapsed_min=0,
                phase="investigating",
                p10=1,
                p50=2,
                p90=3,
            )
        )
    db.kv_set("similar:github:gone", {"items": []})
    db.kv_set("similar:github:live", {"items": []})
    db.prune(keep_polls=timedelta(days=2), keep_events=timedelta(days=30))
    with db.session() as s:
        assert s.query(PollRow).count() == 0
        assert s.query(EventRow).filter(EventRow.incident_id == "github:gone").count() == 0
        assert s.query(BlastRow).count() == 0
        assert s.query(PredictionRow).count() == 0
    assert db.kv_get("similar:github:gone") is None
    assert db.kv_get("similar:github:live") is not None, "open incidents keep their similar list"
    assert {i.id for i in db.open_incidents()} == {"github:live"}


def test_hostile_incident_ids_stay_inside_the_api_tree(db, github, tmp_path):
    hostile = _incident("github", "../../overview", Impact.MAJOR, timedelta(minutes=5))
    assert "/" not in hostile.source_id and ".." not in hostile.source_id
    db.sync_services([github])
    db.upsert_incident(hostile, seen_at=utcnow(), seen_open=True)
    out = tmp_path / "site"
    publish(ApiBuilder(db), out)
    overview = json.loads((out / "api/v1/overview.json").read_text())
    assert "services" in overview, "overview.json must still be the overview"
    written = [p.relative_to(out).as_posix() for p in out.rglob("*.json")]
    assert all(p.startswith("api/v1/") for p in written)


def test_dropping_a_service_closes_its_incidents_and_publish_survives(db, github, tmp_path):
    from mercalli.catalog import Service
    from mercalli.domain import Category

    other = Service(
        id="other",
        name="Other",
        category=Category.DEVTOOLS,
        adapter="statuspage",
        base_url="https://status.example.com",
    )
    db.sync_services([github, other])
    db.upsert_incident(_incident("other", "x", Impact.MAJOR, timedelta(minutes=5)), seen_at=utcnow(), seen_open=True)
    db.sync_services([github])
    assert db.open_incidents() == []
    publish(ApiBuilder(db), tmp_path / "site")
    assert (tmp_path / "site/feeds/all.xml").exists()
