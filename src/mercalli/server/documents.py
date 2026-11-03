"""The documents the server keeps ready, and the offline artifacts it serves from disk.

Overview and services are the heavy documents and the ones every client polls, so they are built
once after each tick, encoded once (plain and gzip) and served from memory. Feeds are rendered
from the same overview, so a feed and the page that links to it never disagree.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson

from mercalli.api import ApiBuilder
from mercalli.publish import render_feed as _render_feed
from mercalli.store import Database

log = logging.getLogger("mercalli.server")


@dataclass(frozen=True)
class Encoded:
    body: bytes
    gzipped: bytes
    etag: str


def encode(doc: Any) -> Encoded:
    # The same serialization as the static publisher, so both editions return identical bytes.
    body = orjson.dumps(doc, option=orjson.OPT_NON_STR_KEYS)
    etag = f'"{hashlib.blake2b(body, digest_size=12).hexdigest()}"'
    return Encoded(body, gzip.compress(body, compresslevel=6, mtime=0), etag)


class ArtifactStore:
    """JSON artifacts produced offline (graph, model card, census), parsed once per change on disk.

    Documents handed out are shared between requests and must be treated as read only.
    """

    def __init__(self, root: Path | None) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._parsed: dict[str, tuple[tuple[int, int], Any, Encoded | None]] = {}

    def _path(self, name: str) -> Path | None:
        if self.root is None or "/" in name or "\\" in name or name.startswith("."):
            return None
        return self.root / name

    def parsed(self, name: str) -> Any:
        entry = self._entry(name)
        return entry[1] if entry else None

    def encoded(self, name: str) -> Encoded | None:
        entry = self._entry(name)
        if entry is None:
            return None
        stamp, doc, enc = entry
        if enc is None:
            enc = encode(doc)
            with self._lock:
                current = self._parsed.get(name)
                if current and current[0] == stamp:
                    self._parsed[name] = (stamp, doc, enc)
        return enc

    def data_file(self, name: str) -> Path | None:
        """A file from the public dataset (artifacts/data/), the way the static site publishes it."""
        if self.root is None or "/" in name or name.startswith("."):
            return None
        path = self.root / "data" / name
        return path if path.is_file() else None

    def _entry(self, name: str) -> tuple[tuple[int, int], Any, Encoded | None] | None:
        path = self._path(name)
        if path is None:
            return None
        try:
            st = path.stat()
        except OSError:
            self._parsed.pop(name, None)
            return None
        stamp = (st.st_mtime_ns, st.st_size)
        cached = self._parsed.get(name)
        if cached and cached[0] == stamp:
            return cached
        with self._lock:
            cached = self._parsed.get(name)
            if cached and cached[0] == stamp:
                return cached
            try:
                doc = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                # Usually a file caught halfway through being replaced. Keep serving the last good copy.
                log.warning("could not read artifact %s: %s", name, exc)
                return cached
            self._parsed[name] = (stamp, doc, None)
            return self._parsed[name]


class LiveBuilder(ApiBuilder):
    """ApiBuilder reading artifacts through the shared store instead of from disk on every request."""

    def __init__(self, db: Database, store: ArtifactStore, *, now: datetime, edition: str) -> None:
        super().__init__(db, data_dir=store.root, now=now, edition=edition)
        self.store = store

    def artifact(self, name: str) -> Any:
        return self.store.parsed(name)


@dataclass
class DocumentSet:
    built_at: datetime
    meta: Encoded
    overview: Encoded
    services: Encoded
    entries: list[dict[str, Any]]
    names: dict[str, str]
    open_by_impact: dict[str, int]
    _feeds: dict[tuple[str, str], bytes] = field(default_factory=dict)

    def feed(self, name: str, site: str) -> bytes | None:
        """The Atom feed for `all` or one service id, with links pointing at `site`."""
        key = (name, site)
        cached = self._feeds.get(key)
        if cached is not None:
            return cached
        if name == "all":
            title, entries = "Mercalli: all incidents", self.entries
        elif name in self.names:
            # Quiet services get an empty feed rather than a 404, so a feed reader can subscribe early.
            title, entries = f"Mercalli: {self.names[name]}", [e for e in self.entries if e["service_id"] == name]
        else:
            return None
        data = render_feed(f"{name}.xml", title, entries, site)
        if len(self._feeds) > 4096:
            self._feeds.clear()
        self._feeds[key] = data
        return data


def render_feed(filename: str, title: str, entries: list[dict[str, Any]], site: str) -> bytes:
    return _render_feed(filename, title, entries, site)


def build_documents(
    db: Database, store: ArtifactStore, *, now: datetime, edition: str, tick: dict[str, Any] | None
) -> DocumentSet:
    builder = LiveBuilder(db, store, now=now, edition=edition)
    overview = builder.overview()
    services = builder.services_doc()
    meta = builder.meta(tick)
    open_docs = overview["active"] + overview["long_running"] + overview["notices"]
    return DocumentSet(
        built_at=now,
        meta=encode(meta),
        overview=encode(overview),
        services=encode(services),
        entries=overview["active"] + overview["recent"],
        names={sid: row.name for sid, row in builder.services.items()},
        open_by_impact=dict(Counter(d["impact"] for d in open_docs)),
    )
