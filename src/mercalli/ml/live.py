"""Run the trained models inside the engine's tick.

Each hook gets the open incidents after reconciliation and writes forecasts to the database. A
forecast is only written when something changed (a new update, a new phase) or the last one is
older than REFRESH, so the stored history of forecasts stays meaningful and small.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sqlalchemy import delete, func, select

from mercalli.domain import Health, Impact, Incident
from mercalli.engine import ModelHook
from mercalli.ml.blast import FEATURES as BLAST_FEATURES
from mercalli.ml.blast import WINDOW_AFTER, WINDOW_BEFORE, BlastModel, is_upstream
from mercalli.ml.embed import Embedder
from mercalli.ml.recovery import CATEGORIES, RecoveryModel, Step, matrix, snapshot_features
from mercalli.store import BlastRow, Database, EventRow, PredictionRow, ServiceRow

log = logging.getLogger("mercalli.ml")
REFRESH = timedelta(minutes=15)
LONG_RUNNING = timedelta(days=7)
BLAST_KEEP = 50
BLAST_MIN_PROB = 0.01
BLAST_ALERT_PROB = 0.3


def _health_rank(value: str | None) -> int:
    try:
        return Health(value or "unknown").rank
    except ValueError:
        return 0


def _incident_severity(inc: Incident, svc: ServiceRow | None) -> int:
    """Worst current state of the components this incident names, as training measured it from
    the components each update named. Falls back to the page's overall health."""
    if svc is None:
        return 0
    named = set(inc.components)
    ranks = [_health_rank(c.get("status")) for c in svc.components or [] if c.get("name") in named]
    return max(ranks) if ranks else _health_rank(svc.health)


def recovery_hook(model: RecoveryModel, embedder: Embedder) -> ModelHook:
    def recovery(db: Database, open_incidents: list[Incident], now: datetime) -> dict[str, int]:
        todo = [
            i
            for i in open_incidents
            if i.impact not in (Impact.NONE, Impact.MAINTENANCE) and now - i.start < LONG_RUNNING and i.updates
        ]
        if not todo:
            return {"forecasts": 0}
        with db.session() as s:
            services = {r.id: r for r in s.scalars(select(ServiceRow))}
            last = {
                r.incident_id: r
                for r in s.scalars(
                    select(PredictionRow)
                    .where(PredictionRow.incident_id.in_([i.id for i in todo]))
                    .order_by(PredictionRow.made_at)
                )
            }
        pending = []
        for inc in todo:
            svc = services.get(inc.service_id)
            category = svc.category if svc else "other"
            sev = _incident_severity(inc, svc)
            steps = [Step(u.at, u.status.value, len(u.components), sev, u.body) for u in inc.updates]
            start = min(inc.start, steps[0].at)
            k = len(steps) - 1
            prev = last.get(inc.id)
            if (
                prev
                and prev.phase == steps[k].status
                and now - prev.made_at < REFRESH
                and prev.elapsed_min >= (steps[k].at - start).total_seconds() / 60
            ):
                continue
            pending.append((inc, steps, start, k, category if category in CATEGORIES else "other"))
        if not pending:
            return {"forecasts": 0}
        keep = [p[0] for p in pending]
        tv = embedder.embed([inc.title for inc in keep])
        bv = embedder.embed([inc.updates[0].body for inc in keep])
        nn = (
            model.neighbors.features(
                tv + bv,
                np.array([int(p[2].timestamp() // 60) for p in pending]),
                [inc.service_id for inc in keep],
            )
            if model.neighbors is not None
            else None
        )
        feats = [
            snapshot_features(
                steps, k, start, inc.service_id, cat, model.priors, now=now, nn=nn[i] if nn is not None else None
            )
            for i, (inc, steps, start, k, cat) in enumerate(pending)
        ]
        lv = embedder.embed([inc.updates[-1].body for inc in keep])
        x = matrix(feats, model.projector.transform(tv, bv, lv))
        mins = model.predict_minutes(x)
        with db.session() as s:
            for inc, (lo, mid, hi) in zip(keep, mins, strict=True):
                elapsed = (now - min(inc.start, inc.updates[0].at)).total_seconds() / 60
                phase = inc.updates[-1].status.value
                s.add(
                    PredictionRow(
                        incident_id=inc.id,
                        made_at=now,
                        model_version=model.version,
                        elapsed_min=elapsed,
                        phase=phase,
                        p10=float(lo),
                        p50=float(mid),
                        p90=float(hi),
                    )
                )
                if inc.id not in last:
                    s.add(
                        EventRow(
                            at=now,
                            kind="forecast",
                            service_id=inc.service_id,
                            incident_id=inc.id,
                            payload={
                                "p10": float(lo),
                                "p50": float(mid),
                                "p90": float(hi),
                                "eta_p50": (now + timedelta(minutes=float(mid))).isoformat(),
                            },
                        )
                    )
        embedder.save()
        return {"forecasts": len(keep)}

    return recovery


def blast_hook(model: BlastModel, census_deps: dict[str, Any]) -> ModelHook:
    live = model.live
    vids: list[str] = live["services"]
    vcat = np.array(live["category_codes"], dtype=np.float32)
    rate = np.array(live["rate"], dtype=np.float32)
    before_n = np.array(live["before_n"], dtype=np.float32)
    generic_share = np.array(live["generic_share"], dtype=np.float32)
    window_days = (WINDOW_BEFORE + WINDOW_AFTER) / 1440
    p_win = 1 - np.exp(-rate * window_days)

    def dep_flags(u: str) -> np.ndarray:
        return np.array(
            [[float(u in census_deps.get(kind, {}).get(v, ())) for kind in ("dns", "edge", "hosting")] for v in vids],
            dtype=np.float32,
        )

    def blast(db: Database, open_incidents: list[Incident], now: datetime) -> dict[str, int]:
        with db.session() as s:
            cats = {r.id: r.category for r in s.scalars(select(ServiceRow))}
        made = 0
        for inc in open_incidents:
            u = inc.service_id
            if inc.impact.rank < 1 or not is_upstream(u, cats.get(u, "other")) or now - inc.start > timedelta(hours=24):
                continue
            with db.session() as s:
                last = s.scalar(select(func.max(BlastRow.made_at)).where(BlastRow.upstream_incident_id == inc.id))
            if last and now - last < REFRESH:
                continue
            zeros = [0] * len(vids)
            co = np.array(live["co"].get(u, zeros), dtype=np.float32)
            co_named = np.array(live.get("co_named", {}).get(u, zeros), dtype=np.float32)
            n_u = float(live["n_upstream"].get(u, 0))
            named = np.array([live["named"].get(v, {}).get(u, 0) for v in vids], dtype=np.float32)
            regions = {f"{u}:{r}" for r in inc.regions}
            region_match = np.array(
                [float(bool(regions & set(census_deps.get("regions", {}).get(v, ())))) for v in vids],
                dtype=np.float32,
            )
            ucat = float(CATEGORIES.index(cats.get(u, "other")) if cats.get(u) in CATEGORIES else 15)
            dep = dep_flags(u)
            n = len(vids)
            columns = {
                "log_rate_v": np.log(rate + 1e-3),
                "p_window_v": p_win,
                "prior_upstream_n": np.full(n, math.log1p(n_u)),
                "prior_co": np.log1p(co),
                "log_lift": np.log((co + 1) / (n_u * p_win + 1)),
                "prior_named_co": np.log1p(co_named),
                "named": np.log1p(named),
                "named_share": named / np.maximum(before_n, 1),
                "generic_share_v": generic_share,
                "dep_dns": dep[:, 0],
                "dep_edge": dep[:, 1],
                "dep_hosting": dep[:, 2],
                "region_match": region_match,
                "impact": np.full(n, float(inc.impact.rank)),
                "upstream_cat": np.full(n, ucat),
                "downstream_cat": vcat,
                "same_cat": (vcat == ucat).astype(np.float32),
                "hour_sin": np.full(n, math.sin(2 * math.pi * now.hour / 24)),
                "hour_cos": np.full(n, math.cos(2 * math.pi * now.hour / 24)),
            }
            x = np.column_stack([columns[name] for name in BLAST_FEATURES]).astype(np.float32)
            prob = model.predict(x)
            order = [i for i in np.argsort(-prob) if vids[i] != u and prob[i] >= BLAST_MIN_PROB][:BLAST_KEEP]
            with db.session() as s:
                # Only the newest list per incident is ever served; older ones would only grow the store.
                s.execute(delete(BlastRow).where(BlastRow.upstream_incident_id == inc.id))
                alerted = {
                    e.payload.get("service_id")
                    for e in s.scalars(select(EventRow).where(EventRow.kind == "blast", EventRow.incident_id == inc.id))
                }
                for i in order:
                    v = vids[i]
                    evidence = _evidence(
                        dep[i], int(co[i]), int(co_named[i]), int(n_u), int(named[i]), bool(region_match[i]), u
                    )
                    s.add(
                        BlastRow(
                            upstream_incident_id=inc.id,
                            made_at=now,
                            model_version=model.version,
                            service_id=v,
                            probability=float(prob[i]),
                            evidence=evidence,
                        )
                    )
                    if prob[i] >= BLAST_ALERT_PROB and v not in alerted:
                        s.add(
                            EventRow(
                                at=now,
                                kind="blast",
                                service_id=v,
                                incident_id=inc.id,
                                payload={
                                    "upstream_incident_id": inc.id,
                                    "service_id": v,
                                    "probability": float(prob[i]),
                                    "evidence": evidence,
                                },
                            )
                        )
            made += 1
        return {"blast_radius": made}

    return blast


def _evidence(dep: np.ndarray, co: int, co_named: int, n_u: int, named: int, region: bool, u: str) -> list[str]:
    out = []
    for flag, kind in zip(dep, ("dns", "edge", "hosting"), strict=True):
        if flag:
            out.append(f"{kind}:{u}")
    if region:
        out.append("same region")
    if co_named:
        out.append(f"blamed it during {co_named} of its {n_u} past incidents")
    if named:
        out.append(f"named it in {named} incident report{'s' if named != 1 else ''}")
    if co and not co_named:
        out.append(f"had incidents alongside {co} of its {n_u} past incidents")
    return out


def similar_hook(index: dict[str, Any], embedder: Embedder, k: int = 5) -> ModelHook:
    """Attach the most similar past incidents, and how long they took, to each open incident."""
    from mercalli.ml.artifacts import nearest

    def similar(db: Database, open_incidents: list[Incident], now: datetime) -> dict[str, int]:
        todo = [
            i
            for i in open_incidents
            if i.impact.rank >= 1 and now - i.start < LONG_RUNNING and db.kv_get(f"similar:{i.id}") is None
        ]
        if not todo:
            return {"similar": 0}
        tv = embedder.embed([i.title for i in todo])
        bv = embedder.embed([i.updates[0].body if i.updates else "" for i in todo])
        q = tv + bv
        q /= np.linalg.norm(q, axis=1, keepdims=True).clip(1e-6)
        for inc, vec in zip(todo, q, strict=True):
            db.kv_set(f"similar:{inc.id}", {"items": nearest(index, vec, k=k, exclude={inc.id})})
        return {"similar": len(todo)}

    return similar


def load_similar(path: Path) -> dict[str, Any]:
    data = np.load(path, allow_pickle=False)
    return {"ids": data["ids"].tolist(), "vectors": data["vectors"], "meta": json.loads(str(data["meta"]))}


def build_hooks(artifacts: Path, cache: Path | None = None) -> list[ModelHook]:
    """Load whatever models exist under `artifacts/model`. Missing pieces are skipped, not fatal.

    `cache` holds state that should outlive one run: computed embeddings and the downloaded
    embedding model. It defaults to `artifacts/cache`; the model files can be pointed elsewhere with
    MERCALLI_MODEL_CACHE so a CI cache can keep them between runs.
    """
    import os

    hooks: list[ModelHook] = []
    mdir = artifacts / "model"
    cache = cache or artifacts / "cache"
    model_cache = Path(os.environ.get("MERCALLI_MODEL_CACHE", cache / "fastembed"))
    shared: list[Embedder] = []

    def embedder() -> Embedder:
        if not shared:
            shared.append(Embedder(cache / "embeddings.npz", model_dir=model_cache))
        return shared[0]

    rec = mdir / "recovery.joblib"
    if rec.exists():
        try:
            model: RecoveryModel = joblib.load(rec)
            hooks.append(recovery_hook(model, embedder()))
        except Exception:
            log.exception("could not load the recovery model")
    sim = mdir / "similar.npz"
    if sim.exists():
        try:
            hooks.append(similar_hook(load_similar(sim), embedder()))
        except Exception:
            log.exception("could not load the similar incident index")
    bl = mdir / "blast.joblib"
    if bl.exists():
        try:
            bmodel: BlastModel = joblib.load(bl)
            deps_path = artifacts / "census-deps.json"
            deps = json.loads(deps_path.read_text()) if deps_path.exists() else {}
            hooks.append(blast_hook(bmodel, deps))
        except Exception:
            log.exception("could not load the blast radius model")
    return hooks
