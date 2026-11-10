"""Measure the deployed forecaster on the forecasts it actually made.

Backtests say how the procedure would have done. This says how the running model did: every forecast
is stored when it is made, and once the vendor resolves the incident, the forecast is scored against
what really happened. Incidents whose end Mercalli had to infer are left out, because their true
end time is unknown.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import numpy as np
from sqlalchemy import select

from mercalli.store import Database, IncidentRow, PredictionRow


def evaluate_live(db: Database, *, since: datetime | None = None) -> dict[str, Any]:
    with db.session() as s:
        q = (
            select(PredictionRow, IncidentRow)
            .join(IncidentRow, IncidentRow.id == PredictionRow.incident_id)
            .where(IncidentRow.resolved_at.is_not(None), IncidentRow.resolved_inferred.is_(False))
        )
        if since is not None:
            q = q.where(PredictionRow.made_at >= since)
        rows = s.execute(q).all()
    if not rows:
        return {"n_forecasts": 0, "n_incidents": 0}
    first: dict[str, tuple[PredictionRow, IncidentRow]] = {}
    for p, inc in rows:
        if inc.id not in first or p.made_at < first[inc.id][0].made_at:
            first[inc.id] = (p, inc)

    def score(pairs: list[tuple[PredictionRow, IncidentRow]]) -> dict[str, float]:
        # The query keeps only incidents with resolved_at set.
        truth = np.array(
            [max(0.0, (cast(datetime, inc.resolved_at) - p.made_at).total_seconds() / 60) for p, inc in pairs]
        )
        p10 = np.array([p.p10 for p, _ in pairs])
        p50 = np.array([p.p50 for p, _ in pairs])
        p90 = np.array([p.p90 for p, _ in pairs])
        ratio = np.maximum(p50, 1) / np.maximum(truth, 1)
        return {
            "n": len(pairs),
            "mae_log": float(np.abs(np.log1p(p50) - np.log1p(truth)).mean()),
            "median_abs_error_min": float(np.median(np.abs(p50 - truth))),
            "within_2x": float(((ratio >= 0.5) & (ratio <= 2)).mean()),
            "coverage_80": float(((truth >= p10) & (truth <= p90)).mean()),
        }

    all_pairs = [(p, inc) for p, inc in rows]
    first_made = min(p.made_at for p, _ in all_pairs)
    last_made = max(p.made_at for p, _ in all_pairs)
    return {
        "n_forecasts": len(all_pairs),
        "n_incidents": len(first),
        "window": {"from": first_made.isoformat(), "to": last_made.isoformat()},
        "first_forecast": score(list(first.values())),
        "all_forecasts": score(all_pairs),
        "note": "Scored against the vendor's own resolution time; incidents with an inferred end are excluded.",
    }
