"""What else will this incident hit? Ranking services by how likely they are to post an incident next.

An example is a pair: an incident at an upstream provider u that began at time t, and another
service v. The label is whether v posted an incident of its own that began between 45 minutes before
and 180 minutes after t. Vendors often notice a dependency's failure before the dependency admits
it, which is why the window opens before t.

Every feature is computed from history strictly before t: how often v failed alongside u before, how
often v's own incident reports named u, how often v fails at all, and whether the census found v
using u for DNS, its edge network or hosting. So the model can be evaluated on a later period exactly
as it would have run live.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, brier_score_loss

from mercalli.domain import Category

WINDOW_BEFORE = 45  # minutes
WINDOW_AFTER = 180
RATE_LOOKBACK_DAYS = 365
HISTORY_LAG = 24 * 60  # minutes before an incident's own reports count as history
UPSTREAM_CATEGORIES = {"cloud", "edge", "dns", "hosting", "identity", "ai"}
UPSTREAM_EXTRA = {"github", "twilio", "sendgrid", "mongodb", "datadog", "atlassian", "slack", "supabase"}
CATEGORIES = [c.value for c in Category]
FEATURES = [
    "log_rate_v",
    "p_window_v",
    "prior_upstream_n",
    "prior_co",
    "log_lift",
    "prior_named_co",
    "named",
    "named_share",
    "generic_share_v",
    "dep_dns",
    "dep_edge",
    "dep_hosting",
    "region_match",
    "impact",
    "upstream_cat",
    "downstream_cat",
    "same_cat",
    "hour_sin",
    "hour_cos",
]
CAT_FEATURES = ["upstream_cat", "downstream_cat"]
# The model sees only evidence about the pair. Categories and the hour were tried and only added
# noise: positives are rare (a few hundred a year), and a small, heavily regularized model on these
# features ranks better on the held out period than larger ones.
MODEL_FEATURES = [
    "named",
    "named_share",
    "prior_named_co",
    "dep_dns",
    "dep_edge",
    "dep_hosting",
    "region_match",
    "log_rate_v",
    "generic_share_v",
    "impact",
    "log_lift",
]
MODEL_IDX = [FEATURES.index(f) for f in MODEL_FEATURES]


def is_upstream(service_id: str, category: str) -> bool:
    return category in UPSTREAM_CATEGORIES or service_id in UPSTREAM_EXTRA


def _minutes(col: pl.Series) -> np.ndarray:
    return (col.cast(pl.Int64) // 60_000_000).to_numpy()  # microseconds since epoch to minutes


@dataclass
class History:
    """Incident start times and attributes as arrays, indexed by service."""

    services: list[str]
    category: dict[str, str]
    starts: dict[str, np.ndarray]  # sorted minutes since epoch
    mentions: dict[str, list[list[str]]]  # aligned with starts
    generic: dict[str, np.ndarray]  # aligned with starts, bool
    coverage: dict[str, int]  # first minute we have data for the service
    _named: dict[tuple[str, str], np.ndarray] = field(default_factory=dict, repr=False)

    def named_prefix(self, v: str, u: str) -> np.ndarray:
        """Prefix counts of v's incidents naming u: element k counts incidents 0..k-1."""
        key = (v, u)
        if key not in self._named:
            flags = np.fromiter((u in m for m in self.mentions[v]), dtype=np.int32, count=len(self.mentions[v]))
            self._named[key] = np.concatenate([[0], np.cumsum(flags)])
        return self._named[key]

    @classmethod
    def from_frame(cls, incidents: pl.DataFrame) -> History:
        # Planned maintenance is not a failure, so it is neither a label nor history here.
        d = incidents.filter(pl.col("start").is_not_null() & (pl.col("impact") != "maintenance")).sort("start")
        starts, mentions, generic, coverage, category = {}, {}, {}, {}, {}
        for (sid,), g in d.group_by(["service_id"], maintain_order=True):
            m = _minutes(g["start"])
            starts[sid] = m
            mentions[sid] = [list(x) if x is not None else [] for x in g["mentions"].to_list()]
            generic[sid] = g["generic_upstream"].fill_null(False).to_numpy()
            coverage[sid] = int(m[0])
            category[sid] = g["category"][0]
        return cls(sorted(starts), category, starts, mentions, generic, coverage)


@dataclass
class Dependencies:
    """Census evidence: for a downstream service, which providers serve its DNS, edge and hosting."""

    dns: dict[str, set[str]] = field(default_factory=dict)
    edge: dict[str, set[str]] = field(default_factory=dict)
    hosting: dict[str, set[str]] = field(default_factory=dict)
    regions: dict[str, set[str]] = field(default_factory=dict)  # "aws:us-east-1" style

    def flags(self, u: str, v: str) -> tuple[float, float, float]:
        return (float(u in self.dns.get(v, ())), float(u in self.edge.get(v, ())), float(u in self.hosting.get(v, ())))


def build_examples(
    hist: History,
    upstream: pl.DataFrame,
    deps: Dependencies,
    *,
    since: int | None = None,
    until: int | None = None,
    label: str = "named",
) -> tuple[np.ndarray, np.ndarray, list[tuple[str, str, int]]]:
    """Rows for every (upstream incident, candidate service) pair with t in [since, until).

    `upstream` holds the upstream incidents: service_id, start, impact_rank, regions.
    Counts are exclusive: they use only incidents that began before each example's t.

    With `label="named"` (the default) a pair is positive when the other service posts an incident in
    the window whose reports name the upstream provider: a knock-on effect it attributed publicly.
    With `label="any"` any incident in the window counts, which mostly measures how often a service
    fails at all, and is kept for comparison.
    """
    services = hist.services
    vidx = {s: i for i, s in enumerate(services)}
    nv = len(services)
    rows: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    meta: list[tuple[str, str, int]] = []
    vcat = np.array(
        [
            CATEGORIES.index(hist.category[s]) if hist.category[s] in CATEGORIES else CATEGORIES.index("other")
            for s in services
        ],
        dtype=np.float32,
    )
    cov = np.array([hist.coverage[s] for s in services])
    # Precompute generic share prefix sums for each v.
    gen_prefix = {s: np.concatenate([[0], np.cumsum(hist.generic[s])]) for s in services}

    for (u,), g in upstream.sort("start").group_by(["service_id"], maintain_order=True):
        if u not in vidx:
            continue
        t = _minutes(g["start"])
        impact = g["impact_rank"].to_numpy()
        regions = g["regions"].to_list()
        n = len(t)
        # Label matrix: does v start an incident within the window of each u incident?
        lab = np.zeros((n, nv), dtype=bool)
        lab_named = np.zeros((n, nv), dtype=bool)
        named_prior = np.zeros((n, nv), dtype=np.float32)
        before_n = np.zeros((n, nv), dtype=np.float32)
        generic_prior = np.zeros((n, nv), dtype=np.float32)
        rate = np.zeros((n, nv), dtype=np.float32)
        for j, v in enumerate(services):
            if v == u:
                continue
            sv = hist.starts[v]
            lo = np.searchsorted(sv, t - WINDOW_BEFORE, side="left")
            hi = np.searchsorted(sv, t + WINDOW_AFTER, side="right")
            lab[:, j] = hi > lo
            named_cum = hist.named_prefix(v, u)
            lab_named[:, j] = named_cum[hi] > named_cum[lo]
            # Incidents of v that began strictly before t - WINDOW_BEFORE are history, never label.
            # What they said only counts once they are a day old, by when their reports are final.
            k = lo
            k_said = np.searchsorted(sv, t - HISTORY_LAG, side="left")
            before_n[:, j] = k_said
            generic_prior[:, j] = gen_prefix[v][k_said]
            look = np.searchsorted(sv, t - RATE_LOOKBACK_DAYS * 1440, side="left")
            span_days = np.clip((t - np.maximum(cov[j], t - RATE_LOOKBACK_DAYS * 1440)) / 1440, 1, None)
            rate[:, j] = (k - look) / span_days
            named_prior[:, j] = named_cum[k_said]
        # Co-failure counts along u's own history, using only earlier incidents of u whose windows
        # had closed before this one's opened. A nearer one could share this example's label.
        closed = np.searchsorted(t + WINDOW_AFTER, t - WINDOW_BEFORE, side="left")
        cum_any = np.vstack([np.zeros((1, nv)), np.cumsum(lab, axis=0)])
        cum_named = np.vstack([np.zeros((1, nv)), np.cumsum(lab_named, axis=0)])
        co_prior = cum_any[closed]
        co_named_prior = cum_named[closed]
        n_prior = closed.astype(np.float32)[:, None]
        window_days = (WINDOW_BEFORE + WINDOW_AFTER) / 1440
        p_win = 1 - np.exp(-rate * window_days)
        expected = n_prior * p_win
        log_lift = np.log((co_prior + 1) / (expected + 1))
        ucat = CATEGORIES.index(hist.category.get(u, "other")) if hist.category.get(u) in CATEGORIES else 15
        dep_all = np.array([deps.flags(u, v) for v in services], dtype=np.float32)
        region_sets = [deps.regions.get(v, set()) for v in services]
        for i in range(n):
            if (since is not None and t[i] < since) or (until is not None and t[i] >= until):
                continue
            covered = cov <= t[i] - WINDOW_BEFORE
            covered[vidx[u]] = False
            idx = np.nonzero(covered)[0]
            if len(idx) == 0:
                continue
            dep = dep_all[idx]
            wanted = {f"{u}:{r}" for r in (regions[i] or [])}
            region_match = (
                np.array([float(bool(wanted & region_sets[j])) for j in idx], dtype=np.float32)
                if wanted
                else np.zeros(len(idx), dtype=np.float32)
            )
            hour = (t[i] // 60) % 24
            x = np.column_stack(
                [
                    np.log(rate[i, idx] + 1e-3),
                    p_win[i, idx],
                    np.full(len(idx), math.log1p(n_prior[i, 0])),
                    np.log1p(co_prior[i, idx]),
                    log_lift[i, idx],
                    np.log1p(co_named_prior[i, idx]),
                    np.log1p(named_prior[i, idx]),
                    named_prior[i, idx] / np.maximum(before_n[i, idx], 1),
                    generic_prior[i, idx] / np.maximum(before_n[i, idx], 1),
                    dep[:, 0],
                    dep[:, 1],
                    dep[:, 2],
                    region_match,
                    np.full(len(idx), float(impact[i])),
                    np.full(len(idx), float(ucat)),
                    vcat[idx],
                    (vcat[idx] == ucat).astype(np.float32),
                    np.full(len(idx), math.sin(2 * math.pi * hour / 24)),
                    np.full(len(idx), math.cos(2 * math.pi * hour / 24)),
                ]
            ).astype(np.float32)
            rows.append(x)
            labels.append((lab_named if label == "named" else lab)[i, idx])
            meta.extend((u, services[j], int(t[i])) for j in idx)
    if not rows:
        return np.zeros((0, len(FEATURES)), np.float32), np.zeros(0, bool), []
    return np.vstack(rows), np.concatenate(labels), meta


@dataclass
class BlastModel:
    version: str
    model: HistGradientBoostingClassifier
    calibrator: IsotonicRegression
    trained_at: str
    # Live lookup tables computed over all history, so inference needs no dataset.
    live: dict[str, Any] = field(default_factory=dict)

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Calibrated probabilities from the full feature matrix (see FEATURES)."""
        raw = self.model.predict_proba(x[:, MODEL_IDX])[:, 1]
        return self.calibrator.predict(raw)


def fit_blast(x: np.ndarray, y: np.ndarray, *, seed: int = 0) -> HistGradientBoostingClassifier:
    m = HistGradientBoostingClassifier(
        learning_rate=0.05,
        max_iter=200,
        max_leaf_nodes=8,
        min_samples_leaf=200,
        l2_regularization=5.0,
        early_stopping=False,
        random_state=seed,
    )
    m.fit(x[:, MODEL_IDX], y)
    return m


def ranking_metrics(
    scores: np.ndarray, y: np.ndarray, meta: list[tuple[str, str, int]], k: int = 10, *, boot: int = 0, seed: int = 0
) -> dict[str, Any]:
    """Pooled average precision, plus precision, recall and hit rate at k per upstream incident that
    had any positive. With `boot`, 95% intervals from resampling upstream incidents."""
    groups: dict[tuple[str, int], list[int]] = {}
    for i, (u, _, t) in enumerate(meta):
        groups.setdefault((u, t), []).append(i)
    prec, rec, hit = [], [], []
    for idx in groups.values():
        yy = y[idx]
        if not yy.any():
            continue
        order = np.argsort(-scores[idx])[:k]
        top = yy[order]
        prec.append(top.mean())
        rec.append(top.sum() / yy.sum())
        hit.append(float(top.any()))
    out: dict[str, Any] = {
        "pairs": len(y),
        "positives": int(y.sum()),
        "base_rate": float(y.mean()) if len(y) else 0.0,
        "average_precision": float(average_precision_score(y, scores)) if y.any() else 0.0,
        "upstream_incidents_with_positive": len(prec),
        f"precision_at_{k}": float(np.mean(prec)) if prec else 0.0,
        f"recall_at_{k}": float(np.mean(rec)) if rec else 0.0,
        f"hit_at_{k}": float(np.mean(hit)) if hit else 0.0,
    }
    if boot and prec:
        rng = np.random.default_rng(seed)
        arr = {"recall": np.array(rec), "hit": np.array(hit), "precision": np.array(prec)}
        n = len(rec)
        draws = rng.integers(0, n, size=(boot, n))
        out["ci95"] = {
            f"{name}_at_{k}": [
                float(np.percentile(a[draws].mean(1), 2.5)),
                float(np.percentile(a[draws].mean(1), 97.5)),
            ]
            for name, a in arr.items()
        }
    return out


def brier(scores: np.ndarray, y: np.ndarray) -> float:
    return float(brier_score_loss(y, np.clip(scores, 0, 1)))


def to_minutes(dt: datetime) -> int:
    return int(dt.timestamp() // 60)
