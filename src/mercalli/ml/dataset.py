"""Turn the raw cache into analysis tables.

`incidents` has one row per incident with its timeline milestones. `updates` has one row per
vendor update. Both are rebuilt from the raw cache in full, which is cheap, so there is no
incremental state to get wrong.

Incident text is used to compute features but is not written to the published dataset: the
dataset carries titles, timings, statuses and embeddings, and links back to the vendor's own page.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from mercalli.backfill import RawCache
from mercalli.catalog import Service
from mercalli.domain import Impact, Incident, Phase
from mercalli.sources import statuspage, vendors

# Names a vendor uses when it blames someone else. Matched case-insensitively on word boundaries.
PROVIDER_ALIASES: dict[str, list[str]] = {
    "aws": [
        "aws",
        "amazon web services",
        "us-east-1",
        "us-west-2",
        "eu-west-1",
        "cloudfront",
        "amazon s3",
        "amazon ec2",
        "dynamodb",
        "route 53",
        "route53",
    ],
    "azure": ["azure", "microsoft cloud", "azure front door"],
    "gcp": ["google cloud", "gcp", "google compute engine", "gke", "google kubernetes engine"],
    "cloudflare": ["cloudflare"],
    "fastly": ["fastly"],
    "akamai": ["akamai"],
    "github": ["github"],
    "openai": ["openai", "chatgpt", "gpt-4", "gpt-5"],
    "twilio": ["twilio"],
    "sendgrid": ["sendgrid"],
    "okta": ["okta"],
    "auth0": ["auth0"],
    "stripe": ["stripe"],
    "digitalocean": ["digitalocean", "digital ocean"],
    "vercel": ["vercel"],
    "netlify": ["netlify"],
    "heroku": ["heroku"],
    "mongodb": ["mongodb atlas", "mongodb"],
    "datadog": ["datadog"],
    "slack": ["slack"],
    "atlassian": ["atlassian", "jira", "confluence", "bitbucket"],
    "letsencrypt": ["let's encrypt", "letsencrypt"],
    "ns1": ["ns1"],
    "pagerduty": ["pagerduty"],
}
_GENERIC_UPSTREAM = re.compile(
    r"\b(third[- ]party|upstream|downstream provider|cloud provider|hosting provider|infrastructure provider|"
    r"service provider|vendor)\b",
    re.IGNORECASE,
)
_ALIAS_RE = {
    pid: re.compile(r"\b(" + "|".join(re.escape(a) for a in aliases) + r")\b", re.IGNORECASE)
    for pid, aliases in PROVIDER_ALIASES.items()
}


def mentioned_providers(service_id: str, text: str) -> list[str]:
    """Providers named in an incident's text, excluding the service itself."""
    return [pid for pid, rx in _ALIAS_RE.items() if pid != service_id and rx.search(text)]


def _milestone(inc: Incident, phase: Phase) -> datetime | None:
    return next((u.at for u in inc.updates if u.status == phase), None)


def incident_row(inc: Incident, service: Service, source: str) -> dict[str, object]:
    text = " ".join([inc.title, *(u.body for u in inc.updates)])
    first = inc.updates[0] if inc.updates else None
    return {
        "id": inc.id,
        "service_id": inc.service_id,
        "category": service.category.value,
        "curated": service.curated,
        "title": inc.title,
        "impact": inc.impact.value,
        "impact_rank": inc.impact.rank,
        "created_at": inc.created_at,
        "start": inc.start,
        "resolved_at": inc.resolved_at,
        "duration_min": inc.duration_minutes(),
        "n_updates": len(inc.updates),
        "first_status": first.status.value if first else None,
        "first_body": first.body if first else "",
        "identified_at": _milestone(inc, Phase.IDENTIFIED),
        "monitoring_at": _milestone(inc, Phase.MONITORING),
        "retroactive": inc.retroactive,
        "n_components": len(inc.components),
        "components": inc.components[:20],
        "regions": inc.regions[:20],
        "mentions": mentioned_providers(inc.service_id, text),
        "generic_upstream": bool(_GENERIC_UPSTREAM.search(text)),
        "source": source,
        "url": inc.url,
    }


def update_rows(inc: Incident) -> Iterable[dict[str, object]]:
    for seq, u in enumerate(inc.updates):
        yield {
            "incident_id": inc.id,
            "seq": seq,
            "at": u.at,
            "status": u.status.value,
            "n_components": len(u.components),
            "body_len": len(u.body),
            "body": u.body[:2000],
            "severity_so_far": 0,  # filled from component states when the source has them
        }


def _component_severity(raw: dict) -> list[int]:
    """Worst component state announced by each update so far, from Statuspage's affected_components."""
    rank = {"degraded_performance": 1, "partial_outage": 2, "major_outage": 3}
    out, worst = [], 0
    for u in sorted(raw.get("incident_updates") or [], key=lambda x: x.get("display_at") or x.get("created_at") or ""):
        for c in u.get("affected_components") or []:
            worst = max(worst, rank.get(c.get("new_status") or "", 0))
        out.append(worst)
    return out


def build(raw: Path, services: list[Service]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Build (incidents, updates) from every source in the raw cache."""
    cache = RawCache(raw)
    incidents: dict[str, dict[str, object]] = {}
    updates: dict[str, list[dict[str, object]]] = {}

    def add(inc: Incident, svc: Service, source: str, severity: list[int] | None = None) -> None:
        if not inc.title and not inc.updates:
            return
        prev = incidents.get(inc.id)
        # Prefer the richest record of an incident: a full timeline beats an index line.
        if prev and int(prev["n_updates"]) > len(inc.updates):  # type: ignore[call-overload]
            return
        incidents[inc.id] = incident_row(inc, svc, source)
        rows = list(update_rows(inc))
        if severity and len(severity) == len(rows):
            for r, s in zip(rows, severity, strict=True):
                r["severity_so_far"] = s
        updates[inc.id] = rows

    for svc in services:
        if svc.adapter == "statuspage" and svc.history:
            for e in cache.history_entries(svc.id):
                if e.start is None:
                    continue
                inc = Incident(
                    service_id=svc.id,
                    source_id=e.code,
                    title=e.title,
                    impact=e.impact,
                    status=Phase.RESOLVED if e.end else Phase.INVESTIGATING,
                    created_at=e.start,
                    resolved_at=e.end,
                    url=f"{svc.base_url}/incidents/{e.code}",
                )
                add(inc, svc, "index")
            for p in sorted((raw / "incidents" / svc.id).glob("*.json")):
                data = json.loads(p.read_text())
                try:
                    inc = statuspage.parse_incident(svc.id, data, svc.base_url)
                except (AssertionError, ValueError, KeyError):
                    continue
                add(inc, svc, "detail", _component_severity(data))
        cache.migrate(svc.id)
        feed = cache.feed_path(svc.id)
        if feed.exists():
            for item in json.loads(feed.read_text()).get("items", {}).values():
                inc = _parse_item(svc, item)
                if inc is not None:
                    add(inc, svc, "feed")

    inc_df = pl.DataFrame(list(incidents.values()), infer_schema_length=None)
    upd_df = pl.DataFrame([r for rows in updates.values() for r in rows], infer_schema_length=None)
    return inc_df, upd_df


def _parse_item(svc: Service, item: dict) -> Incident | None:
    """One incident from a provider's merged feed file, parsed by the provider's own adapter."""
    try:
        if svc.adapter == "aws":
            return vendors.parse_aws_event(svc.id, item)
        if svc.adapter == "gcp":
            return vendors.parse_gcp(svc.id, item)
        if svc.adapter == "slack":
            return None if item.get("type") == "notice" else vendors.parse_slack(svc.id, item)
        if svc.adapter == "heroku":
            return vendors.parse_heroku(svc.id, item)
        if svc.adapter == "statuspage":
            return statuspage.parse_incident(svc.id, item, svc.base_url)
    except (AssertionError, ValueError, KeyError, TypeError):
        return None
    return None


def to_utc(df: pl.DataFrame) -> pl.DataFrame:
    cols = [c for c, t in df.schema.items() if isinstance(t, pl.Datetime)]
    return df.with_columns([pl.col(c).dt.convert_time_zone("UTC") for c in cols])


def write(inc: pl.DataFrame, upd: pl.DataFrame, out: Path, *, public: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    keep = inc.drop("first_body") if public else inc
    keep.write_parquet(out / "incidents.parquet", compression="zstd")
    (upd.drop("body") if public and "body" in upd.columns else upd).write_parquet(
        out / "updates.parquet", compression="zstd"
    )
    (out / "dataset.json").write_text(
        json.dumps(
            {
                "built_at": datetime.now(UTC).isoformat(),
                "incidents": inc.height,
                "updates": upd.height,
                "services": inc["service_id"].n_unique() if inc.height else 0,
                "first": str(inc["start"].min()) if inc.height else None,
                "last": str(inc["start"].max()) if inc.height else None,
                "public": public,
            },
            indent=1,
        )
    )


IMPACTS = [i.value for i in Impact]
