"""What an alert says, before any channel decides how it looks.

Every channel renders the same Message, so Slack, email and a pager tell one story: which service,
what happened, how bad it is, how long it may last and where to read more.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

from mercalli.notify.events import subject_incident
from mercalli.store import EventRow

LABELS = {
    "incident.opened": "New incident",
    "incident.updated": "Incident update",
    "incident.resolved": "Resolved",
    "forecast": "Forecast",
    "blast": "May be affected",
}
IMPACT_COLORS = {"critical": 0xB71C1C, "major": 0xE65100, "minor": 0xF9A825, "none": 0x546E7A, "maintenance": 0x546E7A}
RESOLVED_COLOR = 0x2E7D32
FORECAST_COLOR = 0x1565C0
BLAST_COLOR = 0x6A1B9A
PAGER_SEVERITY = {"critical": "critical", "major": "error", "minor": "warning"}
_ETA_BUCKET_S = 30 * 60  # a new forecast is only worth a message when the likely end moves this much


@dataclass(frozen=True)
class Message:
    event_id: int
    kind: str
    at: datetime
    service_id: str
    service_name: str
    incident_id: str | None
    title: str
    impact: str
    status: str
    headline: str
    lead: str
    body: str
    facts: tuple[tuple[str, str], ...]
    vendor_url: str | None
    mercalli_url: str | None
    color: int
    severity: str
    is_open: bool
    dedupe_key: str
    category: str | None = None
    components: tuple[str, ...] = ()
    summary: dict[str, Any] | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    target_service_id: str | None = None
    target_service_name: str | None = None
    resolve_keys: tuple[str, ...] = ()  # extra pager alerts this message closes

    @property
    def links(self) -> list[tuple[str, str]]:
        out = []
        if self.vendor_url:
            out.append(("Vendor incident page", self.vendor_url))
        if self.mercalli_url:
            out.append(("Open in Mercalli", self.mercalli_url))
        return out

    @property
    def text(self) -> str:
        """The whole message as plain text, for email and webhook receivers that show text."""
        parts = [self.headline, self.lead]
        if self.body:
            parts += ["", self.body]
        if self.facts:
            parts += ["", *(f"{k}: {v}" for k, v in self.facts)]
        if self.links:
            parts += ["", *(f"{label}: {url}" for label, url in self.links)]
        return "\n".join(parts)


def build_message(
    ev: EventRow,
    summary: Mapping[str, Any] | None,
    *,
    name_of: Callable[[str], str],
    public_url: str | None,
    now: datetime,
) -> Message:
    """Describe one event, using the incident's current summary for context when there is one."""
    payload = dict(ev.payload or {})
    s: Mapping[str, Any] = summary or {}
    incident_id = s.get("id") or subject_incident(ev)
    service_id = str(s.get("service_id") or _service_of(incident_id) or ev.service_id)
    service_name = str(s.get("service_name") or name_of(service_id))
    # Incident events carry the title, impact and status as they were at the event; prefer those.
    event_title = payload.get("title") if ev.kind.startswith("incident.") else None
    title = str(event_title or s.get("title") or "Untitled incident")
    impact = str(payload.get("impact") or s.get("impact") or "none")
    default_status = "resolved" if ev.kind == "incident.resolved" else "investigating"
    status = str(payload.get("status") or s.get("status") or default_status)
    is_open = s.get("resolved_at") is None if summary is not None else ev.kind != "incident.resolved"
    started = _parse(s.get("started_at"))
    latest_body = _clean((s.get("latest") or {}).get("body") or "")
    components = tuple(str(c) for c in (s.get("components") or [])[:5])
    facts: list[tuple[str, str]] = []
    target_id = target_name = None

    if ev.kind == "blast":
        target_id = str(payload.get("service_id") or "")
        target_name = name_of(target_id)
        probability = as_float(payload.get("probability")) or 0.0
        headline = f"{LABELS['blast']}: {target_name}"
        lead = f"{service_name} incident: {title}"
        body = (
            f"Mercalli puts the chance that {target_name} posts an incident of its own because of this one "
            f"at {_percent(probability)}."
        )
        facts.append(("Probability", _percent(probability)))
        facts += [("Upstream impact", impact), ("Upstream status", status)]
        evidence = [str(e) for e in payload.get("evidence") or [] if e]
        if evidence:
            facts.append(("Evidence", "; ".join(evidence[:6])))
        color, severity = BLAST_COLOR, "warning"
        dedupe = f"blast:{incident_id}:{target_id}"
    else:
        headline = f"{LABELS.get(ev.kind, ev.kind)}: {service_name}"
        lead = title
        forecast = payload if ev.kind == "forecast" else (s.get("forecast") or {})
        forecast_text = forecast_sentence(forecast, now) if is_open else None
        if ev.kind == "incident.resolved":
            body = latest_body or "The vendor marked this incident resolved."
            if payload.get("inferred") or s.get("resolved_inferred"):
                body = (
                    "The incident left the vendor's status page without a resolution time, "
                    "so Mercalli recorded the moment it noticed."
                )
        elif ev.kind == "forecast":
            body = forecast_text or ""
        else:
            body = latest_body
        facts += [("Impact", impact), ("Status", status)]
        if started:
            facts.append(("Started", f"{fmt_time(started)} ({fmt_minutes((now - started).total_seconds() / 60)} ago)"))
        duration = as_float(s.get("duration_min"))
        if ev.kind == "incident.resolved" and duration is not None:
            facts.append(("Duration", fmt_minutes(duration)))
        if components:
            facts.append(("Components", ", ".join(components)))
        if forecast_text and ev.kind != "forecast":
            facts.append(("Forecast", forecast_text))
        if ev.kind == "incident.resolved":
            color = RESOLVED_COLOR
        elif ev.kind == "forecast":
            color = FORECAST_COLOR
        else:
            color = IMPACT_COLORS.get(impact, IMPACT_COLORS["none"])
        severity = PAGER_SEVERITY.get(impact, "info")
        dedupe = _dedupe_key(ev, incident_id, s, impact, status, forecast)

    mercalli_url = f"{public_url}/incident/?id={quote(incident_id, safe=':')}" if public_url and incident_id else None
    return Message(
        event_id=ev.id,
        kind=ev.kind,
        at=ev.at,
        service_id=service_id,
        service_name=service_name,
        incident_id=incident_id,
        title=title,
        impact=impact,
        status=status,
        headline=headline,
        lead=lead,
        body=body,
        facts=tuple(facts),
        vendor_url=s.get("url") or payload.get("url"),
        mercalli_url=mercalli_url,
        color=color,
        severity=severity,
        is_open=is_open,
        dedupe_key=dedupe,
        category=s.get("category"),
        components=components,
        summary=dict(summary) if summary is not None else None,
        payload=payload,
        target_service_id=target_id,
        target_service_name=target_name,
    )


def _dedupe_key(
    ev: EventRow, incident_id: str | None, s: Mapping[str, Any], impact: str, status: str, forecast: Mapping[str, Any]
) -> str:
    """Two events with the same key would produce the same message, so only the first is sent."""
    if not incident_id:
        return f"event:{ev.id}"
    if ev.kind == "incident.updated":
        latest = (s.get("latest") or {}).get("at") or ""
        return f"updated:{incident_id}:{s.get('updates_count')}:{latest}:{impact}:{status}"
    if ev.kind == "forecast":
        eta = _parse(forecast.get("eta_p50"))
        if eta is None:
            return f"forecast:{incident_id}:{as_float(forecast.get('p50'))}"
        return f"forecast:{incident_id}:{int(eta.timestamp() // _ETA_BUCKET_S)}"
    return f"{ev.kind.removeprefix('incident.')}:{incident_id}"


def forecast_sentence(forecast: Mapping[str, Any], now: datetime) -> str | None:
    """'Likely resolved in about 40 min (80% range 10 min to 3 h), around 18:59 UTC.'"""
    p50 = as_float(forecast.get("p50"))
    if p50 is None:
        return None
    eta = _parse(forecast.get("eta_p50"))
    # Counting from the ETA keeps the sentence right when it is sent a while after the forecast.
    left = max(0.0, (eta - now).total_seconds() / 60) if eta else p50
    text = f"Likely resolved in about {fmt_minutes(left)}"
    p10, p90 = as_float(forecast.get("p10")), as_float(forecast.get("p90"))
    if p10 is not None and p90 is not None:
        text += f" (80% range {fmt_minutes(p10)} to {fmt_minutes(p90)})"
    if eta:
        text += f", around {fmt_clock(eta, now)}"
    return text + "."


def fmt_minutes(minutes: float) -> str:
    m = max(0, round(minutes))
    if m < 60:
        return f"{m} min"
    hours, mins = divmod(m, 60)
    if hours < 24:
        return f"{hours} h {mins} min" if mins else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def fmt_time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def fmt_clock(dt: datetime, now: datetime) -> str:
    return dt.strftime("%H:%M UTC") if dt.date() == now.date() else dt.strftime("%b %d %H:%M UTC")


def thread_token(incident_id: str | None, event_id: int) -> str:
    """A stable token per incident, so mail clients thread its messages together."""
    seed = incident_id or f"event-{event_id}"
    return hashlib.sha256(seed.encode()).hexdigest()[:20]


def _percent(p: float) -> str:
    return f"{round(p * 100)}%"


def _service_of(incident_id: str | None) -> str | None:
    return incident_id.partition(":")[0] if incident_id and ":" in incident_id else None


def as_float(value: Any) -> float | None:
    """A finite float, or None. NaN would slip through every comparison."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean(text: str, limit: int = 1200) -> str:
    text = "\n".join(line.rstrip() for line in text.strip().splitlines())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."
