"""Adapters for status pages that do not speak the Statuspage API: AWS, Google Cloud, Azure, Slack, Heroku, status.io.

Each returns the same `Snapshot` and `Incident` types as the Statuspage adapter. Where a provider
publishes a region for an incident, it is kept in `Incident.regions`, because the region is what
decides which downstream services feel it.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from mercalli.catalog import Service
from mercalli.domain import Component, Health, Impact, Incident, Phase, Snapshot, Update
from mercalli.http import PoliteClient
from mercalli.sources.statuspage import parse_time

AWS_CURRENT = "https://health.aws.amazon.com/public/currentevents"
AWS_HISTORY = "https://history-events-us-west-2-prod.s3.amazonaws.com/historyevents.json"
GCP_INCIDENTS = "https://status.cloud.google.com/incidents.json"
AZURE_FEED = "https://azure.status.microsoft/en-us/status/feed/"
SLACK_CURRENT = "https://slack-status.com/api/v2.0.0/current"
SLACK_HISTORY = "https://slack-status.com/api/v2.0.0/history"
HEROKU_CURRENT = "https://status.heroku.com/api/v4/current-status"
HEROKU_INCIDENTS = "https://status.heroku.com/api/v4/incidents?limit=50"

_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", text or ""))).strip()


def _epoch(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return None


# AWS ------------------------------------------------------------------------------------------------

_AWS_IMPACT = {"0": Impact.NONE, "1": Impact.MINOR, "2": Impact.MAJOR, "3": Impact.CRITICAL}
_AWS_ARN = re.compile(r"^arn:aws:health:(?P<region>[a-z0-9-]*)::event/(?P<svc>[^/]+)/[^/]+/(?P<id>.+)$")


def _decode_aws(raw: bytes) -> Any:
    import gzip
    import json

    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, ValueError):
            continue
    raise ValueError("could not decode AWS health feed")


def parse_aws_event(service_id: str, ev: dict[str, Any]) -> Incident | None:
    m = _AWS_ARN.match(ev.get("arn", ""))
    if not m:
        return None
    region = m.group("region")
    log = sorted(ev.get("event_log") or [], key=lambda x: int(x.get("timestamp") or 0))
    updates: list[Update] = []
    for entry in log:
        at = _epoch(entry.get("timestamp"))
        if at is None:
            continue
        resolved = str(entry.get("status")) == "0"
        updates.append(
            Update(
                at=at,
                status=Phase.RESOLVED if resolved else Phase.INVESTIGATING,
                body=strip_html(entry.get("message") or ""),
            )
        )
    created = _epoch(ev.get("date")) or (updates[0].at if updates else None)
    if created is None:
        return None
    raw_summary = ev.get("summary") or ""
    summary = re.sub(r"^\[RESOLVED\]\s*", "", raw_summary).strip()
    statuses = [str(e.get("status")) for e in log] + [str(ev.get("status"))]
    impact = max((_AWS_IMPACT.get(s, Impact.NONE) for s in statuses), key=lambda i: i.rank)
    # An event is over when its last log entry says the service is operating normally (status 0),
    # whichever feed it came from. The history feed also marks finished events "[RESOLVED]".
    resolved_at = None
    if updates and log and str(log[-1].get("status")) == "0":
        resolved_at = updates[-1].at
    elif updates and raw_summary.startswith("[RESOLVED]"):
        resolved_at = next((u.at for u in reversed(updates) if u.status == Phase.RESOLVED), updates[-1].at)
    service_name = ev.get("service_name") or m.group("svc").replace("_", " ").title()
    impacted = [s.get("service_name", "") for s in ev.get("impacted_services") or [] if isinstance(s, dict)]
    return Incident(
        service_id=service_id,
        source_id=m.group("id"),
        title=summary or f"{service_name} issue",
        impact=impact,
        status=Phase.RESOLVED if resolved_at else Phase.INVESTIGATING,
        created_at=created,
        resolved_at=resolved_at,
        updated_at=updates[-1].at if updates else created,
        url="https://health.aws.amazon.com/health/status",
        components=[c for c in [service_name, *impacted] if c],
        regions=[region] if region else ["global"],
        updates=updates,
    )


async def aws_snapshot(client: PoliteClient, service: Service) -> Snapshot:
    resp = await client.get(AWS_CURRENT)
    resp.raise_for_status()
    elapsed = resp.elapsed.total_seconds() * 1000
    events = _decode_aws(resp.content) or []
    parsed = [i for ev in events if (i := parse_aws_event(service.id, ev))]
    incidents = [i for i in parsed if i.is_open]
    snap = Snapshot(service_id=service.id, incidents=incidents)
    snap.indicator = max((i.impact for i in incidents), key=lambda i: i.rank, default=Impact.NONE)
    snap.description = f"{len(incidents)} open AWS events" if incidents else "No open AWS events"
    snap.components = [
        Component(id=f"{c}|{r}", name=f"{c} ({r})", status=_impact_health(i.impact))
        for i in incidents
        for c in i.components[:1]
        for r in i.regions
    ]
    snap.latency_ms = elapsed
    return snap


async def aws_history(client: PoliteClient, service: Service) -> list[Incident]:
    resp = await client.get(AWS_HISTORY)
    resp.raise_for_status()
    data = _decode_aws(resp.content) or {}
    out: list[Incident] = []
    for events in data.values():
        for ev in events:
            inc = parse_aws_event(service.id, ev)
            if inc:
                out.append(inc)
    return out


# Google Cloud --------------------------------------------------------------------------------------

_GCP_IMPACT = {"low": Impact.MINOR, "medium": Impact.MAJOR, "high": Impact.CRITICAL}


def parse_gcp(service_id: str, raw: dict[str, Any]) -> Incident:
    updates = [
        Update(
            at=parse_time(u.get("when") or u.get("created")) or parse_time(raw["created"]),  # type: ignore[arg-type]
            status=Phase.RESOLVED if (u.get("status") == "AVAILABLE") else Phase.INVESTIGATING,
            body=strip_html(u.get("text") or ""),
        )
        for u in raw.get("updates") or []
    ]
    updates.sort(key=lambda u: u.at)
    locations = {
        loc.get("id") or loc.get("title")
        for key in ("currently_affected_locations", "previously_affected_locations")
        for loc in raw.get(key) or []
    }
    end = parse_time(raw.get("end"))
    return Incident(
        service_id=service_id,
        source_id=str(raw.get("id")),
        title=strip_html(raw.get("external_desc") or "")[:300] or "Google Cloud incident",
        impact=_GCP_IMPACT.get(raw.get("severity") or "", Impact.MINOR),
        status=Phase.RESOLVED if end else Phase.INVESTIGATING,
        created_at=parse_time(raw.get("created")) or parse_time(raw.get("begin")),  # type: ignore[arg-type]
        started_at=parse_time(raw.get("begin")),
        resolved_at=end,
        updated_at=parse_time(raw.get("modified")),
        url=f"https://status.cloud.google.com/{raw.get('uri', '')}".rstrip("/"),
        components=[p.get("title", "") for p in raw.get("affected_products") or [] if p.get("title")],
        regions=sorted(x for x in locations if x),
        updates=updates,
    )


async def gcp_snapshot(client: PoliteClient, service: Service) -> Snapshot:
    data: Any
    data, elapsed = await client.get_json_timed(GCP_INCIDENTS)
    incidents = [parse_gcp(service.id, r) for r in data]
    open_ = [i for i in incidents if i.is_open]
    snap = Snapshot(service_id=service.id, incidents=open_)
    snap.indicator = max((i.impact for i in open_), key=lambda i: i.rank, default=Impact.NONE)
    snap.description = f"{len(open_)} open Google Cloud incidents" if open_ else "No open incidents"
    snap.components = [Component(id=c, name=c, status=_impact_health(i.impact)) for i in open_ for c in i.components]
    snap.latency_ms = elapsed
    return snap


async def gcp_history(client: PoliteClient, service: Service) -> list[Incident]:
    data: Any = await client.get_json(GCP_INCIDENTS)
    return [parse_gcp(service.id, r) for r in data]


# Azure ---------------------------------------------------------------------------------------------


async def azure_snapshot(client: PoliteClient, service: Service) -> Snapshot:
    """Azure's feed only lists issues that are happening now, with no structured severity."""
    resp = await client.get(AZURE_FEED)
    resp.raise_for_status()
    elapsed = resp.elapsed.total_seconds() * 1000
    root = ET.fromstring(resp.content)
    incidents = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        body = strip_html(item.findtext("description") or "")
        pub = item.findtext("pubDate")
        at = parsedate_to_datetime(pub) if pub else datetime.now(UTC)
        guid = item.findtext("guid") or item.findtext("link") or title
        incidents.append(
            Incident(
                service_id=service.id,
                source_id=re.sub(r"[^A-Za-z0-9_-]+", "-", guid)[-80:],
                title=title,
                impact=Impact.MAJOR,
                status=Phase.INVESTIGATING,
                created_at=at,
                url=item.findtext("link") or service.base_url,
                updates=[Update(at=at, status=Phase.INVESTIGATING, body=body)],
            )
        )
    snap = Snapshot(service_id=service.id, incidents=incidents)
    snap.indicator = Impact.MAJOR if incidents else Impact.NONE
    snap.description = f"{len(incidents)} active Azure issues" if incidents else "No active issues"
    snap.latency_ms = elapsed
    return snap


# Slack ---------------------------------------------------------------------------------------------


def parse_slack(service_id: str, raw: dict[str, Any]) -> Incident:
    notes = sorted(raw.get("notes") or [], key=lambda n: n.get("date_created") or "")
    created = parse_time(raw.get("date_created"))
    assert created is not None
    resolved = raw.get("status") in ("resolved", "completed")
    updates = [
        Update(
            at=parse_time(n.get("date_created")) or created,
            status=Phase.INVESTIGATING,
            body=strip_html(n.get("body") or ""),
        )
        for n in notes
    ]
    if resolved and updates:
        updates[-1].status = Phase.RESOLVED
    impact = {"outage": Impact.CRITICAL, "incident": Impact.MINOR}.get(raw.get("type") or "", Impact.MINOR)
    return Incident(
        service_id=service_id,
        source_id=str(raw.get("id")),
        title=(raw.get("title") or "").strip(),
        impact=impact,
        status=Phase.RESOLVED if resolved else Phase.INVESTIGATING,
        created_at=created,
        resolved_at=parse_time(raw.get("date_updated")) if resolved else None,
        updated_at=parse_time(raw.get("date_updated")),
        url=raw.get("url"),
        components=list(raw.get("services") or []),
        updates=updates,
    )


async def slack_snapshot(client: PoliteClient, service: Service) -> Snapshot:
    data: Any
    data, elapsed = await client.get_json_timed(SLACK_CURRENT)
    incidents = [parse_slack(service.id, i) for i in data.get("active_incidents") or []]
    snap = Snapshot(service_id=service.id, incidents=[i for i in incidents if i.is_open])
    snap.indicator = max((i.impact for i in snap.incidents), key=lambda i: i.rank, default=Impact.NONE)
    snap.description = "Slack is up and running" if data.get("status") == "ok" else "Slack has an active incident"
    snap.latency_ms = elapsed
    return snap


async def slack_history(client: PoliteClient, service: Service) -> list[Incident]:
    data: Any = await client.get_json(SLACK_HISTORY)
    return [parse_slack(service.id, i) for i in data if i.get("type") != "notice"]


# Heroku --------------------------------------------------------------------------------------------

_HEROKU_HEALTH = {
    "green": Health.OPERATIONAL,
    "yellow": Health.DEGRADED,
    "red": Health.MAJOR_OUTAGE,
    "blue": Health.MAINTENANCE,
}


def parse_heroku(service_id: str, raw: dict[str, Any]) -> Incident:
    updates = sorted(
        (
            Update(
                at=parse_time(u.get("created_at")),  # type: ignore[arg-type]
                status=Phase.RESOLVED if u.get("update_type") == "resolved" else Phase.INVESTIGATING,
                body=strip_html(u.get("contents") or ""),
            )
            for u in raw.get("updates") or []
            if u.get("created_at")
        ),
        key=lambda u: u.at,
    )
    resolved_at = parse_time(raw.get("resolved_at"))
    if resolved_at is None and raw.get("state") == "resolved":
        resolved_at = next((u.at for u in reversed(updates) if u.status == Phase.RESOLVED), None) or parse_time(
            raw.get("updated_at")
        )
    systems = [s.get("name", "") for s in raw.get("systems") or [] if s.get("name")]
    worst = max(
        (_HEROKU_HEALTH.get(s.get("status", ""), Health.UNKNOWN) for s in raw.get("systems") or []),
        key=lambda h: h.rank,
        default=Health.DEGRADED,
    )
    impact = {3: Impact.CRITICAL, 2: Impact.MAJOR}.get(worst.rank, Impact.MINOR)
    return Incident(
        service_id=service_id,
        source_id=str(raw.get("id")),
        title=(raw.get("title") or "").strip(),
        impact=impact,
        status=Phase.RESOLVED if resolved_at else Phase.INVESTIGATING,
        created_at=parse_time(raw.get("created_at")),  # type: ignore[arg-type]
        resolved_at=resolved_at,
        updated_at=parse_time(raw.get("updated_at")),
        url=raw.get("full_url"),
        components=systems,
        regions=list(raw.get("tags") or []),
        updates=updates,
    )


async def heroku_snapshot(client: PoliteClient, service: Service) -> Snapshot:
    data: Any
    data, elapsed = await client.get_json_timed(HEROKU_CURRENT)
    comps = [
        Component(id=s["system"], name=s["system"], status=_HEROKU_HEALTH.get(s.get("status", ""), Health.UNKNOWN))
        for s in data.get("status") or []
    ]
    incidents = [parse_heroku(service.id, i) for i in data.get("incidents") or []]
    snap = Snapshot(service_id=service.id, components=comps, incidents=[i for i in incidents if i.is_open])
    worst = max((c.status for c in comps), key=lambda h: h.rank, default=Health.OPERATIONAL)
    snap.indicator = {3: Impact.CRITICAL, 2: Impact.MAJOR, 1: Impact.MINOR}.get(worst.rank, Impact.NONE)
    snap.description = "All systems operational" if worst.rank == 0 else "Heroku has an active incident"
    snap.latency_ms = elapsed
    return snap


async def heroku_history(client: PoliteClient, service: Service) -> list[Incident]:
    data: Any = await client.get_json(HEROKU_INCIDENTS)
    return [parse_heroku(service.id, i) for i in data]


def _impact_health(impact: Impact) -> Health:
    return {Impact.CRITICAL: Health.MAJOR_OUTAGE, Impact.MAJOR: Health.PARTIAL_OUTAGE}.get(impact, Health.DEGRADED)


# status.io -----------------------------------------------------------------------------------------
# Public API: https://api.status.io/1.0/status/<page id>. Status codes are documented by status.io:
# 100 operational, 200 planned maintenance, 300 degraded performance, 400 partial disruption,
# 500 service disruption, 600 security event. The page id is the `statuspage_id` in the page HTML.

STATUSIO_API = "https://api.status.io/1.0/status/{page_id}"
_STATUSIO_HEALTH = {
    100: Health.OPERATIONAL,
    200: Health.MAINTENANCE,
    300: Health.DEGRADED,
    400: Health.PARTIAL_OUTAGE,
    500: Health.MAJOR_OUTAGE,
    600: Health.MAJOR_OUTAGE,
}
_STATUSIO_IMPACT = {300: Impact.MINOR, 400: Impact.MAJOR, 500: Impact.CRITICAL, 600: Impact.CRITICAL}
_STATUSIO_PHASE = {100: Phase.INVESTIGATING, 200: Phase.IDENTIFIED, 300: Phase.MONITORING, 400: Phase.RESOLVED}
STATUSIO_PAGE_ID = re.compile(r"statuspage_id[\"']?\s*[:=]\s*[\"']([a-f0-9]{24})")


def parse_statusio_incident(service: Service, raw: dict[str, Any]) -> Incident | None:
    msgs = sorted(raw.get("messages") or [], key=lambda m: m.get("datetime") or "")
    created = parse_time(raw.get("datetime_open")) or (parse_time(msgs[0].get("datetime")) if msgs else None)
    if created is None:
        return None
    updates = [
        Update(
            at=parse_time(m.get("datetime")) or created,
            status=_STATUSIO_PHASE.get(int(m.get("state") or 100), Phase.INVESTIGATING),
            body=strip_html(m.get("details") or ""),
        )
        for m in msgs
    ]
    worst = max((int(m.get("status") or 300) for m in msgs), default=300)
    comps = [c.get("name", "") for c in raw.get("components_affected") or [] if c.get("name")]
    regions = [c.get("name", "") for c in raw.get("containers_affected") or [] if c.get("name")]
    return Incident(
        service_id=service.id,
        source_id=str(raw.get("_id") or raw.get("id")),
        title=(raw.get("name") or "").strip(),
        impact=_STATUSIO_IMPACT.get(worst, Impact.MINOR),
        status=updates[-1].status if updates else Phase.INVESTIGATING,
        created_at=created,
        url=service.base_url,
        components=comps,
        regions=regions,
        updates=updates,
    )


async def statusio_snapshot(client: PoliteClient, service: Service) -> Snapshot:
    data: Any
    data, elapsed = await client.get_json_timed(STATUSIO_API.format(page_id=service.page_id))
    result = data.get("result") or {}
    comps = []
    for comp in result.get("status") or []:
        for cont in comp.get("containers") or [{"id": comp.get("id"), "name": ""}]:
            name = f"{comp.get('name', '')} ({cont['name']})" if cont.get("name") else comp.get("name", "")
            code = int(cont.get("status_code") or comp.get("status_code") or 100)
            comps.append(
                Component(
                    id=f"{comp.get('id')}|{cont.get('id')}",
                    name=name,
                    status=_STATUSIO_HEALTH.get(code, Health.UNKNOWN),
                    group=comp.get("name"),
                )
            )
    incidents = [i for raw in result.get("incidents") or [] if (i := parse_statusio_incident(service, raw))]
    overall = int((result.get("status_overall") or {}).get("status_code") or 100)
    snap = Snapshot(service_id=service.id, components=comps, incidents=[i for i in incidents if i.is_open])
    snap.indicator = _STATUSIO_IMPACT.get(overall, Impact.MAINTENANCE if overall == 200 else Impact.NONE)
    snap.description = (result.get("status_overall") or {}).get("status") or ""
    snap.latency_ms = elapsed
    return snap
