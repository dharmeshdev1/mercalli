"""Train and evaluate the blast radius model on the same time split as the recovery model."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import polars as pl
from sklearn.isotonic import IsotonicRegression

from mercalli.ml.blast import (
    CATEGORIES,
    FEATURES,
    MODEL_FEATURES,
    MODEL_IDX,
    RATE_LOOKBACK_DAYS,
    BlastModel,
    Dependencies,
    History,
    brier,
    build_examples,
    fit_blast,
    is_upstream,
    ranking_metrics,
    to_minutes,
)
from mercalli.ml.train import Split


def load_dependencies(path: Path | None) -> Dependencies:
    if not path or not path.exists():
        return Dependencies()
    raw = json.loads(path.read_text())
    return Dependencies(
        dns={k: set(v) for k, v in raw.get("dns", {}).items()},
        edge={k: set(v) for k, v in raw.get("edge", {}).items()},
        hosting={k: set(v) for k, v in raw.get("hosting", {}).items()},
        regions={k: set(v) for k, v in raw.get("regions", {}).items()},
    )


def upstream_incidents(inc: pl.DataFrame) -> pl.DataFrame:
    mask = [is_upstream(s, c) for s, c in zip(inc["service_id"], inc["category"], strict=True)]
    return inc.filter(pl.Series(mask) & (pl.col("impact_rank") >= 1) & pl.col("start").is_not_null()).select(
        "service_id", "start", "impact_rank", "regions", "id"
    )


def train_blast(
    data: Path, out: Path, *, split: Split, deps_path: Path | None, seed: int = 0, evaluate: bool = True
) -> dict[str, Any]:
    inc = pl.read_parquet(data / "incidents.parquet")
    # Only services whose full history we hold can serve as negatives: a service we only started
    # watching last week did not "not fail" in 2023, we just did not see it.
    hist_ids = set(inc.filter(pl.col("source").is_in(["index", "detail"]))["service_id"].unique().to_list())
    inc_h = inc.filter(pl.col("service_id").is_in(list(hist_ids)) | pl.col("service_id").is_in(["aws", "gcp"]))
    hist = History.from_frame(inc_h)
    deps = load_dependencies(deps_path)
    up = upstream_incidents(inc_h)

    t_cal, t_test = to_minutes(split.calib_start), to_minutes(split.test_start)
    t_end = to_minutes(split.test_end) if split.test_end else None
    x_tr, y_tr, _ = build_examples(hist, up, deps, until=t_cal)
    x_ca, y_ca, _ = build_examples(hist, up, deps, since=t_cal, until=t_test)
    x_te, y_te, m_te = build_examples(hist, up, deps, since=t_test, until=t_end)

    model = fit_blast(x_tr, y_tr, seed=seed)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(model.predict_proba(x_ca[:, MODEL_IDX])[:, 1], y_ca)
    version = datetime.now(UTC).strftime("blast-%Y%m%d-%H%M")
    bm = BlastModel(version=version, model=model, calibrator=iso, trained_at=datetime.now(UTC).isoformat())
    if not evaluate:
        bm.live = _live_tables(hist, up, deps)
        out.mkdir(parents=True, exist_ok=True)
        joblib.dump(bm, out / "blast.joblib", compress=3)
        return {
            "version": version,
            "trained_at": bm.trained_at,
            "counts": {
                "train": {"pairs": len(y_tr), "positives": int(y_tr.sum())},
                "calib": {"pairs": len(y_ca), "positives": int(y_ca.sum())},
            },
        }
    p_te = bm.predict(x_te)

    f = {name: i for i, name in enumerate(FEATURES)}
    baselines = {
        "base_rate_only": x_te[:, f["log_rate_v"]],
        "co_failure_lift": x_te[:, f["log_lift"]] + 1e-3 * x_te[:, f["log_rate_v"]],
        "named_in_reports": x_te[:, f["named"]] + 1e-3 * x_te[:, f["log_rate_v"]],
        "census_dependency": x_te[:, [f["dep_dns"], f["dep_edge"], f["dep_hosting"]]].sum(1)
        + 1e-3 * x_te[:, f["log_rate_v"]],
        "named_together_before": x_te[:, f["prior_named_co"]] + 1e-3 * x_te[:, f["log_rate_v"]],
    }
    major = x_te[:, f["impact"]] >= 2
    report: dict[str, Any] = {
        "version": version,
        "trained_at": bm.trained_at,
        "window_minutes": {"before": 45, "after": 180},
        "split": {"calib_start": str(split.calib_start), "test_start": str(split.test_start)},
        "counts": {
            "train": {"pairs": len(y_tr), "positives": int(y_tr.sum())},
            "calib": {"pairs": len(y_ca), "positives": int(y_ca.sum())},
            "test": {"pairs": len(y_te), "positives": int(y_te.sum())},
            "upstream_services": int(up["service_id"].n_unique()),
            "downstream_services": len(hist.services),
        },
        "label": "the other service posts an incident naming the upstream provider within the window",
        "features": MODEL_FEATURES,
        "test": {**ranking_metrics(p_te, y_te, m_te, boot=2000), "brier": brier(p_te, y_te)},
        "test_major": ranking_metrics(
            p_te[major], y_te[major], [m for m, k in zip(m_te, major, strict=True) if k], boot=2000
        ),
        "baselines": {name: ranking_metrics(s, y_te, m_te, boot=2000) for name, s in baselines.items()},
        "reliability": _reliability(p_te, y_te),
    }
    bm.live = _live_tables(hist, up, deps)
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump(bm, out / "blast.joblib", compress=3)
    (out / "blast-report.json").write_text(json.dumps(report, indent=1))
    return report


def _reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    """Predicted against observed rates in equal count bins of the prediction."""
    order = np.argsort(p)
    out = []
    for chunk in np.array_split(order, bins):
        if len(chunk):
            out.append({"predicted": float(p[chunk].mean()), "observed": float(y[chunk].mean()), "n": len(chunk)})
    return out


def _live_tables(hist: History, up: pl.DataFrame, deps: Dependencies) -> dict[str, Any]:
    """Counts over all history, for live inference without the dataset."""
    services = hist.services
    now = max(int(s[-1]) for s in hist.starts.values())
    vidx = {s: i for i, s in enumerate(services)}
    n_up: dict[str, int] = {}
    tables: dict[str, dict[str, np.ndarray]] = {}
    for label in ("any", "named"):
        _, y, meta = build_examples(hist, up, deps, label=label)
        counts: dict[str, np.ndarray] = {}
        seen: set[tuple[str, int]] = set()
        for (u, v, t), positive in zip(meta, y, strict=True):
            if u not in counts:
                counts[u] = np.zeros(len(services), dtype=np.int32)
            if label == "any" and (u, t) not in seen:
                seen.add((u, t))
                n_up[u] = n_up.get(u, 0) + 1
            if positive:
                counts[u][vidx[v]] += 1
        tables[label] = counts
    co, co_named = tables["any"], tables["named"]
    rate, before_n, generic_share, named = [], [], [], {}
    for v in services:
        sv = hist.starts[v]
        start = max(hist.coverage[v], now - RATE_LOOKBACK_DAYS * 1440)
        recent = int((sv >= now - RATE_LOOKBACK_DAYS * 1440).sum())
        rate.append(recent / max(1.0, (now - start) / 1440))
        before_n.append(len(sv))
        generic_share.append(float(hist.generic[v].mean()) if len(sv) else 0.0)
        counts: dict[str, int] = {}
        for m in hist.mentions[v]:
            for u in m:
                counts[u] = counts.get(u, 0) + 1
        named[v] = counts
    return {
        "services": services,
        "category_codes": [
            CATEGORIES.index(hist.category[s]) if hist.category[s] in CATEGORIES else 15 for s in services
        ],
        "rate": rate,
        "before_n": before_n,
        "generic_share": generic_share,
        "named": named,
        "co": {u: c.tolist() for u, c in co.items()},
        "co_named": {u: c.tolist() for u, c in co_named.items()},
        "n_upstream": n_up,
        "as_of_minute": now,
    }
