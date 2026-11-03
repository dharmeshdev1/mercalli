"""Offline artifacts the live site reads: the dependency graph, intensity thresholds, cascade replays,
similar incidents and the model card. All are rebuilt from the dataset and the census each week.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from mercalli.catalog import Service
from mercalli.ml.blast import WINDOW_AFTER, WINDOW_BEFORE

_SCORE_WEIGHT = {"critical": 4.0, "major": 2.0, "minor": 1.0}
PERCENTILES = [50, 75, 90, 95, 98, 99, 99.5, 99.8, 99.95]
# Census answers that are not a provider: run by the site itself, not in the taxonomy, or absent.
PLACEHOLDERS = {"self", "other", "none", "unknown", None}


def census_dependencies(domains_parquet: Path, services: list[Service]) -> dict[str, Any]:
    """Which providers serve each catalog service's DNS, edge and hosting, from the census."""
    if not domains_parquet.exists():
        return {}
    df = pl.read_parquet(domains_parquet)
    by_domain = {r["domain"]: r for r in df.iter_rows(named=True)}
    out: dict[str, dict[str, set[str]]] = {
        "dns": defaultdict(set),
        "edge": defaultdict(set),
        "hosting": defaultdict(set),
        "regions": defaultdict(set),
    }
    for svc in services:
        for d in svc.domains:
            r = by_domain.get(d) or by_domain.get(d.removeprefix("www."))
            if not r:
                continue
            for kind, col in (("dns", "dns_provider"), ("edge", "edge_provider"), ("hosting", "hosting_provider")):
                p = r.get(col)
                if p and p != svc.id and p not in PLACEHOLDERS:
                    out[kind][svc.id].add(p)
            if r.get("hosting_provider") not in PLACEHOLDERS and r.get("hosting_region"):
                out["regions"][svc.id].add(f"{r['hosting_provider']}:{r['hosting_region']}")
    return {k: {s: sorted(v) for s, v in m.items()} for k, m in out.items()}


def dependency_graph(
    services: list[Service],
    deps: dict[str, Any],
    blast_live: dict[str, Any] | None,
    incidents: pl.DataFrame,
    now: datetime,
) -> dict[str, Any]:
    """Nodes are services and providers; an edge points from a dependent to what it depends on."""
    names = {s.id: s.name for s in services}
    cats = {s.id: s.category.value for s in services}
    edges: dict[tuple[str, str], dict[str, Any]] = {}

    def edge(src: str, dst: str, kind: str, evidence: str, weight: float) -> None:
        if src == dst:
            return
        e = edges.setdefault((src, dst), {"source": src, "target": dst, "kinds": [], "weight": 0.0, "evidence": []})
        if kind not in e["kinds"]:
            e["kinds"].append(kind)
        e["evidence"].append(evidence)
        e["weight"] = round(min(1.0, e["weight"] + weight), 3)

    for kind in ("dns", "edge", "hosting"):
        for svc, providers in deps.get(kind, {}).items():
            for p in providers:
                edge(svc, p, kind, f"{kind} served by {names.get(p, p)}", 0.5 if kind != "dns" else 0.35)

    # Named in incident reports: a dependent that has blamed a provider at least twice.
    named: Counter[tuple[str, str]] = Counter()
    totals: Counter[str] = Counter()
    for sid, mentions in incidents.select("service_id", "mentions").iter_rows():
        totals[sid] += 1
        for p in mentions or []:
            named[(sid, p)] += 1
    for (sid, p), n in named.items():
        if n >= 2 and p in names:
            edge(sid, p, "named", f"named {names[p]} in {n} incident reports", min(0.6, 0.15 * n))

    # Failed together far more often than chance, from the blast model's history tables.
    if blast_live:
        vids = blast_live["services"]
        rate = np.array(blast_live["rate"])
        p_win = 1 - np.exp(-rate * (WINDOW_BEFORE + WINDOW_AFTER) / 1440)
        for u, co in blast_live["co"].items():
            n_u = blast_live["n_upstream"].get(u, 0)
            for j, c in enumerate(co):
                expected = n_u * p_win[j]
                if c >= 3 and (c + 1) / (expected + 1) >= 3:
                    edge(
                        vids[j],
                        u,
                        "cofailure",
                        f"failed within hours of {names.get(u, u)} {c} of {n_u} times",
                        min(0.5, 0.05 * c),
                    )

    dependents: Counter[str] = Counter(dst for _, dst in edges)
    node_ids = {s.id for s in services} | set(dependents)
    nodes = [
        {
            "id": n,
            "name": names.get(n, n.replace("-", " ").title()),
            "category": cats.get(n, "provider"),
            "kind": "service" if n in names else "provider",
            "dependents": dependents.get(n, 0),
        }
        for n in sorted(node_ids)
    ]
    return {
        "generated_at": now.isoformat(),
        "nodes": nodes,
        "edges": sorted(edges.values(), key=lambda e: (e["target"], e["source"])),
    }


def intensity_calibration(
    incidents: pl.DataFrame, graph: dict[str, Any], now: datetime, *, days: int = 365
) -> dict[str, Any]:
    """The live intensity score, replayed hour by hour over the past year, and its percentiles."""
    hubs = {n["id"] for n in graph.get("nodes", []) if n.get("dependents", 0) >= 20}
    # Only services whose whole history we hold can be compared across the year. A service that only
    # publishes a recent feed would count today but be missing from every past hour.
    covered = sorted(set(incidents.filter(pl.col("source").is_in(["index", "detail"]))["service_id"].to_list()))
    incidents = incidents.filter(pl.col("service_id").is_in(covered))
    start = now - timedelta(days=days)
    d = incidents.filter(
        (pl.col("start") >= start - timedelta(days=7))
        & pl.col("resolved_at").is_not_null()
        & (pl.col("impact").is_in(list(_SCORE_WEIGHT)))
        & ~pl.col("retroactive")
    )
    hours = int(days * 24)
    score = np.zeros(hours)
    t0 = start.timestamp()
    for sid, impact, s, ended in d.select("service_id", "impact", "start", "resolved_at").iter_rows():
        # The live score stops counting an incident once it has been open a week, so history does too.
        e = min(ended, s + timedelta(days=7))
        a = max(0, int((s.timestamp() - t0) // 3600))
        b = min(hours - 1, int((e.timestamp() - t0) // 3600))
        if b >= a:
            score[a : b + 1] += _SCORE_WEIGHT[impact] * (2.0 if sid in hubs else 1.0)
    thresholds = [float(np.percentile(score, p)) for p in PERCENTILES]
    # Levels must be strictly increasing even when the score is flat for long stretches.
    for i in range(1, len(thresholds)):
        thresholds[i] = max(thresholds[i], thresholds[i - 1] + 0.5)
    return {
        "generated_at": now.isoformat(),
        "hours": hours,
        "percentiles": PERCENTILES,
        "thresholds": [round(t, 2) for t in thresholds],
        "weights": _SCORE_WEIGHT,
        "hub_dependents": 20,
        "services": covered,
        "median_score": float(np.median(score)),
        "max_score": float(score.max()),
    }


def replays(
    incidents: pl.DataFrame,
    services: list[Service],
    *,
    limit: int = 6,
    window: int = WINDOW_AFTER,
    cluster_hours: float = 6.0,
    min_services: int = 6,
    min_events: int = 10,
) -> dict[str, Any]:
    """The biggest cascades in history, found from what downstream services said.

    For each provider, incidents at other services that name it are grouped into bursts. A burst is
    a replay: its epicenter is the provider, its clock starts at the provider's own incident when
    Mercalli has one on record, or else at the first report naming it, and it shows every incident
    that began in the following window, marking the ones that named the provider. Overlapping bursts
    keep only the larger one, so one bad day appears once, under the provider it was blamed on.
    """
    from mercalli.ml.dataset import PROVIDER_ALIASES

    cats = {s.id: s.category.value for s in services}
    names = {s.id: s.name for s in services}
    d = incidents.filter(pl.col("start").is_not_null() & (pl.col("impact") != "maintenance")).sort("start")
    rows = d.select(
        "id", "service_id", "title", "impact", "start", "resolved_at", "duration_min", "mentions"
    ).to_dicts()
    t = np.array([r["start"].timestamp() for r in rows])
    rank = {"critical": 3, "major": 2, "minor": 1, "none": 0}

    bursts = []
    for p in PROVIDER_ALIASES:
        named = [r for r in rows if p in (r["mentions"] or []) and r["service_id"] != p]
        k = 0
        while k < len(named):
            first = named[k]["start"]
            group = [x for x in named[k:] if x["start"] - first <= timedelta(hours=cluster_hours)]
            k += len(group)
            distinct = {x["service_id"] for x in group}
            if len(distinct) >= min_services:
                bursts.append((len(distinct), p, first, group))
    bursts.sort(key=lambda b: -b[0])

    out: list[dict[str, Any]] = []
    taken: list[tuple[datetime, datetime]] = []
    for _, p, first, _group in bursts:
        if len(out) >= limit:
            break
        span = (first - timedelta(hours=cluster_hours), first + timedelta(hours=cluster_hours))
        if any(a < span[1] and span[0] < b for a, b in taken):
            continue
        taken.append(span)
        own = [
            r
            for r in rows
            if r["service_id"] == p and first - timedelta(hours=3) <= r["start"] <= first + timedelta(hours=1)
        ]
        own.sort(key=lambda r: (-rank.get(r["impact"], 0), r["start"]))
        anchor = own[0] if own else None
        t0 = min(anchor["start"], first) if anchor else first
        a = np.searchsorted(t, t0.timestamp() - WINDOW_BEFORE * 60, side="left")
        b = np.searchsorted(t, t0.timestamp() + window * 60, side="right")
        seen: set[str] = set()
        events = []
        for x in rows[a:b]:
            if x["service_id"] in (p, *seen):
                continue
            seen.add(x["service_id"])
            events.append(
                {
                    "service_id": x["service_id"],
                    "service_name": names.get(x["service_id"], x["service_id"]),
                    "category": cats.get(x["service_id"], "other"),
                    "incident_id": x["id"],
                    "title": x["title"],
                    "impact": x["impact"],
                    "offset_min": round((x["start"] - t0).total_seconds() / 60, 1),
                    "duration_min": x["duration_min"],
                    "names_upstream": p in (x["mentions"] or []),
                }
            )
        if len(events) < min_events:
            continue  # a handful of incidents is a coincidence, not a cascade worth replaying
        named_count = sum(e["names_upstream"] for e in events)
        if anchor:
            upstream = {
                "service_id": p,
                "service_name": names.get(p, p),
                "incident_id": anchor["id"],
                "title": anchor["title"],
                "impact": anchor["impact"],
                "started_at": anchor["start"].isoformat(),
                "resolved_at": anchor["resolved_at"].isoformat() if anchor["resolved_at"] else None,
                "duration_min": anchor["duration_min"],
                "on_record": True,
            }
        else:
            worst = max(
                (e["impact"] for e in events if e["names_upstream"]), key=lambda i: rank.get(i, 0), default="major"
            )
            upstream = {
                "service_id": p,
                "service_name": names.get(p, p),
                "incident_id": None,
                "title": f"{named_count} services named {names.get(p, p)} in their own incident reports",
                "impact": worst,
                "started_at": t0.isoformat(),
                "resolved_at": None,
                "duration_min": None,
                "on_record": False,
            }
        out.append(
            {
                "slug": f"{p}-{t0:%Y-%m-%d}",
                "title": f"{names.get(p, p)}, {t0:%d %B %Y}",
                "upstream": upstream,
                "window_min": window,
                "events": events,
                "totals": {
                    "services": len(events),
                    "named": named_count,
                    "categories": dict(Counter(e["category"] for e in events)),
                },
            }
        )
    return {"generated_at": datetime.now(UTC).isoformat(), "replays": out}


def similar_index(incidents: pl.DataFrame, vectors: np.ndarray, ids: list[str]) -> dict[str, Any]:
    """Unit vectors (float16) for nearest neighbour lookups of past incidents, with their outcomes."""
    meta = {
        r["id"]: r
        for r in incidents.select("id", "service_id", "title", "duration_min", "start", "impact").iter_rows(named=True)
    }
    keep = [i for i, x in enumerate(ids) if x in meta and meta[x]["duration_min"] is not None]
    return {
        "ids": [ids[i] for i in keep],
        "vectors": vectors[keep].astype(np.float16),
        "meta": [
            {
                "service_id": meta[ids[i]]["service_id"],
                "title": meta[ids[i]]["title"],
                "duration_min": round(meta[ids[i]]["duration_min"], 1),
                "impact": meta[ids[i]]["impact"],
                "started_at": meta[ids[i]]["start"].isoformat(),
            }
            for i in keep
        ],
    }


def nearest(
    index: dict[str, Any], query: np.ndarray, k: int = 5, exclude: set[str] | None = None
) -> list[dict[str, Any]]:
    sims = index["vectors"].astype(np.float32) @ query.astype(np.float32)
    out = []
    for i in np.argsort(-sims):
        if exclude and index["ids"][i] in exclude:
            continue
        out.append({"id": index["ids"][i], "similarity": round(float(sims[i]), 3), **index["meta"][i]})
        if len(out) >= k:
            break
    return out


def model_card(
    recovery: dict[str, Any] | None,
    blast: dict[str, Any] | None,
    intensity: dict[str, Any] | None,
    dataset: dict[str, Any] | None,
    live: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            # The deployed models are the production refits; the reports describe the evaluated ones.
            "recovery_version": ((recovery or {}).get("production") or {}).get("version")
            or (recovery or {}).get("version"),
            "blast_version": ((blast or {}).get("production") or {}).get("version") or (blast or {}).get("version"),
            "recovery_evaluated_version": (recovery or {}).get("version"),
            "blast_evaluated_version": (blast or {}).get("version"),
            "dataset": dataset,
        },
        "recovery": recovery,
        "blast": blast,
        "intensity": intensity,
        "live": live,
    }


def write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, default=_default))


def _default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, float) and math.isnan(o):
        return None
    raise TypeError(type(o).__name__)
