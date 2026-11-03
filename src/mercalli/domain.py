"""The normalized vocabulary every source adapter translates into.

Status pages disagree about almost everything: AWS numbers its severities, Google Cloud calls them
low, medium and high, and Atlassian Statuspage uses minor, major and critical. Everything past the
adapters speaks only the types in this module, so models, storage and the API never need to know
which software a vendor runs.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field, field_validator


class Impact(StrEnum):
    """How bad the vendor says an incident is, from their own label."""

    NONE = "none"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"
    MAINTENANCE = "maintenance"

    @property
    def rank(self) -> int:
        return {"none": 0, "maintenance": 0, "minor": 1, "major": 2, "critical": 3}[self.value]


class Phase(StrEnum):
    """Where an incident is in its lifecycle."""

    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"
    POSTMORTEM = "postmortem"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    VERIFYING = "verifying"
    COMPLETED = "completed"

    @property
    def is_open(self) -> bool:
        return self not in (Phase.RESOLVED, Phase.POSTMORTEM, Phase.COMPLETED)


class Health(StrEnum):
    """Current state of one component, worst last."""

    OPERATIONAL = "operational"
    MAINTENANCE = "maintenance"
    DEGRADED = "degraded"
    PARTIAL_OUTAGE = "partial_outage"
    MAJOR_OUTAGE = "major_outage"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        return {
            "operational": 0,
            "maintenance": 0,
            "unknown": 0,
            "degraded": 1,
            "partial_outage": 2,
            "major_outage": 3,
        }[self.value]


class Category(StrEnum):
    CLOUD = "cloud"
    EDGE = "edge"
    DNS = "dns"
    HOSTING = "hosting"
    DEVTOOLS = "devtools"
    DATA = "data"
    OBSERVABILITY = "observability"
    AI = "ai"
    PAYMENTS = "payments"
    MESSAGING = "messaging"
    COLLABORATION = "collaboration"
    IDENTITY = "identity"
    COMMERCE = "commerce"
    MEDIA = "media"
    CONSUMER = "consumer"
    OTHER = "other"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Update(BaseModel):
    """One message a vendor posted on an incident."""

    at: datetime
    status: Phase
    body: str = ""
    components: list[str] = Field(default_factory=list)


_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


class Incident(BaseModel):
    """An incident as the vendor published it, normalized."""

    service_id: str
    source_id: str
    title: str
    impact: Impact = Impact.NONE
    status: Phase = Phase.INVESTIGATING
    created_at: datetime
    started_at: datetime | None = None
    resolved_at: datetime | None = None
    updated_at: datetime | None = None
    url: str | None = None
    components: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    updates: list[Update] = Field(default_factory=list)

    @field_validator("source_id")
    @classmethod
    def _safe_source_id(cls, value: str) -> str:
        # Vendor ids end up in file paths and URLs. Anything outside a plain alphabet is replaced,
        # so an id like "../../overview" can never point outside the incident directory.
        return _UNSAFE_ID.sub("_", value)[:120] or "_"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        return f"{self.service_id}:{self.source_id}"

    @property
    def start(self) -> datetime:
        """When the incident began, using the vendor's backdated start when it gave one."""
        if self.started_at and self.started_at < self.created_at:
            return self.started_at
        return self.created_at

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None and self.status.is_open

    @property
    def retroactive(self) -> bool:
        """True when the vendor only posted the incident after it was already over."""
        if not self.updates:
            return False
        return not self.updates[0].status.is_open

    def duration_minutes(self) -> float | None:
        if self.resolved_at is None:
            return None
        return max(0.0, (self.resolved_at - self.start).total_seconds() / 60.0)


class Component(BaseModel):
    id: str
    name: str
    status: Health = Health.UNKNOWN
    group: str | None = None


class Snapshot(BaseModel):
    """Everything one poll of one status page told us."""

    service_id: str
    fetched_at: datetime = Field(default_factory=utcnow)
    ok: bool = True
    error: str | None = None
    latency_ms: float | None = None
    indicator: Impact = Impact.NONE
    description: str = ""
    components: list[Component] = Field(default_factory=list)
    incidents: list[Incident] = Field(default_factory=list)
    maintenances: list[Incident] = Field(default_factory=list)

    @property
    def health(self) -> Health:
        """The worst component state, or a state implied by open incidents."""
        worst = max((c.status for c in self.components), key=lambda h: h.rank, default=Health.OPERATIONAL)
        implied = {
            Impact.CRITICAL: Health.MAJOR_OUTAGE,
            Impact.MAJOR: Health.PARTIAL_OUTAGE,
            Impact.MINOR: Health.DEGRADED,
        }
        for inc in self.incidents:
            h = implied.get(inc.impact)
            if h and h.rank > worst.rank:
                worst = h
        if worst.rank == 0 and self.incidents:
            worst = Health.DEGRADED
        return worst
