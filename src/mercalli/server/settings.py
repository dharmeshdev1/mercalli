"""Runtime settings of `mercalli serve`, as opposed to the YAML config file it reads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerSettings:
    db_url: str = "sqlite:///mercalli.db"
    interval: float = 60.0
    jitter: float = 0.1  # each tick starts up to this fraction of the interval early or late
    artifacts: Path | None = None
    cache_dir: Path | None = None
    web: Path | None = None
    config_path: Path | None = None
    public_url: str | None = None
    scheduler: bool = True
    edition: str = "self-hosted"
    tick_timeout: float | None = None
    notify_timeout: float | None = None
    shutdown_grace: float = 20.0
    stream_heartbeat: float = 15.0
    stream_poll: float = 1.0
    max_stream_clients: int = 500
    follower_poll: float = 10.0
    prune_every: float = 24 * 3600.0

    @property
    def effective_tick_timeout(self) -> float:
        # Generous: a slow tick still produces good data, but a hung one must not stop the server.
        return self.tick_timeout or max(300.0, 5 * self.interval)

    @property
    def effective_notify_timeout(self) -> float:
        return self.notify_timeout or max(30.0, self.interval)
