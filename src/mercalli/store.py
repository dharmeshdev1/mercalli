"""Operational storage. SQLite for a single process, PostgreSQL in production.

The schema sticks to types both databases share, so the same code runs the public edition (a
SQLite file carried between scheduled runs) and a self-hosted deployment (PostgreSQL, optionally
with TimescaleDB for the two time series tables).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    create_engine,
    delete,
    event,
    literal,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from mercalli.catalog import Service
from mercalli.domain import Impact, Incident, Phase, Snapshot, Update

SCHEMA_VERSION = 1


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC, always hand back timezone aware datetimes, on SQLite too."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime, dict[str, Any]: JSON, list[Any]: JSON}


class ServiceRow(Base):
    __tablename__ = "services"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32), index=True)
    adapter: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(500))
    domains: Mapped[list[Any]] = mapped_column(default=list)
    curated: Mapped[bool] = mapped_column(Boolean, default=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Latest known state, denormalized so the overview needs one query.
    health: Mapped[str] = mapped_column(String(32), default="unknown")
    indicator: Mapped[str] = mapped_column(String(32), default="none")
    description: Mapped[str] = mapped_column(Text, default="")
    last_ok_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    components: Mapped[list[Any]] = mapped_column(default=list)


class PollRow(Base):
    __tablename__ = "polls"
    __table_args__ = (Index("ix_polls_service_at", "service_id", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(index=True)
    ok: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    health: Mapped[str] = mapped_column(String(32))
    open_incidents: Mapped[int] = mapped_column(Integer, default=0)


class IncidentRow(Base):
    __tablename__ = "incidents"
    __table_args__ = (Index("ix_incidents_service_created", "service_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    service_id: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text)
    impact: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column()
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    components: Mapped[list[Any]] = mapped_column(default=list)
    regions: Mapped[list[Any]] = mapped_column(default=list)
    # What we observed ourselves, as opposed to what the vendor published.
    first_seen_at: Mapped[datetime] = mapped_column()
    last_seen_open_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolved_inferred: Mapped[bool] = mapped_column(Boolean, default=False)

    updates: Mapped[list[UpdateRow]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="UpdateRow.seq"
    )

    def to_domain(self) -> Incident:
        return Incident(
            service_id=self.service_id,
            source_id=self.source_id,
            title=self.title,
            impact=Impact(self.impact),
            status=Phase(self.status),
            created_at=self.created_at,
            started_at=self.started_at,
            resolved_at=self.resolved_at,
            updated_at=self.updated_at,
            url=self.url,
            components=list(self.components or []),
            regions=list(self.regions or []),
            updates=[
                Update(at=u.at, status=Phase(u.status), body=u.body, components=list(u.components or []))
                for u in self.updates
            ],
        )


class UpdateRow(Base):
    __tablename__ = "incident_updates"

    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text, default="")
    components: Mapped[list[Any]] = mapped_column(default=list)

    incident: Mapped[IncidentRow] = relationship(back_populates="updates")


class PredictionRow(Base):
    """Every forecast is kept, so live accuracy can be measured honestly later."""

    __tablename__ = "predictions"
    __table_args__ = (Index("ix_predictions_incident", "incident_id", "made_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(String(200))
    made_at: Mapped[datetime] = mapped_column()
    model_version: Mapped[str] = mapped_column(String(64))
    elapsed_min: Mapped[float] = mapped_column(Float)
    phase: Mapped[str] = mapped_column(String(16))
    p10: Mapped[float] = mapped_column(Float)  # remaining minutes
    p50: Mapped[float] = mapped_column(Float)
    p90: Mapped[float] = mapped_column(Float)


class BlastRow(Base):
    __tablename__ = "blast"
    __table_args__ = (Index("ix_blast_upstream", "upstream_incident_id", "made_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upstream_incident_id: Mapped[str] = mapped_column(String(200))
    made_at: Mapped[datetime] = mapped_column()
    model_version: Mapped[str] = mapped_column(String(64))
    service_id: Mapped[str] = mapped_column(String(64))
    probability: Mapped[float] = mapped_column(Float)
    evidence: Mapped[list[Any]] = mapped_column(default=list)


class EventRow(Base):
    """A change worth telling someone about. Drives the live stream and notifications."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(32))
    service_id: Mapped[str] = mapped_column(String(64), index=True)
    incident_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)


class DeliveryRow(Base):
    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True)
    channel: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column()
    ok: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class KVRow(Base):
    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgres://")
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
        self.url = url
        self.engine: Engine = create_engine(url, future=True, pool_pre_ping=True)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_conn: Any, _: Any) -> None:
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.session() as s:
            row = s.get(KVRow, "schema")
            if row is None:
                s.add(KVRow(key="schema", value={"version": SCHEMA_VERSION}))

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions.begin() as s:
            yield s

    # Services --------------------------------------------------------------------------------------

    def sync_services(self, services: list[Service]) -> None:
        with self.session() as s:
            existing = {r.id: r for r in s.scalars(select(ServiceRow))}
            for svc in services:
                row = existing.get(svc.id)
                if row is None:
                    row = ServiceRow(id=svc.id)
                    s.add(row)
                row.name, row.category, row.adapter = svc.name, svc.category.value, svc.adapter
                row.base_url, row.domains, row.curated, row.rank = svc.base_url, svc.domains, svc.curated, svc.rank
            gone = set(existing) - {svc.id for svc in services}
            for sid in gone:
                s.delete(existing[sid])
            if gone:
                # Nobody will poll these again, so their open incidents would stay open forever.
                now = datetime.now(UTC)
                for row in s.scalars(
                    select(IncidentRow).where(IncidentRow.service_id.in_(gone), IncidentRow.resolved_at.is_(None))
                ):
                    row.resolved_at, row.status, row.resolved_inferred = now, Phase.RESOLVED.value, True

    def record_snapshot(self, snap: Snapshot) -> None:
        with self.session() as s:
            row = s.get(ServiceRow, snap.service_id)
            if row is None:
                return
            row.last_polled_at = snap.fetched_at
            if snap.ok:
                row.health = snap.health.value
                row.indicator = snap.indicator.value
                row.description = snap.description
                row.last_ok_at = snap.fetched_at
                row.last_error = None
                row.components = [c.model_dump(mode="json") for c in snap.components]
            else:
                row.last_error = snap.error
                if row.last_ok_at is None or snap.fetched_at - row.last_ok_at > timedelta(minutes=30):
                    row.health = "unknown"
            s.add(
                PollRow(
                    service_id=snap.service_id,
                    at=snap.fetched_at,
                    ok=snap.ok,
                    latency_ms=snap.latency_ms,
                    health=snap.health.value if snap.ok else "unknown",
                    open_incidents=len(snap.incidents),
                )
            )

    # Incidents -------------------------------------------------------------------------------------

    def upsert_incident(self, inc: Incident, *, seen_at: datetime, seen_open: bool) -> str | None:
        """Insert or update an incident. Returns the kind of change, or None when nothing changed."""
        with self.session() as s:
            row = s.get(IncidentRow, inc.id)
            change: str | None = None
            reopened = False
            if row is None:
                row = IncidentRow(id=inc.id, service_id=inc.service_id, source_id=inc.source_id, first_seen_at=seen_at)
                s.add(row)
                change = "incident.opened" if inc.is_open else "incident.recorded"
            else:
                if len(inc.updates) < len(row.updates) and inc.resolved_at is None:
                    # An older copy (a cached list page, say) must never roll an incident back.
                    return None
                if row.resolved_at is not None and seen_open and inc.is_open:
                    # It was closed (usually by inference) but the vendor shows it open again. To
                    # anyone listening that is a new incident, so it is announced as one.
                    row.resolved_at, row.resolved_inferred = None, False
                    change, reopened = "incident.opened", True
                elif row.resolved_at is None and inc.resolved_at is not None:
                    change = "incident.resolved"
                elif len(row.updates) < len(inc.updates) or row.impact != inc.impact.value:
                    change = "incident.updated"
            row.title, row.impact, row.status = inc.title, inc.impact.value, inc.status.value
            row.created_at, row.started_at, row.updated_at = inc.created_at, inc.started_at, inc.updated_at
            if inc.resolved_at is not None:
                row.resolved_at, row.resolved_inferred = inc.resolved_at, False
            row.url, row.components, row.regions = inc.url, inc.components, inc.regions
            if seen_open:
                row.last_seen_open_at = seen_at
            if len(row.updates) != len(inc.updates) or change:
                row.updates.clear()
                s.flush()
                row.updates.extend(
                    UpdateRow(seq=i, at=u.at, status=u.status.value, body=u.body, components=u.components)
                    for i, u in enumerate(inc.updates)
                )
            if change and change != "incident.recorded":
                payload = {"title": inc.title, "impact": inc.impact.value, "status": inc.status.value}
                if reopened:
                    payload["reopened"] = True
                s.add(EventRow(at=seen_at, kind=change, service_id=inc.service_id, incident_id=inc.id, payload=payload))
            return change

    def open_incidents(self, service_id: str | None = None) -> list[IncidentRow]:
        with self.session() as s:
            q = select(IncidentRow).where(IncidentRow.resolved_at.is_(None))
            if service_id:
                q = q.where(IncidentRow.service_id == service_id)
            rows = list(s.scalars(q))
            for r in rows:
                _ = r.updates  # load before the session closes
            return rows

    def mark_resolved(self, incident_id: str, at: datetime) -> None:
        with self.session() as s:
            row = s.get(IncidentRow, incident_id)
            if row is None or row.resolved_at is not None:
                return
            row.resolved_at, row.status, row.resolved_inferred = at, Phase.RESOLVED.value, True
            s.add(
                EventRow(
                    at=at,
                    kind="incident.resolved",
                    service_id=row.service_id,
                    incident_id=row.id,
                    payload={"title": row.title, "impact": row.impact, "inferred": True},
                )
            )

    # Housekeeping ----------------------------------------------------------------------------------

    def prune(
        self,
        *,
        keep_polls: timedelta,
        keep_events: timedelta,
        keep_predictions: timedelta = timedelta(days=180),
        keep_incidents: timedelta | None = None,
    ) -> None:
        """Drop rows that only matter for a while. Incidents are kept for good unless
        `keep_incidents` is set, in which case those resolved longer ago go too, with their updates,
        forecasts and blast lists."""
        now = datetime.now(UTC)
        with self.session() as s:
            if keep_incidents is not None:
                old = select(IncidentRow.id).where(IncidentRow.resolved_at < now - keep_incidents)
                s.execute(delete(PredictionRow).where(PredictionRow.incident_id.in_(old)))
                s.execute(delete(BlastRow).where(BlastRow.upstream_incident_id.in_(old)))
                s.execute(delete(UpdateRow).where(UpdateRow.incident_id.in_(old)))
                s.execute(delete(IncidentRow).where(IncidentRow.resolved_at < now - keep_incidents))
            s.execute(delete(PollRow).where(PollRow.at < now - keep_polls))
            s.execute(delete(EventRow).where(EventRow.at < now - keep_events))
            s.execute(delete(PredictionRow).where(PredictionRow.made_at < now - keep_predictions))
            ended = select(IncidentRow.id).where(IncidentRow.resolved_at < now - keep_events)
            s.execute(delete(BlastRow).where(BlastRow.upstream_incident_id.in_(ended)))
            similar_keys = select(literal("similar:", String) + IncidentRow.id).where(
                IncidentRow.resolved_at < now - keep_events
            )
            s.execute(delete(KVRow).where(KVRow.key.in_(similar_keys)))

    def kv_get(self, key: str) -> dict[str, Any] | None:
        with self.session() as s:
            row = s.get(KVRow, key)
            return dict(row.value) if row else None

    def kv_set(self, key: str, value: dict[str, Any]) -> None:
        with self.session() as s:
            row = s.get(KVRow, key)
            if row is None:
                s.add(KVRow(key=key, value=value))
            else:
                row.value = value
