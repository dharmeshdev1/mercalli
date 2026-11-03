"""How long will this incident last? A calibrated forecast of the time left until resolution.

Training examples are snapshots: one per vendor update, taken at the moment the update was posted,
labelled with the minutes that were still left until the incident was resolved. Features only use
what was known at that moment, so a forecast made live looks exactly like one made in training.

Three gradient boosted quantile models predict the 10th, 50th and 90th percentile of the log of the
remaining time. The interval is then widened by split conformal calibration (conformalized quantile
regression, Romano, Patterson and Candes, 2019) on a later, held out period, which makes the 80%
interval hold its coverage on data the models never saw.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import polars as pl
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingRegressor

from mercalli.domain import Category

PHASES = ["investigating", "identified", "monitoring", "other"]
CATEGORIES = [c.value for c in Category]
TEXT_DIMS = 16
ALPHA = 0.2  # 80% intervals
QUANTILES = (0.1, 0.5, 0.9)
OPEN_PHASES = {"investigating", "identified", "monitoring", "in_progress", "verifying", "scheduled"}
SHRINK = 8.0  # incidents worth of weight the category prior carries against a service's own history


@dataclass
class Priors:
    """Per service and per category history, fitted on the training period only."""

    service: dict[str, tuple[float, int, float]] = field(default_factory=dict)  # log median, n, share > 2h
    category: dict[str, float] = field(default_factory=dict)
    global_log_median: float = math.log1p(60.0)

    @classmethod
    def fit(cls, incidents: pl.DataFrame) -> Priors:
        d = incidents.filter(
            pl.col("duration_min").is_not_null() & ~pl.col("retroactive") & (pl.col("impact") != "maintenance")
        )
        d = d.with_columns(pl.col("duration_min").log1p().alias("ld"))
        p = cls(global_log_median=float(d["ld"].median() or math.log1p(60)))
        for row in d.group_by("category").agg(pl.col("ld").median()).iter_rows(named=True):
            p.category[row["category"]] = float(row["ld"])
        agg = d.group_by("service_id").agg(
            pl.col("ld").median().alias("m"),
            pl.len().alias("n"),
            (pl.col("duration_min") > 120).mean().alias("long"),
        )
        for row in agg.iter_rows(named=True):
            p.service[row["service_id"]] = (float(row["m"]), int(row["n"]), float(row["long"]))
        return p

    def lookup(self, service_id: str, category: str) -> tuple[float, float, float]:
        cat = self.category.get(category, self.global_log_median)
        m, n, long_share = self.service.get(service_id, (cat, 0, 0.25))
        w = n / (n + SHRINK)
        return w * m + (1 - w) * cat, math.log1p(n), w * long_share + (1 - w) * 0.25


_DURATION = r"(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?)\b"
_NEXT_UPDATE = re.compile(
    r"(?:next|another|further|an?) update (?:in|within) (?:the next |approximately |about |roughly )?" + _DURATION,
    re.IGNORECASE,
)
_ETA = re.compile(
    r"(?:expect|estimat|anticipat|should be (?:fully )?(?:resolved|restored|recovered|complete))[^.]{0,80}?"
    r"(?:in|within|over) (?:the next |approximately |about |roughly |around )?" + _DURATION,
    re.IGNORECASE,
)
_FIX = re.compile(r"\b(fix|fixes|fixed|mitigat\w*|roll(?:ed|ing)? back|rollback|deployed|patch\w*|restor\w*)\b", re.I)
_UPSTREAM = re.compile(r"\b(third[- ]party|upstream|provider|vendor|partner|carrier)\b", re.IGNORECASE)


def _minutes_from(match: re.Match[str] | None) -> float:
    if not match:
        return math.nan
    value, unit = float(match.group(1)), match.group(2).lower()
    return value * 60 if unit.startswith(("hour", "hr")) else value


def text_cues(body: str) -> dict[str, float]:
    """What the latest vendor update says about timing: a stated ETA, a promised next update, a fix."""
    eta = _minutes_from(_ETA.search(body))
    nxt = _minutes_from(_NEXT_UPDATE.search(body))
    return {
        "eta_log": math.log1p(eta) if not math.isnan(eta) else math.nan,
        "next_update_log": math.log1p(nxt) if not math.isnan(nxt) else math.nan,
        "says_fix": float(bool(_FIX.search(body))),
        "says_upstream": float(bool(_UPSTREAM.search(body))),
    }


@dataclass(frozen=True)
class Step:
    """One vendor update, as far as the features are concerned."""

    at: datetime
    status: str
    n_components: int = 0
    severity: int = 0
    body: str = ""


def phase_of(status: str) -> str:
    return status if status in ("investigating", "identified", "monitoring") else "other"


def snapshot_features(
    steps: Sequence[Step],
    k: int,
    start: datetime,
    service_id: str,
    category: str,
    priors: Priors,
    now: datetime | None = None,
    nn: Sequence[float] | None = None,
) -> dict[str, float | str]:
    """Features at update k (or at `now`, after update k, for live forecasts).

    `nn` holds the neighbor features of the incident (see `Neighbors.features`), or None.
    """
    t = now or steps[k].at
    elapsed = max(0.0, (t - start).total_seconds() / 60)
    prev = steps[k - 1].at if k > 0 else start
    seen = [s.status for s in steps[: k + 1]]
    prior_m, prior_n, prior_long = priors.lookup(service_id, category)
    return {
        "elapsed_log": math.log1p(elapsed),
        "since_update_log": math.log1p(max(0.0, (t - steps[k].at).total_seconds() / 60)),
        "gap_log": math.log1p(max(0.0, (steps[k].at - prev).total_seconds() / 60)),
        "n_updates": float(k + 1),
        "phase": phase_of(steps[k].status),
        "reached_identified": float("identified" in seen),
        "reached_monitoring": float("monitoring" in seen),
        "severity": float(max(s.severity for s in steps[: k + 1])),
        "n_components": float(max(s.n_components for s in steps[: k + 1])),
        "prior_log_median": prior_m,
        "prior_log_n": prior_n,
        "prior_long_share": prior_long,
        "prior_minus_elapsed": prior_m - math.log1p(elapsed),
        "hour_sin": math.sin(2 * math.pi * start.hour / 24),
        "hour_cos": math.cos(2 * math.pi * start.hour / 24),
        "weekend": float(start.weekday() >= 5),
        "nn_log_median": nn[0] if nn is not None else math.nan,
        "nn_long_share": nn[1] if nn is not None else math.nan,
        "nn_similarity": nn[2] if nn is not None else math.nan,
        "nn_same_service": nn[3] if nn is not None else math.nan,
        "nn_minus_elapsed": (nn[0] - math.log1p(elapsed)) if nn is not None else math.nan,
        **text_cues(steps[k].body),
        "category": category if category in CATEGORIES else "other",
    }


NUMERIC = [
    "elapsed_log",
    "since_update_log",
    "gap_log",
    "n_updates",
    "reached_identified",
    "reached_monitoring",
    "severity",
    "n_components",
    "prior_log_median",
    "prior_log_n",
    "prior_long_share",
    "prior_minus_elapsed",
    "hour_sin",
    "hour_cos",
    "weekend",
    "nn_log_median",
    "nn_long_share",
    "nn_similarity",
    "nn_same_service",
    "nn_minus_elapsed",
    "eta_log",
    "next_update_log",
    "says_fix",
    "says_upstream",
]
CATEGORICAL = ["phase", "category"]


def make_snapshots(incidents: pl.DataFrame, updates: pl.DataFrame) -> list[dict[str, object]]:
    """One row per update of every resolved, non retroactive incident with a real timeline."""
    # Planned maintenance has a planned length; it is not an outage and would only teach the model
    # to copy maintenance windows.
    eligible = incidents.filter(
        (pl.col("impact") != "maintenance")
        & (pl.col("first_status").is_null() | ~pl.col("first_status").is_in(["scheduled", "in_progress"]))
        & pl.col("resolved_at").is_not_null()
        & ~pl.col("retroactive")
        & (pl.col("n_updates") >= 2)
        & (pl.col("duration_min") > 0)
        & (pl.col("duration_min") < 60 * 24 * 14)
    )
    meta = {
        r["id"]: r
        for r in eligible.select(
            "id", "service_id", "category", "start", "resolved_at", "title", "first_body"
        ).iter_rows(named=True)
    }
    steps: dict[str, list[Step]] = defaultdict(list)
    for r in updates.filter(pl.col("incident_id").is_in(list(meta))).sort("incident_id", "seq").iter_rows(named=True):
        steps[r["incident_id"]].append(
            Step(
                r["at"],
                r["status"],
                int(r["n_components"] or 0),
                int(r["severity_so_far"] or 0),
                r.get("body") or "",
            )
        )
    rows: list[dict[str, object]] = []
    for iid, st in steps.items():
        m = meta[iid]
        # Vendors sometimes backdate their first update before the incident's own start; the
        # incident began at whichever came first.
        start = min(m["start"], st[0].at)
        for k, s in enumerate(st):
            if s.status not in OPEN_PHASES:
                break
            remaining = (m["resolved_at"] - s.at).total_seconds() / 60
            if remaining < 0:
                break
            rows.append(
                {
                    "incident_id": iid,
                    "k": k,
                    "at": s.at,
                    "start": start,
                    "service_id": m["service_id"],
                    "category": m["category"],
                    "remaining": remaining,
                    "steps": st,
                }
            )
    return rows


@dataclass
class TextProjector:
    """PCA of title, first update and latest update embeddings, down to a few dimensions each."""

    title: PCA
    body: PCA
    latest: PCA

    @classmethod
    def fit(
        cls, title_vecs: np.ndarray, body_vecs: np.ndarray, latest_vecs: np.ndarray, seed: int = 0
    ) -> TextProjector:
        return cls(
            PCA(TEXT_DIMS, random_state=seed).fit(title_vecs),
            PCA(TEXT_DIMS, random_state=seed).fit(body_vecs),
            PCA(TEXT_DIMS, random_state=seed).fit(latest_vecs),
        )

    def transform(self, title_vecs: np.ndarray, body_vecs: np.ndarray, latest_vecs: np.ndarray) -> np.ndarray:
        return np.hstack(
            [self.title.transform(title_vecs), self.body.transform(body_vecs), self.latest.transform(latest_vecs)]
        ).astype(np.float32)

    @property
    def names(self) -> list[str]:
        return [f"{part}_pc{i}" for part in ("title", "body", "latest") for i in range(TEXT_DIMS)]


NEIGHBOR_K = 25
NEIGHBOR_DIMS = 64
LONG_LOG = math.log1p(120)


@dataclass
class Neighbors:
    """Past incidents in a compact embedding space, with when they ended and how long they lasted.

    For a new incident, the most similar past incidents are found by cosine similarity of title and
    first update, and their outcomes become features: how long incidents like this one took. Only
    incidents that had already ended when the new one began are eligible, in training and live alike,
    so the feature never knows anything the forecaster could not have known.
    """

    pca: PCA
    vectors: np.ndarray  # float16, unit length
    log_duration: np.ndarray
    ended: np.ndarray  # minutes since epoch
    service: np.ndarray

    @classmethod
    def fit(
        cls, vecs: np.ndarray, log_duration: np.ndarray, ended: np.ndarray, service: list[str], seed: int = 0
    ) -> Neighbors:
        pca = PCA(NEIGHBOR_DIMS, random_state=seed).fit(vecs)
        return cls(
            pca,
            _unit(pca.transform(vecs)).astype(np.float16),
            log_duration.astype(np.float32),
            ended.astype(np.int64),
            np.asarray(service),
        )

    def features(self, vecs: np.ndarray, started: np.ndarray, service: list[str], chunk: int = 512) -> np.ndarray:
        """Columns: median log duration of the nearest past incidents, share over two hours, mean
        similarity, and median log duration of the nearest past incidents at the same service."""
        out = np.full((len(vecs), 4), np.nan, dtype=np.float32)
        if len(vecs) == 0 or len(self.vectors) == 0:
            return out
        z = _unit(self.pca.transform(vecs)).astype(np.float32)
        base = self.vectors.astype(np.float32)
        for a in range(0, len(z), chunk):
            sims = z[a : a + chunk] @ base.T
            sims[self.ended[None, :] >= started[a : a + chunk, None]] = -np.inf
            top = np.argpartition(-sims, min(NEIGHBOR_K * 8, sims.shape[1] - 1), axis=1)[:, : NEIGHBOR_K * 8]
            for i in range(sims.shape[0]):
                cand = top[i][np.isfinite(sims[i, top[i]])]
                if len(cand) < 5:
                    continue
                cand = cand[np.argsort(-sims[i, cand])]
                best = cand[:NEIGHBOR_K]
                d = self.log_duration[best]
                out[a + i, 0] = float(np.median(d))
                out[a + i, 1] = float((d > LONG_LOG).mean())
                out[a + i, 2] = float(sims[i, best].mean())
                same = cand[self.service[cand] == service[a + i]][:5]
                if len(same) >= 3:
                    out[a + i, 3] = float(np.median(self.log_duration[same]))
        return out


def _unit(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=1, keepdims=True).clip(1e-6)


def matrix(feats: list[dict[str, float | str]], text: np.ndarray) -> np.ndarray:
    """Numeric features, then categorical codes, then text components."""
    if not feats:
        return np.zeros((0, len(NUMERIC) + len(CATEGORICAL) + 3 * TEXT_DIMS), np.float32)
    num = np.array([[float(f[c]) for c in NUMERIC] for f in feats], dtype=np.float32)
    cat = np.array(
        [[PHASES.index(str(f["phase"])), CATEGORIES.index(str(f["category"]))] for f in feats], dtype=np.float32
    )
    return np.hstack([num, cat, text])


def feature_names(proj: TextProjector) -> list[str]:
    return NUMERIC + CATEGORICAL + proj.names


@dataclass
class RecoveryModel:
    version: str
    priors: Priors
    projector: TextProjector
    models: dict[float, HistGradientBoostingRegressor]
    conformal: float = 0.0  # widening in log space, from the calibration period
    names: list[str] = field(default_factory=list)
    neighbors: Neighbors | None = None

    def predict_log(self, x: np.ndarray) -> np.ndarray:
        """Columns: calibrated low, median, calibrated high, in log1p minutes."""
        lo = self.models[0.1].predict(x) - self.conformal
        mid = self.models[0.5].predict(x)
        hi = self.models[0.9].predict(x) + self.conformal
        mid = np.clip(mid, lo, hi)
        return np.stack([np.maximum(lo, 0.0), mid, hi], axis=1)

    def predict_minutes(self, x: np.ndarray) -> np.ndarray:
        return np.expm1(self.predict_log(x))


def fit_quantiles(
    x: np.ndarray, y: np.ndarray, w: np.ndarray, *, seed: int = 0, categorical: list[int] | None = None
) -> dict[float, HistGradientBoostingRegressor]:
    models = {}
    for q in QUANTILES:
        m = HistGradientBoostingRegressor(
            loss="quantile",
            quantile=q,
            learning_rate=0.05,
            max_iter=600,
            max_leaf_nodes=31,
            min_samples_leaf=40,
            l2_regularization=1.0,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=30,
            categorical_features=categorical,
            random_state=seed,
        )
        m.fit(x, y, sample_weight=w)
        models[q] = m
    return models


def conformal_widening(
    models: dict[float, HistGradientBoostingRegressor], x: np.ndarray, y: np.ndarray, alpha: float = ALPHA
) -> float:
    """Split conformal score quantile for conformalized quantile regression."""
    lo, hi = models[0.1].predict(x), models[0.9].predict(x)
    scores = np.maximum(lo - y, y - hi)
    n = len(scores)
    level = min(1.0, math.ceil((n + 1) * (1 - alpha)) / n)
    return float(np.quantile(scores, level, method="higher"))
