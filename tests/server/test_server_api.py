"""The live API serves exactly what the static publisher writes, at the same paths."""

from __future__ import annotations

import gzip
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mercalli.api import ApiBuilder
from mercalli.catalog import Service
from mercalli.domain import Category
from mercalli.notify.config import Config
from mercalli.publish import publish
from mercalli.store import Database

from .support import NOW, SERVICES, SITE, TICK


def test_every_static_document_is_served_live_byte_for_byte(
    db: Database, artifacts: Path, tmp_path: Path, make_app: Callable[..., FastAPI]
) -> None:
    db.kv_set("server:last_tick", {"report": TICK, "finished_at": NOW.isoformat()})
    out = tmp_path / "site"
    publish(ApiBuilder(db, data_dir=artifacts, now=NOW, edition="self-hosted"), out, tick=TICK, site=SITE)
    app = make_app(artifacts=artifacts, config=Config(public_url=SITE))
    files = sorted(p for p in out.rglob("*") if p.is_file())
    api_files = [p for p in files if p.is_relative_to(out / "api" / "v1")]
    assert len(api_files) >= 12  # meta, overview, services, 4 services, 4 incidents, 3 artifacts
    with TestClient(app) as client:
        for path in files:
            url = "/" + path.relative_to(out).as_posix()
            resp = client.get(url)
            assert resp.status_code == 200, url
            assert resp.content == path.read_bytes(), url
    served = {"/" + p.relative_to(out).as_posix() for p in files}
    assert "/api/v1/replays.json" not in served
    assert {"/feeds/all.xml", "/feeds/github.xml", "/data/incidents.parquet"} <= served


def test_missing_documents_are_json_404s(make_app: Callable[..., FastAPI], db: Database, artifacts: Path) -> None:
    with TestClient(make_app(artifacts=artifacts)) as client:
        for url in (
            "/api/v1/replays.json",
            "/api/v1/services/nope.json",
            "/api/v1/incidents/github/nope.json",
            "/api/v1/whatever.json",
            "/feeds/nope.xml",
            "/data/secrets.txt",
            "/data/../graph.json",
        ):
            resp = client.get(url)
            assert resp.status_code == 404, url
            assert resp.headers["content-type"].startswith("application/json"), url


def test_documents_are_self_hosted_edition(make_app: Callable[..., FastAPI], db: Database) -> None:
    with TestClient(make_app()) as client:
        meta = client.get("/api/v1/meta.json").json()
        assert meta["edition"] == "self-hosted"
        assert meta["services"] == 4
        overview = client.get("/api/v1/overview.json").json()
        assert [d["id"] for d in overview["active"]] == ["cloudflare:cf1", "github:gh1"]
        assert overview["active"][1]["forecast"]["p50"] == 30.0
        assert overview["active"][0]["blast"][0]["service_id"] == "vercel"


def test_prepared_documents_support_etags_gzip_and_head(make_app: Callable[..., FastAPI], db: Database) -> None:
    with TestClient(make_app()) as client:
        plain = client.get("/api/v1/overview.json", headers={"Accept-Encoding": "identity"})
        assert plain.headers.get("content-encoding") is None
        etag = plain.headers["etag"]
        assert plain.headers["cache-control"] == "public, max-age=10"
        again = client.get("/api/v1/overview.json", headers={"If-None-Match": etag})
        assert again.status_code == 304
        assert again.content == b""
        zipped = client.get("/api/v1/overview.json", headers={"Accept-Encoding": "gzip"})
        assert zipped.headers["content-encoding"] == "gzip"
        assert zipped.content == plain.content  # httpx decodes it
        raw = client.build_request("GET", "/api/v1/overview.json", headers={"Accept-Encoding": "gzip"})
        body = client.send(raw, stream=True)
        assert gzip.decompress(b"".join(body.iter_raw())) == plain.content
        head = client.head("/api/v1/services.json")
        assert head.status_code == 200
        assert head.content == b""


def test_feeds_cover_quiet_services_and_use_the_request_host(make_app: Callable[..., FastAPI], db: Database) -> None:
    quiet_service = Service(
        id="okta", name="Okta", category=Category.IDENTITY, adapter="statuspage", base_url="https://status.okta.com"
    )
    db.sync_services([*SERVICES, quiet_service])
    with TestClient(make_app(), base_url="http://status.internal") as client:
        all_feed = client.get("/feeds/all.xml")
        assert all_feed.headers["content-type"].startswith("application/atom+xml")
        assert "http://status.internal/incident/?id=github:gh1" in all_feed.text
        quiet = client.get("/feeds/okta.xml")
        assert quiet.status_code == 200
        assert "<feed" in quiet.text
        assert "<entry>" not in quiet.text


def test_cors_is_open_for_reads(make_app: Callable[..., FastAPI], db: Database) -> None:
    with TestClient(make_app()) as client:
        resp = client.get("/api/v1/meta.json", headers={"Origin": "https://dash.example.org"})
        assert resp.headers["access-control-allow-origin"] == "*"


def test_data_files_answer_range_requests(make_app: Callable[..., FastAPI], db: Database, artifacts: Path) -> None:
    with TestClient(make_app(artifacts=artifacts)) as client:
        resp = client.get("/data/incidents.parquet", headers={"Range": "bytes=0-3"})
        assert resp.status_code == 206
        assert resp.content == b"PAR1"


def test_web_app_routing_and_cache_headers(make_app: Callable[..., FastAPI], db: Database, web: Path) -> None:
    with TestClient(make_app(web=web), follow_redirects=False) as client:
        home = client.get("/")
        assert "home" in home.text
        assert home.headers["cache-control"] == "no-cache"
        redirect = client.get("/incident")
        assert redirect.status_code in (307, 308)
        assert redirect.headers["location"].endswith("/incident/")
        page = client.get("/incident/?id=github:gh1")
        assert "incident" in page.text
        assert page.headers["cache-control"] == "no-cache"
        assert client.get("/incident/index.txt").headers["cache-control"] == "no-cache"
        chunk = client.get("/_next/static/chunks/app-3f9a1c.js")
        assert chunk.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert client.get("/favicon.ico").headers["cache-control"] == "public, max-age=3600"
        missing = client.get("/no/such/page/")
        assert missing.status_code == 404
        assert "not found" in missing.text
        assert client.get("/%2e%2e/%2e%2e/pyproject.toml").status_code == 404
        # The API still answers under the mounted app, with JSON errors rather than the 404 page.
        assert client.get("/api/v1/meta.json").status_code == 200
        unknown = client.get("/api/v1/nothing-here.json")
        assert unknown.status_code == 404
        assert unknown.json() == {"error": "document not found"}


def test_root_without_a_web_app_describes_the_api(
    make_app: Callable[..., FastAPI], db: Database, tmp_path: Path
) -> None:
    empty = tmp_path / "empty-web"
    empty.mkdir()
    with TestClient(make_app(web=empty)) as client:
        doc = client.get("/").json()
        assert doc["api"] == "/api/v1/meta.json"
        assert doc["stream"] == "/api/v1/stream"
