"""Train and evaluate the recovery forecaster on a time split.

Train on everything that began before the calibration period, calibrate the intervals on the next
period, and report every metric on the final period only. Nothing from the test period is used to
fit, tune or calibrate anything.
"""

from __future__ import annotations

import json
import math
import zlib
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import polars as pl

from mercalli.ml.embed import Embedder
from mercalli.ml.recovery import (
    CATEGORICAL,
    NUMERIC,
    PHASES,
    Neighbors,
    Priors,
    RecoveryModel,
    TextProjector,
    conformal_widening,
    feature_names,
    fit_quantiles,
    make_snapshots,
    matrix,
    phase_of,
    snapshot_features,
)

FOLDS = 5


@dataclass(frozen=True)
class Split:
    calib_start: datetime
    test_start: datetime
    test_end: datetime | None = None

    def label(self, t: datetime) -> str:
        if t < self.calib_start:
            return "train"
        if t < self.test_start:
            return "calib"
        if self.test_end is None or t < self.test_end:
            return "test"
        return "future"


def _metrics(pred_log: np.ndarray, y_log: np.ndarray) -> dict[str, float]:
    lo, mid, hi = pred_log[:, 0], pred_log[:, 1], pred_log[:, 2]
    err = np.abs(mid - y_log)
    ratio = np.expm1(mid).clip(1) / np.expm1(y_log).clip(1)
    return {
        "n": len(y_log),
        "mae_log": float(err.mean()),
        "median_abs_error_min": float(np.median(np.abs(np.expm1(mid) - np.expm1(y_log)))),
        "within_2x": float(((ratio >= 0.5) & (ratio <= 2)).mean()),
        "coverage_80": float(((y_log >= lo) & (y_log <= hi)).mean()),
        "median_width_ratio": float(np.median(np.expm1(hi).clip(1) / np.expm1(lo).clip(1))),
    }


def _baselines(train: list[dict[str, Any]], test: list[dict[str, Any]], priors: Priors) -> dict[str, np.ndarray]:
    """Predictions from three simple rules, each given the same information as the model."""
    by_phase: dict[str, list[float]] = defaultdict(list)
    bins: dict[tuple[str, int], list[float]] = defaultdict(list)
    for r in train:
        st = r["steps"][r["k"]]
        y = math.log1p(r["remaining"])
        by_phase[phase_of(st.status)].append(y)
        elapsed = max(0.0, (st.at - r["start"]).total_seconds() / 60)
        bins[(phase_of(st.status), int(math.log2(1 + elapsed)))].append(y)
    phase_med = {p: float(np.median(v)) for p, v in by_phase.items()}
    bin_med = {k: float(np.median(v)) for k, v in bins.items() if len(v) >= 30}
    glob = float(np.median([math.log1p(r["remaining"]) for r in train]))
    out: dict[str, list[float]] = defaultdict(list)
    for r in test:
        st = r["steps"][r["k"]]
        ph = phase_of(st.status)
        elapsed = max(0.0, (st.at - r["start"]).total_seconds() / 60)
        out["phase_median"].append(phase_med.get(ph, glob))
        out["elapsed_and_phase"].append(bin_med.get((ph, int(math.log2(1 + elapsed))), phase_med.get(ph, glob)))
        prior_m, _, _ = priors.lookup(r["service_id"], r["category"])
        out["service_history"].append(math.log1p(max(5.0, math.expm1(prior_m) - elapsed)))
    return {k: np.array(v) for k, v in out.items()}


def train_recovery(
    data: Path, out: Path, *, split: Split, embed_cache: Path, seed: int = 0, evaluate: bool = True
) -> dict[str, Any]:
    """Fit on the train period, calibrate on the next, and (when `evaluate`) report on the last.

    Production models are fitted with `evaluate=False` and a split that ends today, so they learn
    from the most recent data; the procedure itself is the one validated by the evaluation run.
    """
    inc = pl.read_parquet(data / "incidents.parquet")
    upd = pl.read_parquet(data / "updates.parquet")
    if "first_body" not in inc.columns:
        raise SystemExit("training needs the private dataset with first update text (first_body)")
    labels = {r["id"]: split.label(r["start"]) for r in inc.select("id", "start").iter_rows(named=True)}
    train_inc = inc.filter(pl.col("id").is_in([k for k, v in labels.items() if v == "train"]))
    priors = Priors.fit(train_inc)
    # A training row must not see its own incident inside the service's history prior, or the model
    # learns to trust priors more than it should. Training rows get out-of-fold priors; calibration,
    # test and live forecasts use priors from the whole training period, as they would in production.
    fold_of = {iid: zlib.crc32(iid.encode()) % FOLDS for iid in train_inc["id"].to_list()}
    oof = {
        k: Priors.fit(train_inc.filter(pl.col("id").is_in([i for i, f in fold_of.items() if f != k])))
        for k in range(FOLDS)
    }

    def priors_for(incident_id: str) -> Priors:
        fold = fold_of.get(incident_id)
        return priors if fold is None else oof[fold]

    snaps: list[dict[str, Any]] = make_snapshots(inc, upd)
    parts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in snaps:
        parts[labels[r["incident_id"]]].append(r)

    texts = {
        r["id"]: (r["title"] or "", r["first_body"] or "")
        for r in inc.select("id", "title", "first_body").iter_rows(named=True)
    }
    embedder = Embedder(embed_cache)
    ids_needed = list(dict.fromkeys(r["incident_id"] for r in snaps))
    t_vec = embedder.embed([texts[i][0] for i in ids_needed])
    b_vec = embedder.embed([texts[i][1] for i in ids_needed])
    # The latest update at each snapshot: what the vendor is saying now, not just at the start.
    for n, r in enumerate(snaps):
        r["row"] = n
    l_vec = embedder.embed([r["steps"][r["k"]].body for r in snaps])
    embedder.save()
    index = {iid: n for n, iid in enumerate(ids_needed)}
    train_ids = [i for i in ids_needed if labels[i] == "train"]
    train_rows = [r["row"] for r in parts["train"]]
    proj = TextProjector.fit(
        t_vec[[index[i] for i in train_ids]], b_vec[[index[i] for i in train_ids]], l_vec[train_rows], seed=seed
    )

    # Outcomes of similar past incidents. The index holds training incidents only, and each query
    # only sees incidents that had ended before its own began, so no row learns from its future.
    combined = t_vec + b_vec
    first_rows = {r["incident_id"]: r for r in snaps if r["k"] == 0}
    info = {r["id"]: r for r in inc.select("id", "service_id", "start", "resolved_at").iter_rows(named=True)}

    def minutes(dt: Any) -> int:
        return int(dt.timestamp() // 60)

    def start_of(iid: str) -> int:
        row = first_rows.get(iid)
        return minutes(row["start"]) if row else minutes(info[iid]["start"])

    def outcome(iid: str) -> float:
        return math.log1p(max(0.0, (info[iid]["resolved_at"] - info[iid]["start"]).total_seconds() / 60))

    def fit_neighbors(ids: list[str]) -> Neighbors:
        pos = [index[i] for i in ids]
        return Neighbors.fit(
            combined[pos],
            np.array([outcome(i) for i in ids]),
            np.array([minutes(info[i]["resolved_at"]) for i in ids]),
            [info[i]["service_id"] for i in ids],
            seed=seed,
        )

    neighbors = fit_neighbors(train_ids)
    nn_values = neighbors.features(
        combined, np.array([start_of(i) for i in ids_needed]), [info[i]["service_id"] for i in ids_needed]
    )

    def build(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        feats = [
            snapshot_features(
                r["steps"],
                r["k"],
                r["start"],
                r["service_id"],
                r["category"],
                priors_for(r["incident_id"]),
                nn=nn_values[index[r["incident_id"]]],
            )
            for r in rows
        ]
        pos = [index[r["incident_id"]] for r in rows]
        text = proj.transform(t_vec[pos], b_vec[pos], l_vec[[r["row"] for r in rows]]) if rows else np.zeros((0, 0))
        x = matrix(feats, text)
        y = np.log1p(np.array([r["remaining"] for r in rows], dtype=np.float64))
        per_incident: dict[str, int] = defaultdict(int)
        for r in rows:
            per_incident[r["incident_id"]] += 1
        w = np.array([1.0 / per_incident[r["incident_id"]] for r in rows])
        return x, y, w

    x_tr, y_tr, w_tr = build(parts["train"])
    x_ca, y_ca, _ = build(parts["calib"])
    x_te, y_te, _ = build(parts["test"])
    cat_idx = [len(NUMERIC) + i for i in range(len(CATEGORICAL))]
    models = fit_quantiles(x_tr, y_tr, w_tr, seed=seed, categorical=cat_idx)
    widen = conformal_widening(models, x_ca, y_ca)
    version = datetime.now(UTC).strftime("recovery-%Y%m%d-%H%M")
    # Live forecasts search every incident in the training data, including the calibration period,
    # so the index carries the most recent outcomes.
    live_neighbors = fit_neighbors([i for i in ids_needed if labels[i] in ("train", "calib")])
    model = RecoveryModel(
        version=version,
        priors=priors,
        projector=proj,
        models=models,
        conformal=widen,
        names=feature_names(proj),
        neighbors=live_neighbors,
    )

    if not evaluate:
        out.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, out / "recovery.joblib", compress=3)
        return {
            "version": version,
            "trained_at": datetime.now(UTC).isoformat(),
            "split": {k: str(v) for k, v in asdict(split).items()},
            "conformal_widening_log": widen,
            "counts": {
                "incidents": {k: len({r["incident_id"] for r in v}) for k, v in parts.items()},
                "snapshots": {k: len(v) for k, v in parts.items()},
            },
        }

    pred = model.predict_log(x_te)
    first = np.array([r["k"] == 0 for r in parts["test"]])
    base = _baselines(parts["train"], parts["test"], priors)
    report: dict[str, Any] = {
        "version": version,
        "trained_at": datetime.now(UTC).isoformat(),
        "split": {k: str(v) for k, v in asdict(split).items()},
        "counts": {
            "incidents": {k: len({r["incident_id"] for r in v}) for k, v in parts.items()},
            "snapshots": {k: len(v) for k, v in parts.items()},
        },
        "conformal_widening_log": widen,
        "test": {"all_updates": _metrics(pred, y_te), "first_update": _metrics(pred[first], y_te[first])},
        "baselines": {},
        "uncalibrated_coverage_80": float(
            ((y_te >= models[0.1].predict(x_te)) & (y_te <= models[0.9].predict(x_te))).mean()
        ),
    }
    for name, b in base.items():
        bl = np.stack([b, b, b], axis=1)
        m_all, m_first = _metrics(bl, y_te), _metrics(bl[first], y_te[first])
        for m in (m_all, m_first):
            m.pop("coverage_80"), m.pop("median_width_ratio")
        report["baselines"][name] = {"all_updates": m_all, "first_update": m_first}

    # By phase and by category, so weak spots are visible rather than averaged away.
    for key, getter in (
        ("by_phase", lambda r: phase_of(r["steps"][r["k"]].status)),
        ("by_category", lambda r: r["category"]),
    ):
        groups: dict[str, list[int]] = defaultdict(list)
        for n, r in enumerate(parts["test"]):
            groups[getter(r)].append(n)
        report[key] = {g: _metrics(pred[idx], y_te[idx]) for g, idx in groups.items() if len(idx) >= 50}

    # Calibration curve of the median: share of truths below the predicted quantile, per quantile.
    report["reliability"] = {str(q): float((y_te <= (model.models[q].predict(x_te))).mean()) for q in (0.1, 0.5, 0.9)}
    report["feature_importance"] = _importance(model, x_te, y_te, seed)

    out.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out / "recovery.joblib", compress=3)
    (out / "recovery-report.json").write_text(json.dumps(report, indent=1))
    return report


def _importance(model: RecoveryModel, x: np.ndarray, y: np.ndarray, seed: int, n: int = 4000) -> dict[str, float]:
    """Permutation importance of the median model, as the rise in mean absolute log error."""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(y), size=min(n, len(y)), replace=False)
    xs, ys = x[idx], y[idx]
    base = np.abs(model.models[0.5].predict(xs) - ys).mean()
    groups: dict[str, list[int]] = defaultdict(list)
    for i, name in enumerate(model.names):
        groups[name.split("_pc")[0] if "_pc" in name else name].append(i)
    out = {}
    for g, cols in groups.items():
        xp = xs.copy()
        perm = rng.permutation(len(xs))
        xp[:, cols] = xs[perm][:, cols]
        out[g] = float(np.abs(model.models[0.5].predict(xp) - ys).mean() - base)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


PHASE_NAMES = PHASES
