"""Reading events and the incidents they are about. Shared by the notifier and the live stream.

Readers follow the events table by id. That is safe because one writer adds events, the engine
tick on the scheduler replica, one committed transaction after another, so ids become visible in
order and a reader that has seen id N has seen everything before it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from mercalli.api import ApiBuilder
from mercalli.store import Database, EventRow, IncidentRow


def head_event_id(db: Database) -> int:
    with db.session() as s:
        return int(s.scalar(select(func.max(EventRow.id))) or 0)


def events_after(db: Database, after: int, *, limit: int, upto: int | None = None) -> list[EventRow]:
    with db.session() as s:
        q = select(EventRow).where(EventRow.id > after)
        if upto is not None:
            q = q.where(EventRow.id <= upto)
        return list(s.scalars(q.order_by(EventRow.id).limit(limit)))


def subject_incident(ev: EventRow) -> str | None:
    """The incident an event is about. A blast event is about the upstream incident."""
    if ev.kind == "blast":
        upstream = (ev.payload or {}).get("upstream_incident_id")
        if isinstance(upstream, str) and upstream:
            return upstream
    return ev.incident_id


class Summaries:
    """Incident summaries exactly as overview.json shows them, built at most once per incident."""

    BLAST_LIMIT = 8  # the same cut overview.json makes

    def __init__(self, db: Database, builder: ApiBuilder) -> None:
        self.db = db
        self.builder = builder
        self._cache: dict[str, dict[str, Any] | None] = {}

    def get(self, incident_id: str | None) -> dict[str, Any] | None:
        if not incident_id:
            return None
        if incident_id not in self._cache:
            self._cache[incident_id] = self._build(incident_id)
        return self._cache[incident_id]

    def service_name(self, service_id: str) -> str:
        row = self.builder.services.get(service_id)
        return row.name if row else service_id

    def _build(self, incident_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            row = s.get(IncidentRow, incident_id)
            if row is None:
                return None
            _ = row.updates  # load before the session closes
        forecast = self.builder.latest_forecasts([incident_id]).get(incident_id)
        blast = self.builder.latest_blast([incident_id], limit=self.BLAST_LIMIT).get(incident_id)
        return self.builder.incident_summary(row, forecast, blast)
