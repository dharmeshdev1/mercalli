"""Aggregate census records into shares, concentration indices and per-rank breakdowns.

All shares are computed over resolved domains (the probed host had at least one address), which
keeps every table on the same population. Each share is given twice:

* `pct_of_determinable`: over resolved domains where the field could be decided at all, for
  example where a TLS handshake completed for `tls_issuer`.
* `pct_of_resolved`: over every resolved domain, so undecided domains count against the share.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from mercalli.census.classify import registrable
from mercalli.census.models import DomainRecord
from mercalli.census.providers import NONE, OTHER, PSEUDO, display_name

TOP = 25
BUCKET_TOP = 10
RANK_BUCKETS = (("top_100", 100), ("top_1k", 1_000), ("top_10k", 10_000))

Field = Callable[[DomainRecord], str | None]


def _pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 2) if whole else None


def hhi(counts: Mapping[str, int], total: int) -> float | None:
    """Herfindahl-Hirschman index on the 0 to 10,000 scale, shares taken over `total`.

    Buckets that pool many unrelated operators (other, self, none) are treated as atomistic and
    contribute nothing, so the result is a lower bound on true concentration.
    """
    if not total:
        return None
    return round(sum((100.0 * c / total) ** 2 for k, c in counts.items() if k not in PSEUDO), 1)


def shares(
    values: Sequence[str | None], resolved: int, *, top: int = TOP, label: Callable[[str], str] = display_name
) -> dict[str, Any]:
    """Share table for one field. `values` holds one entry per resolved domain, None if undecided."""
    counts = Counter(v for v in values if v is not None)
    determinable = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "determinable": determinable,
        "resolved": resolved,
        "distinct": len(counts),
        "hhi": hhi(counts, determinable),
        "top": [
            {
                "id": key,
                "name": label(key),
                "count": count,
                "pct_of_determinable": _pct(count, determinable),
                "pct_of_resolved": _pct(count, resolved),
            }
            for key, count in ordered[:top]
        ],
    }


def _region_key(r: DomainRecord) -> str | None:
    return f"{r.hosting_provider}/{r.hosting_region}" if r.hosting_provider and r.hosting_region else None


_FIELDS: dict[str, Field] = {
    "dns_provider": lambda r: r.dns_provider,
    # Every resolved domain has an edge answer: a provider, or none detected.
    "edge_provider": lambda r: r.edge_provider or NONE,
    "hosting_provider": lambda r: r.hosting_provider,
    "hosting_region": _region_key,
    "tls_issuer": lambda r: r.tls_issuer,
    "mx_provider": lambda r: r.mx_provider,
}


def _region_label(key: str) -> str:
    provider, _, region = key.partition("/")
    return f"{display_name(provider)} {region}"


_LABELS: dict[str, Callable[[str], str]] = {"hosting_region": _region_label}


def _counts(records: Sequence[DomainRecord]) -> dict[str, Any]:
    statuses = Counter(r.http_status // 100 for r in records if r.http_status is not None)
    return {
        "total": len(records),
        "by_source": dict(Counter(r.source for r in records)),
        "zone_found": sum(1 for r in records if r.ns),
        "resolved": sum(1 for r in records if r.resolved),
        "http_response": sum(1 for r in records if r.http_status is not None),
        "http_ok": sum(1 for r in records if r.http_status is not None and r.http_status < 400),
        "http_status_classes": {f"{k}xx": v for k, v in sorted(statuses.items())},
        "tls_issuer_found": sum(1 for r in records if r.tls_issuer),
        "redirected_off_domain": sum(1 for r in records if r.final_host),
    }


def _population(records: Sequence[DomainRecord], top: int) -> dict[str, Any]:
    resolved = [r for r in records if r.resolved]
    n = len(resolved)
    fields = {
        name: shares([get(r) for r in resolved], n, top=top, label=_LABELS.get(name, display_name))
        for name, get in _FIELDS.items()
    }
    # The closest available proxy for where origins run: domains with no edge in front, whose
    # answering addresses are therefore the origin's own.
    direct = [r for r in resolved if r.edge_provider is None]
    fields["hosting_provider_without_edge"] = shares([r.hosting_provider for r in direct], len(direct), top=top)
    return {"counts": _counts(records), "fields": fields}


def _named(value: str | None) -> bool:
    return value is not None and value not in PSEUDO


def _same_company(resolved: Sequence[DomainRecord]) -> dict[str, Any]:
    """How often the DNS provider is also the company that answers HTTP or owns the addresses."""
    comparisons: dict[str, Field] = {
        "dns_and_edge": lambda r: r.edge_provider,
        "dns_and_hosting": lambda r: r.hosting_provider,
        # The company the browser actually talks to: the edge when there is one, else the host.
        "dns_and_front_door": lambda r: r.edge_provider or r.hosting_provider,
    }
    out: dict[str, Any] = {}
    for name, other in comparisons.items():
        comparable = [r for r in resolved if _named(r.dns_provider) and _named(other(r))]
        same = [r for r in comparable if r.dns_provider == other(r)]
        out[name] = _same_block(same, len(comparable), len(resolved))
    comparable = [
        r for r in resolved if _named(r.dns_provider) and _named(r.edge_provider) and _named(r.hosting_provider)
    ]
    same = [r for r in comparable if r.dns_provider == r.edge_provider == r.hosting_provider]
    out["dns_edge_and_hosting"] = _same_block(same, len(comparable), len(resolved))
    multi = [r for r in resolved if len([p for p in r.dns_providers if _named(p)]) > 1]
    out["dns_multi_provider"] = {"count": len(multi), "pct_of_resolved": _pct(len(multi), len(resolved))}
    return out


def _same_block(same: Sequence[DomainRecord], comparable: int, resolved: int) -> dict[str, Any]:
    by_provider = Counter(r.dns_provider for r in same if r.dns_provider)
    return {
        "count": len(same),
        "comparable": comparable,
        "pct_of_comparable": _pct(len(same), comparable),
        "pct_of_resolved": _pct(len(same), resolved),
        "by_provider": [
            {"id": k, "name": display_name(k), "count": c}
            for k, c in sorted(by_provider.items(), key=lambda kv: (-kv[1], kv[0]))[:BUCKET_TOP]
        ],
    }


def _aws_regions(resolved: Sequence[DomainRecord]) -> dict[str, Any]:
    aws = [r for r in resolved if r.hosting_provider == "aws"]
    regional = Counter(r.hosting_region for r in aws if r.hosting_region)
    with_region = sum(regional.values())
    return {
        "aws_hosted": len(aws),
        "with_region": with_region,
        "without_region": len(aws) - with_region,
        "regions": [
            {
                "region": region,
                "count": count,
                "pct_of_aws_with_region": _pct(count, with_region),
                "pct_of_resolved": _pct(count, len(resolved)),
            }
            for region, count in sorted(regional.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }


def _top_asns(resolved: Sequence[DomainRecord]) -> list[dict[str, Any]]:
    """The network of each domain's first answering address, independent of the taxonomy."""
    firsts = [(r.asns[0], r.asn_orgs[0]) for r in resolved if r.asns]
    counts = Counter(asn for asn, _ in firsts)
    orgs = dict(firsts)
    return [
        {"asn": asn, "org": orgs[asn], "count": c, "pct_of_resolved": _pct(c, len(resolved))}
        for asn, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP]
    ]


def _edges_in_other_networks(resolved: Sequence[DomainRecord]) -> dict[str, Any]:
    """A known edge answering from addresses no provider claims.

    Usually CDN caches installed inside the vantage point's ISP, which announces their addresses.
    Their hosting reads "other" although the CDN runs them, an effect of where the census ran.
    """
    rows = [r for r in resolved if _named(r.edge_provider) and r.hosting_provider == OTHER]
    by_edge = Counter(r.edge_provider for r in rows if r.edge_provider)
    by_network = Counter(f"AS{r.asns[0]} {r.asn_orgs[0]}" for r in rows if r.asns)
    return {
        "count": len(rows),
        "pct_of_resolved": _pct(len(rows), len(resolved)),
        "by_edge": [{"id": k, "count": c} for k, c in by_edge.most_common(BUCKET_TOP)],
        "by_network": [{"key": k, "count": c} for k, c in by_network.most_common(BUCKET_TOP)],
    }


def _unclassified(resolved: Sequence[DomainRecord]) -> dict[str, Any]:
    """What fell into "other", grouped, to show where the taxonomy needs new entries."""

    def table(values: Iterable[str]) -> list[dict[str, Any]]:
        counts = Counter(values)
        return [{"key": k, "count": c} for k, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:BUCKET_TOP]]

    return {
        "dns_ns_domains": table(
            d for r in resolved if r.dns_provider == OTHER for d in sorted({registrable(h) for h in r.ns})
        ),
        "mx_domains": table(registrable(r.mx_hosts[0]) for r in resolved if r.mx_provider == OTHER and r.mx_hosts),
        "hosting_asns": table(
            f"AS{r.asns[0]} {r.asn_orgs[0]}" for r in resolved if r.hosting_provider == OTHER and r.asns
        ),
    }


def summarize(
    records: Sequence[DomainRecord],
    *,
    dataset_info: Mapping[str, Mapping[str, Any]],
    vantage: Mapping[str, Any] | None = None,
    run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything summary.json holds. JSON serializable, computed only from `records` and arguments."""
    resolved = [r for r in records if r.resolved]
    overall = _population(records, TOP)
    buckets = {
        name: _population([r for r in records if r.rank is not None and r.rank <= limit], BUCKET_TOP)
        for name, limit in RANK_BUCKETS
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "vantage": dict(vantage or {}),
        "run": dict(run or {}),
        "dataset_dates": {
            name: info.get("published") or info.get("last_modified") or info.get("fetched_at")
            for name, info in dataset_info.items()
        },
        "datasets": {name: dict(info) for name, info in dataset_info.items()},
        "population_note": (
            "Shares are over resolved domains. pct_of_determinable divides by resolved domains where "
            "the field could be decided; pct_of_resolved divides by all resolved domains."
        ),
        "counts": overall["counts"],
        "fields": overall["fields"],
        "single_provider": _same_company(resolved),
        "aws_regions": _aws_regions(resolved),
        "top_asns": _top_asns(resolved),
        "unclassified": _unclassified(resolved),
        "edges_in_other_networks": _edges_in_other_networks(resolved),
        "rank_buckets": buckets,
        "catalog_only": _population([r for r in records if r.source == "catalog"], BUCKET_TOP),
    }
