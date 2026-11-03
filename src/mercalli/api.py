"""The JSON API, built once and served two ways.

The public edition writes these documents to static files every few minutes. A self-hosted
server returns the same documents from live queries. The web app cannot tell the difference,
which keeps one client for both. The shapes are documented in docs/api.md and pinned by tests.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from mercalli import __version__
from mercalli.domain import Impact, utcnow
from mercalli.store import BlastRow, Database, IncidentRow, PredictionRow, ServiceRow

API_VERSION = 1

# Providers drawn as traces on the seismograph, in order. Missing ones are skipped.
SEISMOGRAPH = [
    "aws",
    "azure",
    "gcp",
    "cloudflare",
    "fastly",
    "akamai",
    "github",
    "openai",
    "vercel",
    "netlify",
    "slack",
    "twilio",
    "discord",
    "shopify",
    "zoom",
    "atlassian",
    "datadog",
    "okta",
]
_IMPACT_WEIGHT = {"critical": 3.0, "major": 2.0, "minor": 1.0, "none": 0.5, "maintenance": 0.25}
_SCORE_WEIGHT = {"critical": 4.0, "major": 2.0, "minor": 1.0}
LONG_RUNNING_MIN = 7 * 24 * 60  # open longer than a week: a vendor that forgot, not an outage in progress
HUB_DEPENDENTS = 20
NUMERALS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
LABELS = ["Not felt", "Weak", "Weak", "Light", "Moderate", "Strong", "Very strong", "Severe", "Violent", "Extreme"]
# Used until intensity.json has been computed from history: level n+2 means the score is at or
# above thresholds[n], which history puts at percentiles[n] of hours.
DEFAULT_PERCENTILES = [50, 75, 90, 95, 98, 99, 99.5, 99.8, 99.95]
DEFAULT_THRESHOLDS = [4, 8, 12, 16, 22, 28, 36, 48, 64]


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def incident_path(incident_id: str) -> str:
    service_id, _, source_id = incident_id.partition(":")
    safe = re.compile(r"[^A-Za-z0-9_-]")
    return f"incidents/{safe.sub('_', service_id)}/{safe.sub('_', source_id)}.json"


class ApiBuilder:
    """Builds every API document from one database at one moment."""

    def __init__(
        self, db: Database, *, data_dir: Path | None = None, now: datetime | None = None, edition: str = "public"
    ) -> None:
        self.db = db
        self.now = now or utcnow()
        self.edition = edition
        self.data_dir = data_dir
        with db.session() as s:
            self.services = {r.id: r for r in s.scalars(select(ServiceRow))}
        self._artifacts: dict[str, Any] = {}
        self._reliability_cache: dict[int, dict[str, dict[str, Any]]] = {}
        self._edges: tuple[dict[str, list[Any]], dict[str, list[Any]]] | None = None

    # Shared pieces ---------------------------------------------------------------------------------

    def artifact(self, name: str) -> Any:
        """A JSON artifact produced offline (graph, census summary, model card), or None."""
        if name not in self._artifacts:
            path = self.data_dir / name if self.data_dir else None
            self._artifacts[name] = json.loads(path.read_text()) if path and path.exists() else None
        return self._artifacts[name]

    def latest_forecasts(self, ids: list[str]) -> dict[str, PredictionRow]:
        if not ids:
            return {}
        with self.db.session() as s:
            sub = (
                select(PredictionRow.incident_id, func.max(PredictionRow.made_at).label("m"))
                .where(PredictionRow.incident_id.in_(ids))
                .group_by(PredictionRow.incident_id)
                .subquery()
            )
            rows = s.scalars(
                select(PredictionRow).join(
                    sub, (PredictionRow.incident_id == sub.c.incident_id) & (PredictionRow.made_at == sub.c.m)
                )
            )
            return {r.incident_id: r for r in rows}

    def latest_blast(self, ids: list[str], limit: int) -> dict[str, list[dict[str, Any]]]:
        """The most recent blast radius list for each incident, most likely first."""
        if not ids:
            return {}
        with self.db.session() as s:
            latest = (
                select(BlastRow.upstream_incident_id, func.max(BlastRow.made_at).label("m"))
                .where(BlastRow.upstream_incident_id.in_(ids))
                .group_by(BlastRow.upstream_incident_id)
                .subquery()
            )
            rows = s.scalars(
                select(BlastRow)
                .join(
                    latest,
                    (BlastRow.upstream_incident_id == latest.c.upstream_incident_id) & (BlastRow.made_at == latest.c.m),
                )
                .order_by(BlastRow.upstream_incident_id, BlastRow.probability.desc())
            ).all()
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            items = out.setdefault(r.upstream_incident_id, [])
            if len(items) < limit:
                items.append(
                    {
                        "service_id": r.service_id,
                        "service_name": self._name(r.service_id),
                        "probability": round(r.probability, 4),
                        "evidence": r.evidence,
                    }
                )
        return out

    def summary(self, incident_id: str) -> dict[str, Any] | None:
        """One incident summary with its latest forecast and blast radius, as in overview.json."""
        with self.db.session() as s:
            row = s.scalar(
                select(IncidentRow).options(selectinload(IncidentRow.updates)).where(IncidentRow.id == incident_id)
            )
        if row is None:
            return None
        return self.incident_summary(
            row,
            self.latest_forecasts([incident_id]).get(incident_id),
            self.latest_blast([incident_id], limit=8).get(incident_id),
        )

    def service_name(self, service_id: str) -> str:
        return self._name(service_id)

    def _name(self, service_id: str) -> str:
        row = self.services.get(service_id)
        return row.name if row else service_id

    def forecast_doc(self, p: PredictionRow | None, started: datetime) -> dict[str, Any] | None:
        if p is None:
            return None
        base = p.made_at
        return {
            "p10": round(p.p10, 1),
            "p50": round(p.p50, 1),
            "p90": round(p.p90, 1),
            "eta_p10": iso(base + timedelta(minutes=p.p10)),
            "eta_p50": iso(base + timedelta(minutes=p.p50)),
            "eta_p90": iso(base + timedelta(minutes=p.p90)),
            "made_at": iso(p.made_at),
            "elapsed_min": round(p.elapsed_min, 1),
            "model_version": p.model_version,
        }

    def incident_summary(
        self, r: IncidentRow, forecast: PredictionRow | None = None, blast: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        start = r.started_at if r.started_at and r.started_at < r.created_at else r.created_at
        end = r.resolved_at
        latest = r.updates[-1] if r.updates else None
        svc = self.services.get(r.service_id)
        return {
            "id": r.id,
            "path": incident_path(r.id),
            "service_id": r.service_id,
            "service_name": self._name(r.service_id),
            "category": svc.category if svc else "other",
            "title": r.title,
            "impact": r.impact,
            "status": r.status,
            "started_at": iso(start),
            "resolved_at": iso(end),
            "resolved_inferred": r.resolved_inferred,
            "duration_min": round((end - start).total_seconds() / 60, 1) if end else None,
            "elapsed_min": round(((end or self.now) - start).total_seconds() / 60, 1),
            "components": r.components[:12],
            "regions": r.regions[:12],
            "url": r.url,
            "updates_count": len(r.updates),
            "latest": {"at": iso(latest.at), "status": latest.status, "body": latest.body[:600]} if latest else None,
            "first_seen_at": iso(r.first_seen_at),
            "forecast": self.forecast_doc(forecast, start) if r.resolved_at is None else None,
            "blast": blast,
        }

    # Documents -------------------------------------------------------------------------------------

    def meta(self, tick: dict[str, Any] | None = None) -> dict[str, Any]:
        ok = sum(1 for r in self.services.values() if r.last_error is None and r.last_ok_at is not None)
        model = self.artifact("model.json")
        census = self.artifact("census-summary.json")
        return {
            "api_version": API_VERSION,
            "generated_at": iso(self.now),
            "edition": self.edition,
            "version": __version__,
            "services": len(self.services),
            "services_ok": ok,
            "tick": tick,
            "models": (model or {}).get("summary"),
            "census": {"generated_at": census.get("generated_at"), "domains": census.get("counts", {}).get("total")}
            if census
            else None,
        }

    def overview(self) -> dict[str, Any]:
        with self.db.session() as s:
            open_rows = list(
                s.scalars(
                    select(IncidentRow)
                    .options(selectinload(IncidentRow.updates))
                    .where(IncidentRow.resolved_at.is_(None))
                )
            )
            recent_rows = list(
                s.scalars(
                    select(IncidentRow)
                    .options(selectinload(IncidentRow.updates))
                    .where(IncidentRow.resolved_at >= self.now - timedelta(hours=48))
                    .order_by(IncidentRow.resolved_at.desc())
                    .limit(60)
                )
            )
            for r in [*open_rows, *recent_rows]:
                _ = r.updates
        open_rows.sort(key=lambda r: (-Impact(r.impact).rank, r.created_at), reverse=False)
        ids = [r.id for r in open_rows]
        forecasts = self.latest_forecasts(ids)
        blasts = self.latest_blast(ids, limit=8)
        counts: dict[str, int] = defaultdict(int)
        board = []
        open_by_service: dict[str, int] = defaultdict(int)
        for r in open_rows:
            open_by_service[r.service_id] += 1
        for svc in sorted(self.services.values(), key=lambda x: x.name.lower()):
            counts[svc.health] += 1
            board.append(
                {
                    "id": svc.id,
                    "name": svc.name,
                    "category": svc.category,
                    "health": svc.health,
                    "open": open_by_service.get(svc.id, 0),
                    "curated": svc.curated,
                }
            )
        counts["services"] = len(self.services)
        counts["open_incidents"] = len(open_rows)
        summaries = [self.incident_summary(r, forecasts.get(r.id), blasts.get(r.id)) for r in open_rows]
        active, long_running, notices = [], [], []
        for doc in summaries:
            if doc["impact"] in ("none", "maintenance"):
                notices.append(doc)
            elif doc["elapsed_min"] > LONG_RUNNING_MIN:
                long_running.append(doc)
            else:
                active.append(doc)
        return {
            "generated_at": iso(self.now),
            "counts": dict(counts),
            "intensity": self.intensity(active),
            "services": board,
            "active": active,
            "long_running": long_running,
            "notices": notices,
            "recent": [self.incident_summary(r) for r in recent_rows],
            "seismograph": self.seismograph(),
        }

    def intensity(self, active: list[dict[str, Any]]) -> dict[str, Any]:
        """A one number summary of how disrupted things are now, ranked against the past year.

        The score adds up open incidents weighted by impact, doubling incidents at providers many
        others depend on. Thresholds come from the same score computed hour by hour over the past
        year of history (intensity.json), so a level means "worse than N% of hours", not a guess.
        """
        graph = self.artifact("graph.json") or {}
        hubs = {n["id"] for n in graph.get("nodes", []) if n.get("dependents", 0) >= HUB_DEPENDENTS}
        cal = self.artifact("intensity.json") or {}
        if cal.get("services"):
            # Score the same services the thresholds were computed from, or the levels drift.
            covered = set(cal["services"])
            active = [d for d in active if d["service_id"] in covered]
        score = sum(_SCORE_WEIGHT.get(d["impact"], 0.0) * (2.0 if d["service_id"] in hubs else 1.0) for d in active)
        thresholds = cal.get("thresholds") or DEFAULT_THRESHOLDS
        percentiles = cal.get("percentiles") or DEFAULT_PERCENTILES
        level = 1 + sum(1 for t in thresholds if score >= t)
        return {
            "level": level,
            "numeral": NUMERALS[level - 1],
            "label": LABELS[level - 1],
            "score": round(score, 2),
            "percentile": percentiles[level - 2] if level >= 2 else None,
            "calibrated": bool(cal),
        }

    def seismograph(self, hours: int = 48, bucket_minutes: int = 10) -> dict[str, Any]:
        start = (self.now - timedelta(hours=hours)).replace(second=0, microsecond=0)
        start -= timedelta(minutes=start.minute % bucket_minutes)
        n = int(hours * 60 / bucket_minutes) + 1
        with self.db.session() as s:
            rows = list(
                s.scalars(
                    select(IncidentRow).where(
                        or_(IncidentRow.resolved_at.is_(None), IncidentRow.resolved_at >= start),
                        IncidentRow.created_at <= self.now,
                    )
                )
            )
        per: dict[str, list[float]] = defaultdict(lambda: [0.0] * n)
        total = [0.0] * n
        arrivals = [0.0] * n
        for r in rows:
            s0 = r.started_at if r.started_at and r.started_at < r.created_at else r.created_at
            e0 = r.resolved_at or self.now
            a = max(0, int((s0 - start).total_seconds() // (bucket_minutes * 60)))
            b = min(n - 1, int((e0 - start).total_seconds() // (bucket_minutes * 60)))
            w = _IMPACT_WEIGHT.get(r.impact, 0.5)
            if r.impact == "maintenance":
                continue  # planned work is not disruption
            if s0 >= start:
                arrivals[a] += w
            for i in range(a, b + 1):
                total[i] += w
                if r.service_id in SEISMOGRAPH:
                    per[r.service_id][i] = max(per[r.service_id][i], w)
        series = [
            {"id": sid, "name": self._name(sid), "values": per[sid] if sid in per else [0.0] * n}
            for sid in SEISMOGRAPH
            if sid in self.services
        ]
        return {
            "start": iso(start),
            "bucket_minutes": bucket_minutes,
            "points": n,
            "series": series,
            "total": [round(v, 2) for v in total],
            "arrivals": [round(v, 2) for v in arrivals],
        }

    def services_doc(self) -> dict[str, Any]:
        stats = self._reliability(days=90)
        rows = []
        for svc in sorted(self.services.values(), key=lambda x: x.name.lower()):
            rows.append(
                {
                    "id": svc.id,
                    "name": svc.name,
                    "category": svc.category,
                    "health": svc.health,
                    "indicator": svc.indicator,
                    "description": svc.description,
                    "curated": svc.curated,
                    "rank": svc.rank,
                    "status_url": svc.base_url,
                    "domains": svc.domains,
                    "last_polled_at": iso(svc.last_polled_at),
                    "last_error": svc.last_error,
                    "components": len(svc.components or []),
                    "stats_90d": stats.get(svc.id, {"incidents": 0, "minutes": 0.0, "median_min": None}),
                }
            )
        return {"generated_at": iso(self.now), "services": rows}

    def _reliability(self, days: int) -> dict[str, dict[str, Any]]:
        if days not in self._reliability_cache:
            self._reliability_cache[days] = self._compute_reliability(days)
        return self._reliability_cache[days]

    def _edges_by_service(self) -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
        if self._edges is None:
            out: dict[str, list[Any]] = defaultdict(list)
            into: dict[str, list[Any]] = defaultdict(list)
            for e in (self.artifact("graph.json") or {}).get("edges", []):
                out[e["source"]].append(e)
                into[e["target"]].append(e)
            self._edges = (out, into)
        return self._edges

    def _compute_reliability(self, days: int) -> dict[str, dict[str, Any]]:
        since = self.now - timedelta(days=days)
        with self.db.session() as s:
            rows = s.execute(
                select(
                    IncidentRow.service_id,
                    IncidentRow.created_at,
                    IncidentRow.started_at,
                    IncidentRow.resolved_at,
                    IncidentRow.impact,
                ).where(IncidentRow.created_at >= since)
            ).all()
        durations: dict[str, list[float]] = defaultdict(list)
        counts: dict[str, int] = defaultdict(int)
        for sid, created, started, resolved, _impact in rows:
            counts[sid] += 1
            if resolved:
                st = started if started and started < created else created
                durations[sid].append(max(0.0, (resolved - st).total_seconds() / 60))
        out = {}
        for sid, n in counts.items():
            d = sorted(durations[sid])
            out[sid] = {
                "incidents": n,
                "minutes": round(sum(d), 1),
                "median_min": round(d[len(d) // 2], 1) if d else None,
            }
        return out

    def service_doc(self, service_id: str) -> dict[str, Any] | None:
        svc = self.services.get(service_id)
        if svc is None:
            return None
        with self.db.session() as s:
            rows = list(
                s.scalars(
                    select(IncidentRow)
                    .options(selectinload(IncidentRow.updates))
                    .where(
                        IncidentRow.service_id == service_id,
                        or_(IncidentRow.resolved_at.is_(None), IncidentRow.created_at >= self.now - timedelta(days=90)),
                    )
                    .order_by(IncidentRow.created_at.desc())
                    .limit(200)
                )
            )
            for r in rows:
                _ = r.updates
        open_ids = [r.id for r in rows if r.resolved_at is None]
        forecasts = self.latest_forecasts(open_ids)
        blasts = self.latest_blast(open_ids, limit=12)
        out_edges, in_edges = self._edges_by_service()
        upstream = out_edges.get(service_id, [])
        downstream = in_edges.get(service_id, [])
        return {
            "generated_at": iso(self.now),
            "service": {
                "id": svc.id,
                "name": svc.name,
                "category": svc.category,
                "health": svc.health,
                "indicator": svc.indicator,
                "description": svc.description,
                "status_url": svc.base_url,
                "domains": svc.domains,
                "curated": svc.curated,
                "last_polled_at": iso(svc.last_polled_at),
                "last_error": svc.last_error,
            },
            "components": svc.components or [],
            "incidents": [self.incident_summary(r, forecasts.get(r.id), blasts.get(r.id)) for r in rows],
            "stats_90d": self._reliability(days=90).get(service_id),
            "depends_on": upstream[:50],
            "dependents": downstream[:200],
        }

    def incident_doc(self, incident_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            r = s.get(IncidentRow, incident_id)
            if r is None:
                return None
            _ = r.updates
            preds = list(
                s.scalars(
                    select(PredictionRow)
                    .where(PredictionRow.incident_id == incident_id)
                    .order_by(PredictionRow.made_at)
                )
            )
        start = r.started_at if r.started_at and r.started_at < r.created_at else r.created_at
        doc = self.incident_summary(
            r, preds[-1] if preds else None, self.latest_blast([incident_id], limit=60).get(incident_id)
        )
        doc["updates"] = [
            {"at": iso(u.at), "status": u.status, "body": u.body, "components": u.components} for u in r.updates
        ]
        doc["forecast_history"] = [
            {
                "made_at": iso(p.made_at),
                "elapsed_min": round(p.elapsed_min, 1),
                "phase": p.phase,
                "p10": round(p.p10, 1),
                "p50": round(p.p50, 1),
                "p90": round(p.p90, 1),
                "eta_p50": iso(p.made_at + timedelta(minutes=p.p50)),
            }
            for p in preds
        ]
        doc["started_at"] = iso(start)
        doc["similar"] = (self.db.kv_get(f"similar:{incident_id}") or {}).get("items", [])
        return doc
