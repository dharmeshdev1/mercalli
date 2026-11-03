"""The HTTP surface of the self-hosted server.

The API answers the same paths with the same documents as the static public edition (see
docs/api.md), so the web app and any client work against either. On top of that it adds the live
stream, health and readiness probes, Prometheus metrics, and serves the exported web app.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import orjson
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse

from mercalli import __version__
from mercalli.domain import utcnow
from mercalli.notify.config import Config, load_config
from mercalli.server.context import ServerContext
from mercalli.server.documents import Encoded
from mercalli.server.metrics import HttpMetrics
from mercalli.server.scheduler import EngineLike
from mercalli.server.settings import ServerSettings
from mercalli.server.stream import event_stream, parse_last_event_id
from mercalli.server.web import WebApp

log = logging.getLogger("mercalli.server")

API = "/api/v1"
GET_HEAD = ["GET", "HEAD"]
ARTIFACTS = ("graph.json", "model.json", "census-summary.json", "replays.json")
DATA_FILES = {"incidents.parquet": "application/vnd.apache.parquet", "dataset.json": "application/json"}
JSON_TYPE = "application/json"
# Documents change once a tick. A short max-age absorbs bursts while staying close to live.
API_CACHE = "public, max-age=10"
NO_STORE = {"Cache-Control": "no-store"}


def create_app(
    settings: ServerSettings,
    *,
    config: Config | None = None,
    clock: Callable[[], datetime] = utcnow,
    engine_factory: Callable[[], EngineLike] | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> FastAPI:
    if config is None:
        config = load_config(settings.config_path) if settings.config_path else Config()
    if settings.public_url:
        config = config.model_copy(update={"public_url": settings.public_url.rstrip("/")})
    ctx = ServerContext(settings, config, clock=clock, engine_factory=engine_factory, client_factory=client_factory)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await ctx.start()
        try:
            yield
        finally:
            await ctx.stop()

    # The interactive docs routes would shadow pages of the web app, and docs/api.md is the contract.
    app = FastAPI(
        title="Mercalli", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.ctx = ctx
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=GET_HEAD,
        allow_headers=["Last-Event-ID", "If-None-Match"],
        expose_headers=["ETag"],
    )
    app.add_middleware(HttpMetrics, metrics=ctx.metrics)
    _add_probes(app, ctx)
    _add_api(app, ctx)
    _add_web(app, ctx)
    return app


def _json(doc: Any, status: int = 200) -> Response:
    return Response(
        orjson.dumps(doc, option=orjson.OPT_NON_STR_KEYS),
        status_code=status,
        media_type=JSON_TYPE,
        headers={"Cache-Control": API_CACHE},
    )


def _not_found(what: str) -> Response:
    return JSONResponse({"error": f"{what} not found"}, status_code=404, headers={"Cache-Control": API_CACHE})


def _encoded(request: Request, enc: Encoded) -> Response:
    """A prepared document, with conditional requests and the gzip copy made when it was built."""
    headers = {"ETag": enc.etag, "Cache-Control": API_CACHE, "Vary": "Accept-Encoding"}
    wanted = {tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")}
    if enc.etag in wanted:
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", ""):
        headers["Content-Encoding"] = "gzip"
        return Response(enc.gzipped, media_type=JSON_TYPE, headers=headers)
    return Response(enc.body, media_type=JSON_TYPE, headers=headers)


def _add_probes(app: FastAPI, ctx: ServerContext) -> None:
    @app.api_route("/healthz", methods=GET_HEAD)
    async def healthz() -> Response:
        return JSONResponse({"status": "ok", "version": __version__}, headers=NO_STORE)

    @app.api_route("/readyz", methods=GET_HEAD)
    async def readyz() -> Response:
        ready, detail = await ctx.readiness()
        return JSONResponse(detail, status_code=200 if ready else 503, headers=NO_STORE)

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(ctx.metrics.registry), media_type=CONTENT_TYPE_LATEST, headers=NO_STORE)


def _add_api(app: FastAPI, ctx: ServerContext) -> None:
    @app.api_route(f"{API}/meta.json", methods=GET_HEAD)
    def meta(request: Request) -> Response:
        return _encoded(request, ctx.documents().meta)

    @app.api_route(f"{API}/overview.json", methods=GET_HEAD)
    def overview(request: Request) -> Response:
        return _encoded(request, ctx.documents().overview)

    @app.api_route(f"{API}/services.json", methods=GET_HEAD)
    def services(request: Request) -> Response:
        return _encoded(request, ctx.documents().services)

    @app.api_route(API + "/services/{service_id}.json", methods=GET_HEAD)
    def service(service_id: str) -> Response:
        doc = ctx.builder().service_doc(service_id)
        return _json(doc) if doc is not None else _not_found("service")

    @app.api_route(API + "/incidents/{service_id}/{source_id}.json", methods=GET_HEAD)
    def incident(service_id: str, source_id: str) -> Response:
        doc = ctx.builder().incident_doc(f"{service_id}:{source_id}")
        return _json(doc) if doc is not None else _not_found("incident")

    for name in ARTIFACTS:
        _add_artifact(app, ctx, name)

    @app.get(f"{API}/stream")
    async def stream(request: Request) -> Response:
        if ctx.hub.full:
            busy = {"error": "too many live stream clients"}
            return JSONResponse(busy, status_code=503, headers={"Retry-After": "30"})
        last = parse_last_event_id(request.headers.get("last-event-id") or request.query_params.get("last_event_id"))
        return StreamingResponse(
            event_stream(ctx.hub, last, heartbeat=ctx.settings.stream_heartbeat),
            media_type="text/event-stream",
            # X-Accel-Buffering stops nginx (and ingress-nginx) from holding events back in a buffer.
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.api_route("/api/{rest:path}", methods=GET_HEAD)
    def unknown_api(rest: str) -> Response:
        return _not_found("document")

    @app.api_route("/feeds/{name}.xml", methods=GET_HEAD)
    def feed(name: str, request: Request) -> Response:
        site = ctx.config.public_url or str(request.base_url).rstrip("/")
        data = ctx.documents().feed(name, site)
        if data is None:
            return _not_found("feed")
        return Response(
            data, media_type="application/atom+xml; charset=utf-8", headers={"Cache-Control": "public, max-age=60"}
        )

    @app.api_route("/data/{name}", methods=GET_HEAD)
    def data_file(name: str) -> Response:
        media = DATA_FILES.get(name)
        path = ctx.store.data_file(name) if media else None
        if path is None:
            return _not_found("file")
        # FileResponse answers Range requests, which is how browsers read parquet files in pieces.
        return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=300"})


def _add_artifact(app: FastAPI, ctx: ServerContext, name: str) -> None:
    def artifact(request: Request) -> Response:
        enc = ctx.store.encoded(name)
        return _encoded(request, enc) if enc is not None else _not_found(name)

    app.add_api_route(f"{API}/{name}", artifact, methods=GET_HEAD, name=name)


def _add_web(app: FastAPI, ctx: ServerContext) -> None:
    web = ctx.settings.web
    if web is not None and (web / "index.html").is_file():
        app.mount("/", WebApp(directory=web, html=True), name="web")
        return
    if web is not None:
        log.warning("no index.html in %s; serving the API without the web app", web)

    @app.get("/")
    async def index() -> Response:
        return JSONResponse(
            {
                "name": "Mercalli",
                "edition": ctx.settings.edition,
                "version": __version__,
                "api": f"{API}/meta.json",
                "stream": f"{API}/stream",
                "feeds": "/feeds/all.xml",
            }
        )
