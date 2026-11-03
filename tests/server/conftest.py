"""Fixtures: a seeded SQLite database, offline artifacts, an exported web app and an app factory."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from mercalli.notify.config import Config
from mercalli.server.app import create_app
from mercalli.store import Database

from .support import NOW, FakeClient, seed, settings_for


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "mercalli.db"


@pytest.fixture
def db(db_path: Path) -> Database:
    database = Database(f"sqlite:///{db_path}")
    seed(database)
    return database


@pytest.fixture
def artifacts(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    (root / "data").mkdir(parents=True)
    graph = {
        "generated_at": "2026-11-01T00:00:00Z",
        "nodes": [
            {"id": "cloudflare", "name": "Cloudflare", "category": "edge", "kind": "service", "dependents": 25},
            {"id": "vercel", "name": "Vercel", "category": "hosting", "kind": "service", "dependents": 3},
        ],
        "edges": [
            {
                "source": "vercel",
                "target": "cloudflare",
                "kinds": ["edge"],
                "weight": 0.9,
                "evidence": ["header:cf-ray"],
            }
        ],
    }
    (root / "graph.json").write_text(json.dumps(graph))
    (root / "model.json").write_text(json.dumps({"summary": {"recovery": {"coverage_80": 0.81}}, "trained_at": "x"}))
    (root / "census-summary.json").write_text(json.dumps({"generated_at": "2026-10-30", "counts": {"total": 1000}}))
    (root / "data" / "dataset.json").write_text(json.dumps({"rows": 3}))
    (root / "data" / "incidents.parquet").write_bytes(b"PAR1" + bytes(range(256)) * 4 + b"PAR1")
    return root


@pytest.fixture
def web(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    (root / "incident").mkdir(parents=True)
    (root / "_next" / "static" / "chunks").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>home</title>")
    (root / "404.html").write_text("<!doctype html><title>not found</title>")
    (root / "incident" / "index.html").write_text("<!doctype html><title>incident</title>")
    (root / "incident" / "index.txt").write_text("rsc payload")
    (root / "_next" / "static" / "chunks" / "app-3f9a1c.js").write_text("console.log(1)")
    (root / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    return root


@pytest.fixture
def make_app(db_path: Path) -> Callable[..., FastAPI]:
    def factory(
        *,
        config: Config | None = None,
        engine_factory: Callable[[], Any] | None = None,
        client_factory: Callable[[], Any] | None = None,
        **overrides: Any,
    ) -> FastAPI:
        return create_app(
            settings_for(db_path, **overrides),
            config=config or Config(),
            clock=lambda: NOW,
            engine_factory=engine_factory,
            client_factory=client_factory or FakeClient,
        )

    return factory
