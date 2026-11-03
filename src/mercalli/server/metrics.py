"""Prometheus metrics for the server, kept in a registry of its own.

A private registry means two apps in one process (tests, or an embedding application) never
collide over metric names, and /metrics shows exactly what this server measures.
"""

from __future__ import annotations

import time

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    GCCollector,
    Histogram,
    PlatformCollector,
    ProcessCollector,
)
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.types import Message as AsgiMessage

from mercalli import __version__

IMPACTS = ("critical", "major", "minor", "none", "maintenance")
CHANNEL_TYPES = ("slack", "discord", "teams", "webhook", "email", "pagerduty")
_METHODS = {"GET", "HEAD", "POST", "OPTIONS"}


class Metrics:
    def __init__(self, edition: str = "self-hosted") -> None:
        r = self.registry = CollectorRegistry()
        ProcessCollector(registry=r)
        PlatformCollector(registry=r)
        GCCollector(registry=r)
        self.ticks = Counter("mercalli_ticks", "Engine ticks run by this process, by result.", ["result"], registry=r)
        self.tick_duration = Histogram(
            "mercalli_tick_duration_seconds",
            "How long a successful tick took.",
            registry=r,
            buckets=(1, 2.5, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300),
        )
        self.last_tick = Gauge(
            "mercalli_last_tick_timestamp_seconds", "Unix time the last successful tick finished.", registry=r
        )
        self.services_polled = Gauge(
            "mercalli_services_polled", "Status pages polled in the last tick, by result.", ["result"], registry=r
        )
        self.incident_changes = Counter(
            "mercalli_incident_changes", "Incident changes found by ticks, by kind.", ["kind"], registry=r
        )
        self.open_incidents = Gauge("mercalli_open_incidents", "Open incidents by impact.", ["impact"], registry=r)
        self.notifications = Counter(
            "mercalli_notifications",
            "Notification deliveries by channel type and result.",
            ["channel_type", "result"],
            registry=r,
        )
        self.http_requests = Counter(
            "mercalli_http_requests",
            "HTTP requests by method, route and status.",
            ["method", "route", "status"],
            registry=r,
        )
        self.http_latency = Histogram(
            "mercalli_http_request_duration_seconds",
            "Time to the first response byte, by route.",
            ["method", "route"],
            registry=r,
            buckets=(0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
        )
        self.stream_clients = Gauge("mercalli_stream_clients", "Open live stream connections.", registry=r)
        self.leader = Gauge("mercalli_scheduler_leader", "1 when this process runs the scheduler.", registry=r)
        Gauge("mercalli_build_info", "Version and edition of this server.", ["version", "edition"], registry=r).labels(
            __version__, edition
        ).set(1)
        # Series exist from the start, so dashboards and alerts see zeros instead of gaps.
        for result in ("ok", "error", "timeout"):
            self.ticks.labels(result)
        for result in ("ok", "failed"):
            self.services_polled.labels(result)
        for impact in IMPACTS:
            self.open_incidents.labels(impact)
        for ctype in CHANNEL_TYPES:
            for result in ("sent", "failed"):
                self.notifications.labels(ctype, result)

    def observe_request(self, scope: Scope, status: int, seconds: float) -> None:
        method = scope.get("method", "GET")
        method = method if method in _METHODS else "other"
        route = _route_label(scope)
        self.http_requests.labels(method, route, str(status)).inc()
        self.http_latency.labels(method, route).observe(seconds)


def _route_label(scope: Scope) -> str:
    # The route template, never the raw path, so labels stay few however many ids exist.
    route = scope.get("route")
    if isinstance(route, Mount):
        return route.name or "mount"
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else "unmatched"


class HttpMetrics:
    """Pure ASGI middleware, so streaming responses pass through untouched."""

    def __init__(self, app: ASGIApp, metrics: Metrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status: int | None = None

        async def send_wrapper(message: AsgiMessage) -> None:
            nonlocal status
            if message["type"] == "http.response.start" and status is None:
                status = int(message["status"])
                self.metrics.observe_request(scope, status, time.perf_counter() - start)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if status is None:
                self.metrics.observe_request(scope, 500, time.perf_counter() - start)
