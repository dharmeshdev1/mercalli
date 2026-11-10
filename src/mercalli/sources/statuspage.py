"""Atlassian Statuspage, and hosted products that copy its public API.

The public API is documented at https://metastatuspage.com/api. Three parts matter here:

* `/api/v2/summary.json` has every component and every unresolved incident, so one request per poll
  is enough to know a page's whole current state.
* `/api/v2/incidents.json` has the 50 most recent incidents with all their updates.
* `/history.json?page=N` walks back through history one quarter at a time, and
  `/incidents/<code>.json` returns one incident with its full timeline. Only Atlassian's own
  product serves these two, so only it supports a backfill.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from mercalli.catalog import Service
from mercalli.domain import Component, Health, Impact, Incident, Phase, Snapshot, Update
from mercalli.http import PoliteClient

_HEALTH = {
    "operational": Health.OPERATIONAL,
    "degraded_performance": Health.DEGRADED,
    "partial_outage": Health.PARTIAL_OUTAGE,
    "major_outage": Health.MAJOR_OUTAGE,
    "under_maintenance": Health.MAINTENANCE,
}
_PHASE = {p.value: p for p in Phase}
_IMPACT = {i.value: i for i in Impact}
_ATLASSIAN_PAGE_ID = re.compile(r"^[a-z0-9]{12}$")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def flavor_of(page_id: str) -> str:
    return "atlassian" if _ATLASSIAN_PAGE_ID.match(page_id or "") else "compatible"


def parse_incident(service_id: str, raw: dict[str, Any], base_url: str) -> Incident:
    updates = []
    for u in raw.get("incident_updates") or []:
        at = parse_time(u.get("display_at") or u.get("created_at"))
        if at is None:
            continue
        comps = [c.get("name", "") for c in (u.get("affected_components") or []) if c.get("name")]
        updates.append(
            Update(
                at=at,
                status=_PHASE.get(u.get("status", ""), Phase.INVESTIGATING),
                body=(u.get("body") or "").strip(),
                components=comps,
            )
        )
    updates.sort(key=lambda u: u.at)
    components = list(dict.fromkeys(c for u in updates for c in u.components))
    for c in raw.get("components") or []:
        name = c.get("name")
        if name and name not in components:
            components.append(name)
    created = parse_time(raw.get("created_at"))
    if created is None:
        created = updates[0].at if updates else parse_time(raw.get("updated_at"))
    assert created is not None, "incident without any timestamp"
    code = raw.get("id") or raw.get("code") or ""
    shortlink = raw.get("shortlink")
    return Incident(
        service_id=service_id,
        source_id=str(code),
        title=(raw.get("name") or "").strip(),
        impact=_IMPACT.get(raw.get("impact") or "none", Impact.NONE),
        status=_PHASE.get(raw.get("status") or "", Phase.INVESTIGATING),
        created_at=created,
        started_at=parse_time(raw.get("started_at")),
        resolved_at=parse_time(raw.get("resolved_at")),
        updated_at=parse_time(raw.get("updated_at")),
        url=shortlink or f"{base_url}/incidents/{code}",
        components=components,
        updates=updates,
    )


def parse_summary(service: Service, data: dict[str, Any]) -> Snapshot:
    groups = {c["id"]: c["name"] for c in data.get("components", []) if c.get("group")}
    components = [
        Component(
            id=c["id"],
            name=c.get("name", ""),
            status=_HEALTH.get(c.get("status", ""), Health.UNKNOWN),
            group=groups.get(c.get("group_id") or ""),
        )
        for c in data.get("components", [])
        if not c.get("group")
    ]
    incidents = [parse_incident(service.id, i, service.base_url) for i in data.get("incidents") or []]
    maint = [
        parse_incident(service.id, m, service.base_url)
        for m in data.get("scheduled_maintenances") or []
        if m.get("status") in ("in_progress", "verifying")
    ]
    for m in maint:
        m.impact = Impact.MAINTENANCE
    status = data.get("status") or {}
    return Snapshot(
        service_id=service.id,
        indicator=_IMPACT.get(status.get("indicator") or "none", Impact.NONE),
        description=status.get("description") or "",
        components=components,
        incidents=[i for i in incidents if i.is_open],
        maintenances=maint,
    )


async def snapshot(client: PoliteClient, service: Service) -> Snapshot:
    data, elapsed = await client.get_json_timed(f"{service.base_url}/api/v2/summary.json")
    snap = parse_summary(service, data)  # type: ignore[arg-type]
    snap.latency_ms = elapsed
    return snap


async def recent_incidents(client: PoliteClient, service: Service) -> list[Incident]:
    data: Any = await client.get_json(f"{service.base_url}/api/v2/incidents.json")
    return [parse_incident(service.id, i, service.base_url) for i in data.get("incidents") or []]
