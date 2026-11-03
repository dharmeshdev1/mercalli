from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from mercalli.ml.blast import Dependencies, History, build_examples
from mercalli.ml.dataset import mentioned_providers
from mercalli.ml.recovery import Priors, Step, conformal_widening, snapshot_features

T0 = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def test_snapshot_features_only_see_the_past():
    steps = [
        Step(T0, "investigating", 1, 1),
        Step(T0 + timedelta(minutes=20), "identified", 3, 2),
        Step(T0 + timedelta(minutes=50), "monitoring", 3, 3),
    ]
    priors = Priors()
    f0 = snapshot_features(steps, 0, T0, "svc", "devtools", priors)
    f1 = snapshot_features(steps, 1, T0, "svc", "devtools", priors)
    assert f0["reached_identified"] == 0.0 and f0["severity"] == 1.0 and f0["n_components"] == 1.0
    assert f1["reached_identified"] == 1.0 and f1["reached_monitoring"] == 0.0
    assert f1["severity"] == 2.0, "the third update's severity must not leak into the second snapshot"
    assert math.isclose(float(f1["elapsed_log"]), math.log1p(20))
    live = snapshot_features(steps, 2, T0, "svc", "devtools", priors, now=T0 + timedelta(minutes=80))
    assert math.isclose(float(live["since_update_log"]), math.log1p(30))


def test_priors_shrink_small_services_towards_their_category():
    df = pl.DataFrame(
        {
            "service_id": ["a"] * 2 + ["b"] * 40 + ["b"] * 5,
            "category": ["devtools"] * 47,
            "duration_min": [600.0, 600.0] + [30.0] * 40 + [10_000.0] * 5,
            "retroactive": [False] * 47,
            "impact": ["minor"] * 42 + ["maintenance"] * 5,
        }
    )
    p = Priors.fit(df)
    a_m, _, _ = p.lookup("a", "devtools")
    b_m, _, _ = p.lookup("b", "devtools")
    assert math.log1p(30) < a_m < math.log1p(600), "two incidents are not enough to trust fully"
    assert abs(b_m - math.log1p(30)) < 0.05, "maintenance windows are not outages and must not move the prior"


class _Const:
    def __init__(self, v: float) -> None:
        self.v = v

    def predict(self, x: np.ndarray) -> np.ndarray:
        return np.full(len(x), self.v)


def test_conformal_widening_restores_coverage():
    rng = np.random.default_rng(0)
    y = rng.normal(0, 1, 5000)
    narrow = {0.1: _Const(-0.5), 0.9: _Const(0.5)}  # covers about 38%, far below 80%
    widen = conformal_widening(narrow, np.zeros((5000, 1)), y)  # type: ignore[arg-type]
    y_new = rng.normal(0, 1, 20000)
    coverage = ((y_new >= -0.5 - widen) & (y_new <= 0.5 + widen)).mean()
    assert 0.78 < coverage < 0.83


def _history(starts: dict[str, list[int]], mentions: dict[str, list[list[str]]] | None = None) -> History:
    def ts(m: int) -> datetime:
        return T0 + timedelta(minutes=m)

    rows = []
    for sid, ms in starts.items():
        for i, m in enumerate(ms):
            rows.append(
                {
                    "service_id": sid,
                    "category": "edge" if sid == "cdn" else "devtools",
                    "start": ts(m),
                    "mentions": (mentions or {}).get(sid, [[]] * len(ms))[i],
                    "generic_upstream": False,
                    "impact": "minor",
                }
            )
    return History.from_frame(pl.DataFrame(rows))


def test_blast_labels_and_counts_are_exclusive_of_the_example():
    from mercalli.ml.blast import FEATURES

    hist = _history(
        {"cdn": [100_000, 200_000, 300_000], "app": [1_000, 100_030, 200_100, 400_000], "other": [5_000, 200_050]},
        mentions={"app": [[], ["cdn"], [], []], "other": [[], []]},
    )
    up = pl.DataFrame(
        {
            "service_id": ["cdn"] * 3,
            "start": [T0 + timedelta(minutes=m) for m in (100_000, 200_000, 300_000)],
            "impact_rank": [2, 2, 1],
            "regions": [[], [], []],
        }
    )
    deps = Dependencies(edge={"app": {"cdn"}})
    f = {name: i for i, name in enumerate(FEATURES)}

    def rows_for(label: str) -> dict[tuple[str, int], tuple[np.ndarray, bool]]:
        x, y, meta = build_examples(hist, up, deps, label=label)
        return {(m[1], m[2] - int(T0.timestamp() // 60)): (x[i], bool(y[i])) for i, m in enumerate(meta)}

    anyl = rows_for("any")
    assert anyl[("app", 100_000)][1] and anyl[("app", 200_000)][1] and not anyl[("app", 300_000)][1]
    assert anyl[("other", 200_000)][1], "any incident in the window counts under the any label"
    x1, x2, x3 = (anyl[("app", t)][0] for t in (100_000, 200_000, 300_000))
    assert x1[f["prior_co"]] == 0.0, "the first co-failure cannot count itself"
    assert math.isclose(x2[f["prior_co"]], math.log1p(1), rel_tol=1e-6)
    assert math.isclose(x3[f["prior_co"]], math.log1p(2), rel_tol=1e-6)
    assert x1[f["dep_edge"]] == 1.0, "census says app sits behind the cdn's edge"

    named = rows_for("named")
    assert named[("app", 100_000)][1], "app's incident in that window named the cdn"
    assert not named[("app", 200_000)][1], "an incident that does not name the cdn is not attributed"
    assert not named[("other", 200_000)][1]
    assert math.isclose(named[("app", 200_000)][0][f["prior_named_co"]], math.log1p(1), rel_tol=1e-6)
    assert math.isclose(named[("app", 200_000)][0][f["named"]], math.log1p(1), rel_tol=1e-6)


def test_provider_mentions():
    text = "We are seeing elevated errors due to an issue with our upstream provider AWS in us-east-1."
    assert mentioned_providers("vercel", text) == ["aws"]
    assert mentioned_providers("aws", text) == [], "a service never blames itself"
    assert mentioned_providers("x", "Slack notifications are delayed") == ["slack"]
    assert mentioned_providers("x", "We fixed the backlog") == []


def test_live_evaluation_scores_first_and_all_forecasts(db):
    from mercalli.domain import Impact, Incident, Phase, Update
    from mercalli.ml.live_eval import evaluate_live
    from mercalli.store import PredictionRow

    start = T0
    inc = Incident(
        service_id="svc",
        source_id="1",
        title="x",
        impact=Impact.MINOR,
        status=Phase.RESOLVED,
        created_at=start,
        resolved_at=start + timedelta(minutes=60),
        updates=[Update(at=start, status=Phase.INVESTIGATING)],
    )
    db.upsert_incident(inc, seen_at=start, seen_open=True)
    with db.session() as s:
        s.add(
            PredictionRow(
                incident_id="svc:1",
                made_at=start,
                model_version="t",
                elapsed_min=0,
                phase="investigating",
                p10=20,
                p50=50,
                p90=120,
            )
        )
        s.add(
            PredictionRow(
                incident_id="svc:1",
                made_at=start + timedelta(minutes=30),
                model_version="t",
                elapsed_min=30,
                phase="identified",
                p10=5,
                p50=100,
                p90=200,
            )
        )
    r = evaluate_live(db)
    assert r["n_incidents"] == 1 and r["n_forecasts"] == 2
    assert r["first_forecast"]["coverage_80"] == 1.0 and r["first_forecast"]["within_2x"] == 1.0
    assert r["all_forecasts"]["within_2x"] == 0.5, "the second forecast said 100 minutes when 30 were left"


def test_neighbors_only_use_incidents_that_ended_before():
    from mercalli.ml.recovery import Neighbors

    rng = np.random.default_rng(1)
    base = rng.normal(size=(80, 384)).astype(np.float32)
    durations = np.log1p(np.full(80, 30.0))
    durations[40:] = np.log1p(600.0)  # the later half of history ran long
    ended = np.arange(80) * 100  # incident i ended at minute 100 * i
    nb = Neighbors.fit(base, durations, ended, ["svc"] * 80)
    q = base[[45, 45]] + 0.01
    early, late = nb.features(q, np.array([3000, 9000]), ["svc", "svc"])
    assert math.isclose(float(early[0]), math.log1p(30.0), rel_tol=1e-5), "only incidents over by minute 3000"
    assert float(late[0]) > float(early[0]), "later queries may use the long incidents that had ended"
    assert np.isnan(nb.features(q[:1], np.array([300]), ["svc"])[0, 0]), "too few past incidents gives no value"


def test_blast_counts_ignore_upstream_windows_still_open():
    from mercalli.ml.blast import FEATURES

    hist = _history({"cdn": [50_000, 50_010], "app": [1_000, 50_030]}, mentions={"app": [[], ["cdn"]]})
    up = pl.DataFrame(
        {
            "service_id": ["cdn", "cdn"],
            "start": [T0 + timedelta(minutes=m) for m in (50_000, 50_010)],
            "impact_rank": [2, 2],
            "regions": [[], []],
        }
    )
    x, y, meta = build_examples(hist, up, Dependencies(), label="named")
    f = {name: i for i, name in enumerate(FEATURES)}
    second = next(i for i, m in enumerate(meta) if m[1] == "app" and m[2] - int(T0.timestamp() // 60) == 50_010)
    assert y[second], "app blamed the cdn inside the second window too"
    assert x[second][f["prior_named_co"]] == 0.0, "the first window was still open, so it cannot count as history"
    assert x[second][f["prior_upstream_n"]] == 0.0
