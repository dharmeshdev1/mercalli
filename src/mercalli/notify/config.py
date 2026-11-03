"""The config file of a self-hosted server: the services you depend on and where alerts go.

Secrets do not belong in the file. Any string value may reference the environment as ${NAME},
${NAME:-fallback} (fallback when unset or empty) or ${NAME-fallback} (fallback when unset), and $$
stands for a literal dollar sign. References are resolved once, when the file is loaded.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

EventKind = Literal["incident.opened", "incident.updated", "incident.resolved", "forecast", "blast"]
EVENT_KINDS: tuple[EventKind, ...] = ("incident.opened", "incident.updated", "incident.resolved", "forecast", "blast")
ImpactLevel = Literal["none", "maintenance", "minor", "major", "critical"]
IMPACT_RANK: dict[str, int] = {"none": 0, "maintenance": 0, "minor": 1, "major": 2, "critical": 3}
PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
PAGER_EVENTS: tuple[EventKind, ...] = ("incident.opened", "incident.resolved")

_REF = re.compile(r"\$(?:(\$)|\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?-)([^}]*))?\})")


class ConfigError(ValueError):
    """The config file cannot be used. Messages never echo values, which may be secrets."""


def interpolate(value: Any, env: Mapping[str, str], missing: set[str]) -> Any:
    """Resolve environment references in every string of a parsed YAML document."""
    if isinstance(value, str):
        return _REF.sub(lambda m: _resolve(m, env, missing), value)
    if isinstance(value, dict):
        return {k: interpolate(v, env, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, env, missing) for v in value]
    return value


def _resolve(m: re.Match[str], env: Mapping[str, str], missing: set[str]) -> str:
    if m.group(1):
        return "$"
    name, op, fallback = m.group(2), m.group(3), m.group(4) or ""
    if op == ":-":
        return env.get(name) or fallback
    if op == "-":
        return env.get(name, fallback)
    if name not in env:
        missing.add(name)
        return ""
    return env[name]


def _check_url(value: str) -> str:
    value = value.strip()
    if value and urlsplit(value).scheme not in ("http", "https"):
        raise ValueError("must be an http or https URL")
    return value


UrlStr = Annotated[str, AfterValidator(_check_url)]


class RateLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    per_minute: float = Field(gt=0)
    burst: int = Field(default=5, ge=1)


class ChannelBase(BaseModel):
    """Fields every channel shares. A channel is on when its destination is set, unless `enabled` says otherwise."""

    model_config = ConfigDict(extra="forbid")

    type: str
    name: str = Field(min_length=1, max_length=64)
    enabled: bool | None = None
    events: list[EventKind] = Field(default_factory=lambda: list(EVENT_KINDS))
    min_impact: ImpactLevel = "minor"
    services: list[str] | None = None
    rate_limit: RateLimit | None = None
    timeout: float = Field(default=10.0, gt=0, le=120)

    # Used when a channel sets no rate limit: roughly what each receiving service tolerates.
    default_rate: ClassVar[RateLimit] = RateLimit(per_minute=60, burst=10)
    target_field: ClassVar[str] = "url"

    @property
    def target(self) -> str:
        """Where deliveries go. Empty means the channel is not set up."""
        return str(getattr(self, "url", ""))

    @property
    def active(self) -> bool:
        return bool(self.target) if self.enabled is None else self.enabled

    @property
    def rate(self) -> RateLimit:
        return self.rate_limit or self.default_rate

    @model_validator(mode="after")
    def _enabled_needs_target(self) -> Self:
        if self.enabled and not self.target:
            raise ValueError(f"channel is enabled but {self.target_field} is empty")
        return self


class SlackChannel(ChannelBase):
    type: Literal["slack"]
    url: UrlStr = ""
    # Slack asks incoming webhooks to stay near one message a second.
    default_rate: ClassVar[RateLimit] = RateLimit(per_minute=60, burst=5)


class DiscordChannel(ChannelBase):
    type: Literal["discord"]
    url: UrlStr = ""
    username: str = "Mercalli"
    # Discord allows about 30 webhook messages a minute per channel.
    default_rate: ClassVar[RateLimit] = RateLimit(per_minute=30, burst=5)


class TeamsChannel(ChannelBase):
    type: Literal["teams"]
    url: UrlStr = ""
    default_rate: ClassVar[RateLimit] = RateLimit(per_minute=30, burst=5)


class WebhookChannel(ChannelBase):
    type: Literal["webhook"]
    url: UrlStr = ""
    secret: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    default_rate: ClassVar[RateLimit] = RateLimit(per_minute=120, burst=20)


class EmailSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True)

    host: str = ""
    port: int | None = Field(default=None, gt=0, lt=65536)
    security: Literal["starttls", "ssl", "none"] = "starttls"
    username: str = ""
    password: str = ""
    sender: str = Field(default="", alias="from")
    to: list[str] = Field(default_factory=list)
    timeout: float = Field(default=20.0, gt=0, le=120)

    @field_validator("to", mode="before")
    @classmethod
    def _split_addresses(cls, value: Any) -> Any:
        # Lets one environment variable hold several recipients.
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def effective_port(self) -> int:
        return self.port or {"ssl": 465, "starttls": 587, "none": 25}[self.security]


class EmailChannel(ChannelBase):
    type: Literal["email"]
    settings: EmailSettings = Field(default_factory=EmailSettings)
    default_rate: ClassVar[RateLimit] = RateLimit(per_minute=20, burst=5)
    target_field: ClassVar[str] = "settings.host or settings.to"

    @property
    def target(self) -> str:
        return self.settings.host if self.settings.host and self.settings.to else ""

    @model_validator(mode="after")
    def _needs_sender(self) -> Self:
        if self.active and not self.settings.sender:
            raise ValueError("an email channel needs settings.from")
        return self


class PagerDutySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routing_key: str = ""
    url: UrlStr = PAGERDUTY_EVENTS_URL


class PagerDutyChannel(ChannelBase):
    type: Literal["pagerduty"]
    settings: PagerDutySettings = Field(default_factory=PagerDutySettings)
    # A pager is for things that need a person: open and close alerts, no commentary in between.
    events: list[EventKind] = Field(default_factory=lambda: list(PAGER_EVENTS))
    # The Events API v2 accepts 120 events a minute per routing key.
    default_rate: ClassVar[RateLimit] = RateLimit(per_minute=120, burst=20)
    target_field: ClassVar[str] = "settings.routing_key"

    @property
    def target(self) -> str:
        return self.settings.routing_key


Channel = Annotated[
    SlackChannel | DiscordChannel | TeamsChannel | WebhookChannel | EmailChannel | PagerDutyChannel,
    Field(discriminator="type"),
]


class Retention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    polls_days: float = Field(default=7, gt=0)
    events_days: float = Field(default=30, gt=0)
    predictions_days: float = Field(default=180, gt=0)
    deliveries_days: float = Field(default=30, gt=0)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_url: UrlStr | None = None
    stack: list[str] = Field(default_factory=list)
    blast_threshold: float = Field(default=0.3, ge=0, le=1)
    max_event_age_minutes: float = Field(default=360, gt=0)
    channels: list[Channel] = Field(default_factory=list)
    retention: Retention = Field(default_factory=Retention)

    @field_validator("public_url")
    @classmethod
    def _trim_url(cls, value: str | None) -> str | None:
        return value.rstrip("/") or None if value else None

    @field_validator("stack")
    @classmethod
    def _unique_stack(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(s.strip() for s in value if s.strip()))

    @model_validator(mode="after")
    def _check_channels(self) -> Self:
        names: set[str] = set()
        stack = set(self.stack)
        for ch in self.channels:
            if ch.name in names:
                raise ValueError(f"two channels are named {ch.name!r}")
            names.add(ch.name)
            outside = sorted(set(ch.services or []) - stack)
            if outside:
                raise ValueError(f"channel {ch.name!r} lists services that are not in the stack: {', '.join(outside)}")
        return self

    @property
    def active_channels(self) -> list[ChannelBase]:
        return [ch for ch in self.channels if ch.active]


def load_config(path: Path, env: Mapping[str, str] | None = None) -> Config:
    """Read, interpolate and validate a config file, or raise ConfigError saying what is wrong."""
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must hold a mapping at the top level")
    missing: set[str] = set()
    data = interpolate(raw, os.environ if env is None else env, missing)
    if missing:
        raise ConfigError(f"{path} references environment variables that are not set: {', '.join(sorted(missing))}")
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path} is not a valid config:\n{describe(exc)}") from None


def describe(exc: ValidationError) -> str:
    """Validation errors as `location: problem` lines, leaving out the offending values."""
    lines = []
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        loc = ".".join(str(p) for p in err["loc"]) or "(top level)"
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)
