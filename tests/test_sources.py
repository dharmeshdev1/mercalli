from __future__ import annotations

import httpx
import respx

from mercalli.backfill import parse_history_page, parse_history_time
from mercalli.domain import Health, Impact, Phase
from mercalli.http import PoliteClient
from mercalli.sources import poll, statuspage, vendors
from tests.conftest import load, t


def test_summary_maps_components_incidents_and_maintenance(github):
    snap = statuspage.parse_summary(github, load("statuspage_summary.json"))
    names = {c.name: c for c in snap.components}
    assert "Core" not in names, "group containers are not components"
    assert names["Actions"].status is Health.DEGRADED
    assert names["Actions"].group == "Core"
    assert names["Pages"].status is Health.PARTIAL_OUTAGE
    assert snap.indicator is Impact.MINOR
    assert snap.health is Health.PARTIAL_OUTAGE
    [inc] = snap.incidents
    assert inc.id == "github:inc1"
    assert [u.status for u in inc.updates] == [Phase.INVESTIGATING, Phase.IDENTIFIED], "updates sorted oldest first"
    assert inc.start == t("2026-11-03T09:55:00"), "a backdated start wins over created_at"
    assert inc.components == ["Actions"]
    assert [m.title for m in snap.maintenances] == ["Database upgrade"], "only maintenance in progress counts"


def test_retroactive_incident_is_detected(github):
    data = load("statuspage_incidents.json")
    incs = {
        i.source_id: i for i in (statuspage.parse_incident("github", r, github.base_url) for r in data["incidents"])
    }
    assert incs["old1"].retroactive
    assert not incs["inc1"].retroactive
    assert incs["inc1"].duration_minutes() == 65.0


def test_aws_event_keeps_region_and_severity():
    [raw] = load("aws_current.json")
    inc = vendors.parse_aws_event("aws", raw)
    assert inc is not None
    assert inc.regions == ["us-east-1"]
    assert inc.impact is Impact.MAJOR
    assert inc.is_open
    assert inc.components[0] == "Amazon Elastic Compute Cloud"
    assert len(inc.updates) == 2


def test_aws_history_event_resolves_at_last_normal_entry():
    raw = {
        "summary": "[RESOLVED] Increased latency",
        "status": "1",
        "date": "1762160400",
        "arn": "arn:aws:health:eu-west-1::event/S3/AWS_S3_OPERATIONAL_ISSUE/X_1",
        "event_log": [
            {"message": "Investigating", "status": "1", "timestamp": "1762160400"},
            {"message": "Resolved", "status": "0", "timestamp": "1762164000"},
        ],
    }
    inc = vendors.parse_aws_event("aws", raw)
    assert inc is not None and inc.title == "Increased latency"
    assert inc.duration_minutes() == 60.0


def test_statusio_incident_parsing(github):
    raw = {
        "_id": "abc",
        "name": "Degraded API",
        "datetime_open": "2026-11-03T10:00:00Z",
        "components_affected": [{"name": "API"}],
        "containers_affected": [{"name": "us-east"}],
        "messages": [
            {"details": "<p>Looking</p>", "state": 100, "status": 400, "datetime": "2026-11-03T10:00:00Z"},
            {"details": "Fix deployed", "state": 300, "status": 300, "datetime": "2026-11-03T10:30:00Z"},
        ],
    }
    inc = vendors.parse_statusio_incident(github, raw)
    assert inc is not None
    assert inc.impact is Impact.MAJOR
    assert inc.status is Phase.MONITORING
    assert inc.updates[0].body == "Looking"
    assert inc.regions == ["us-east"]


def test_history_time_parsing_handles_year_and_day_boundaries():
    var = lambda kind, v: f"<var data-var='{kind}'>{v}</var>"  # noqa: E731
    s, e = parse_history_time(f"Nov {var('date', 2)}, {var('time', '17:46')} - {var('time', '18:24')} UTC", 2026, 11)
    assert (s, e) == (t("2026-11-02T17:46"), t("2026-11-02T18:24"))
    s, e = parse_history_time(
        f"Dec {var('date', 31)}, {var('time', '22:28')} - Jan {var('date', 1)}, {var('time', '02:00')} UTC", 2026, 1
    )
    assert (s, e) == (t("2025-12-31T22:28"), t("2026-01-01T02:00"))
    s, e = parse_history_time(
        f"Oct {var('date', 23)}, {var('time', '23:41')} - Oct {var('date', 24)}, {var('time', '00:23')} UTC", 2026, 10
    )
    assert e is not None and s is not None and (e - s).total_seconds() == 42 * 60


def test_history_page_entries():
    page = {
        "months": [
            {
                "name": "November",
                "year": 2026,
                "incidents": [
                    {
                        "code": "x1",
                        "name": "Slow builds",
                        "impact": "major",
                        "timestamp": "Nov <var data-var='date'>2</var>, <var data-var='time'>17:46</var> - "
                        "<var data-var='time'>18:24</var> UTC",
                    }
                ],
            }
        ]
    }
    [e] = parse_history_page("github", page)
    assert e.code == "x1" and e.impact is Impact.MAJOR and e.end == t("2026-11-02T18:24")


@respx.mock
async def test_poll_turns_failures_into_snapshots(github):
    respx.get("https://www.githubstatus.com/api/v2/summary.json").mock(return_value=httpx.Response(503))
    async with PoliteClient(retries=0) as c:
        snap = await poll(c, github)
    assert not snap.ok and snap.error == "HTTP 503"


def test_history_times_are_converted_from_the_page_zone():
    var = lambda kind, v: f"<var data-var='{kind}'>{v}</var>"  # noqa: E731
    s, e = parse_history_time(
        f"Oct {var('date', 15)}, {var('time', '13:42')} - {var('time', '13:47')} EDT", 2026, 10, "Eastern Time"
    )
    assert (s, e) == (t("2026-10-15T17:42"), t("2026-10-15T17:47"))
    s, _ = parse_history_time(
        f"Mar {var('date', 2)}, {var('time', '10:00')} - {var('time', '11:30')} GMT-03:00", 2026, 3
    )
    assert s == t("2026-03-02T13:00")
    s, _ = parse_history_time(
        f"Mar {var('date', 2)}, {var('time', '10:00')} - {var('time', '11:30')} IST", 2026, 3, "Dublin"
    )
    assert s == t("2026-03-02T09:00"), "IST in Dublin is Irish summer time, not India"
    assert parse_history_time(f"Mar {var('date', 2)}, {var('time', '10:00')} ZZZ", 2026, 3) == (None, None)


def test_discovered_page_names_are_cleaned():
    from mercalli.discover import clean_page_name

    assert clean_page_name("Copy.ai – System Status |") == "Copy.ai"
    assert clean_page_name("Pinterest Status") == "Pinterest"
    assert clean_page_name("Bar: System Status") == "Bar"
    assert clean_page_name("DigiCert") == "DigiCert"


def test_aws_event_closed_in_the_current_feed_is_not_open():
    raw = {
        "summary": "Increased latency",
        "status": "1",
        "date": "1762160400",
        "arn": "arn:aws:health:us-east-1::event/S3/AWS_S3_OPERATIONAL_ISSUE/X_2",
        "event_log": [
            {"message": "Investigating", "status": "1", "timestamp": "1762160400"},
            {"message": "Operating normally", "status": "0", "timestamp": "1762167600"},
        ],
    }
    inc = vendors.parse_aws_event("aws", raw)
    assert inc is not None and not inc.is_open and inc.duration_minutes() == 120.0
    ongoing = dict(raw, event_log=raw["event_log"][:1])
    inc2 = vendors.parse_aws_event("aws", ongoing)
    assert inc2 is not None and inc2.is_open, "an event still in progress in the history feed stays open"


def test_history_cache_is_kept_per_month_and_survives_window_shifts(tmp_path):
    from datetime import UTC, datetime

    from mercalli.backfill import RawCache, merge_feed, page_months

    nov = datetime(2026, 11, 3, tzinfo=UTC)
    assert page_months(1, nov) == ["2026-09", "2026-10", "2026-11"]
    assert page_months(2, nov) == ["2026-06", "2026-07", "2026-08"]
    assert page_months(1, datetime(2027, 1, 5, tzinfo=UTC)) == ["2026-11", "2026-12", "2027-01"]

    cache = RawCache(tmp_path)
    page = {
        "page_status": {"page": {"created_at": "2020-01-01T00:00:00Z", "time_zone": "UTC"}},
        "months": [
            {"name": "October", "year": 2026, "incidents": []},
            {
                "name": "September",
                "year": 2026,
                "incidents": [
                    {
                        "code": "a1",
                        "name": "x",
                        "impact": "minor",
                        "timestamp": "Sep <var data-var='date'>2</var>, <var data-var='time'>10:00</var> - "
                        "<var data-var='time'>11:00</var> UTC",
                    }
                ],
            },
        ],
    }
    assert cache.save_months("svc", page) == ["2026-10", "2026-09"]
    assert [e.code for e in cache.history_entries("svc")] == ["a1"]

    merge_feed(cache, "svc", "statuspage", {"incidents": [{"id": "i1", "name": "old"}]})
    merge_feed(cache, "svc", "statuspage", {"incidents": [{"id": "i1", "name": "new"}, {"id": "i2", "name": "b"}]})
    import json

    items = json.loads(cache.feed_path("svc").read_text())["items"]
    assert set(items) == {"i1", "i2"} and items["i1"]["name"] == "new"
