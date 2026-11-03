"""Write the API as static files, plus Atom feeds, for the public edition.

Files are written to a temporary directory and swapped in, so a reader never sees a half written
tree. Incident documents are only written for incidents that are open or ended recently, which
bounds the size of each deploy no matter how long the service has been running.
"""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import orjson
from sqlalchemy import or_, select

from mercalli.api import ApiBuilder, incident_path
from mercalli.store import IncidentRow

SITE = "https://dharmeshdev1.github.io/mercalli"


def _dump(path: Path, doc: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = orjson.dumps(doc, option=orjson.OPT_NON_STR_KEYS)
    path.write_bytes(data)
    return len(data)


def publish(
    builder: ApiBuilder, out: Path, *, tick: dict[str, Any] | None = None, incident_days: int = 14, site: str = SITE
) -> dict[str, int]:
    """Write `api/v1/...` and `feeds/...` under `out`. Returns file counts and bytes written."""
    stage = out.with_name(out.name + ".staging")
    if stage.exists():
        shutil.rmtree(stage)
    api = stage / "api" / "v1"
    stats = {"files": 0, "bytes": 0}

    root = api.resolve()

    def write(rel: str, doc: Any) -> None:
        target = (api / rel).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"refusing to write outside the API tree: {rel}")
        stats["bytes"] += _dump(target, doc)
        stats["files"] += 1

    write("meta.json", builder.meta(tick))
    overview = builder.overview()
    write("overview.json", overview)
    write("services.json", builder.services_doc())
    for sid in builder.services:
        doc = builder.service_doc(sid)
        if doc:
            write(f"services/{sid}.json", doc)

    with builder.db.session() as s:
        ids = list(
            s.scalars(
                select(IncidentRow.id).where(
                    or_(
                        IncidentRow.resolved_at.is_(None),
                        IncidentRow.resolved_at >= builder.now - timedelta(days=incident_days),
                    )
                )
            )
        )
    for iid in ids:
        doc = builder.incident_doc(iid)
        if doc:
            write(incident_path(iid), doc)

    for name in ("graph.json", "model.json", "census-summary.json", "replays.json"):
        art = builder.artifact(name)
        if art is not None:
            write(name, art)

    # The public incident dataset, for querying in the browser.
    if builder.data_dir and (builder.data_dir / "data" / "incidents.parquet").exists():
        (stage / "data").mkdir(parents=True, exist_ok=True)
        for f in ("incidents.parquet", "dataset.json"):
            src = builder.data_dir / "data" / f
            if src.exists():
                shutil.copy2(src, stage / "data" / f)
                stats["bytes"] += src.stat().st_size
                stats["files"] += 1

    feeds = stage / "feeds"
    feeds.mkdir(parents=True, exist_ok=True)
    entries = overview["active"] + overview["recent"]
    stats["bytes"] += _write_feed(feeds / "all.xml", "Mercalli: all incidents", entries, site)
    by_service: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_service.setdefault(e["service_id"], []).append(e)
    for sid, items in by_service.items():
        stats["bytes"] += _write_feed(feeds / f"{sid}.xml", f"Mercalli: {builder.service_name(sid)}", items, site)
    stats["files"] += 1 + len(by_service)

    # Swap the new tree in place of the old one.
    for sub in ("api", "feeds", "data"):
        if not (stage / sub).exists():
            continue
        target = out / sub
        if target.exists():
            shutil.rmtree(target)
        out.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage / sub), str(target))
    shutil.rmtree(stage, ignore_errors=True)
    return stats


def render_feed(feed_name: str, title: str, entries: list[dict[str, Any]], site: str = SITE) -> bytes:
    """An Atom feed for `entries` (incident summaries), as bytes."""
    updated = max((e.get("latest", {}) or {}).get("at") or e["started_at"] for e in entries) if entries else None
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"<title>{escape(title)}</title>",
        f'<link href="{site}/"/>',
        f"<id>{site}/feeds/{feed_name}</id>",
        f"<updated>{updated or '1970-01-01T00:00:00Z'}</updated>",
    ]
    for e in entries:
        state = "Resolved" if e.get("resolved_at") else e["status"].capitalize()
        body = (e.get("latest") or {}).get("body") or ""
        link = f"{site}/incident/?id={e['id']}"
        at = (e.get("latest") or {}).get("at") or e["started_at"]
        parts += [
            "<entry>",
            f"<title>{escape(f'{e["service_name"]}: {e["title"]} ({state})')}</title>",
            f'<link href="{escape(link)}"/>',
            f"<id>{escape(link)}#{escape(at)}</id>",
            f"<updated>{at}</updated>",
            f"<summary>{escape(body[:500])}</summary>",
            "</entry>",
        ]
    parts.append("</feed>")
    return "\n".join(parts).encode()


def _write_feed(path: Path, title: str, entries: list[dict[str, Any]], site: str) -> int:
    data = render_feed(path.name, title, entries, site)
    path.write_bytes(data)
    return len(data)
