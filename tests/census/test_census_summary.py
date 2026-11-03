from datetime import UTC, datetime
from typing import Any

import orjson
import pytest

from mercalli.census.models import DomainRecord
from mercalli.census.summary import hhi, shares, summarize

NOW = datetime(2026, 11, 3, tzinfo=UTC)


def rec(domain: str, rank: int | None = None, *, resolved: bool = True, **fields: Any) -> DomainRecord:
    return DomainRecord(
        domain=domain,
        rank=rank,
        source="tranco" if rank is not None else "catalog",
        fetched_at=NOW,
        ips=["192.0.2.1"] if resolved else [],
        **fields,
    )


def test_hhi_scale_and_pseudo_buckets() -> None:
    assert hhi({"a": 1}, 1) == 10000.0
    assert hhi({"a": 1, "b": 1}, 2) == 5000.0
    assert hhi({"a": 3, "b": 1}, 4) == 6250.0
    # "other" pools unrelated operators, so it counts as many tiny firms and adds nothing.
    assert hhi({"a": 1, "other": 1}, 2) == 2500.0
    assert hhi({}, 0) is None


def test_shares_denominators_ordering_and_truncation() -> None:
    values = ["b", "a", "a", "c", "b", None, None, "other"]
    table = shares(values, resolved=len(values), top=2)
    assert table["determinable"] == 6
    assert table["resolved"] == 8
    assert table["distinct"] == 4
    assert [row["id"] for row in table["top"]] == ["a", "b"]  # equal counts sort by id
    first = table["top"][0]
    assert first["count"] == 2
    assert first["pct_of_determinable"] == pytest.approx(33.33)
    assert first["pct_of_resolved"] == 25.0
    # a and b hold a third each, c a sixth, and "other" is left out: 2 * 33.3^2 + 16.7^2.
    assert table["hhi"] == 2500.0


def test_shares_with_nothing_determinable() -> None:
    table = shares([None, None], resolved=2)
    assert (table["determinable"], table["hhi"], table["top"]) == (0, None, [])


@pytest.fixture
def records() -> list[DomainRecord]:
    return [
        rec(
            "a.com", 1, dns_provider="cloudflare", dns_providers=["cloudflare"], edge_provider="cloudflare",
            hosting_provider="cloudflare", tls_issuer="Google Trust Services", mx_provider="google-workspace",
            http_status=200,
        ),
        rec(
            "b.com", 50, dns_provider="aws", dns_providers=["aws", "ns1"], hosting_provider="aws",
            hosting_region="us-east-1", tls_issuer="Amazon", mx_provider="google-workspace", http_status=301,
            asns=[16509], asn_orgs=["AMAZON-02"],
        ),
        rec(
            "c.com", 500, dns_provider="aws", dns_providers=["aws"], edge_provider="aws", hosting_provider="aws",
            tls_issuer="Amazon", mx_provider="none", http_status=403, asns=[16509], asn_orgs=["AMAZON-02"],
        ),
        rec(
            "d.com", 5000, dns_provider="self", dns_providers=["self"], hosting_provider="other",
            mx_provider="other", http_status=500, asns=[64512], asn_orgs=["EXAMPLE-NET"], ns=["ns1.d.com"],
            mx_hosts=["mx.d.com"],
        ),
        rec("e.com", 9000, resolved=False, dns_provider="cloudflare", error="dns: no address (nodata)"),
        rec(
            "status.example.io", None, dns_provider="aws", dns_providers=["aws"], hosting_provider="aws",
            hosting_region="eu-west-1",
        ),
    ]  # fmt: skip


def _row(table: dict[str, Any], key: str) -> dict[str, Any]:
    return next(r for r in table["top"] if r["id"] == key)


def test_counts(records: list[DomainRecord]) -> None:
    counts = summarize(records, dataset_info={})["counts"]
    assert counts["total"] == 6
    assert counts["by_source"] == {"tranco": 5, "catalog": 1}
    assert counts["resolved"] == 5
    assert counts["http_response"] == 4
    assert counts["http_ok"] == 2
    assert counts["http_status_classes"] == {"2xx": 1, "3xx": 1, "4xx": 1, "5xx": 1}
    assert counts["tls_issuer_found"] == 3


def test_field_shares_use_resolved_domains_only(records: list[DomainRecord]) -> None:
    fields = summarize(records, dataset_info={})["fields"]
    dns = fields["dns_provider"]
    # e.com has a DNS provider but no address, so it is outside every share table.
    assert (dns["resolved"], dns["determinable"]) == (5, 5)
    assert _row(dns, "aws")["count"] == 3
    assert _row(dns, "aws")["pct_of_resolved"] == 60.0
    assert _row(dns, "cloudflare")["count"] == 1

    edge = fields["edge_provider"]
    assert edge["determinable"] == 5
    assert _row(edge, "none")["count"] == 3

    tls = fields["tls_issuer"]
    assert tls["determinable"] == 3
    assert _row(tls, "Amazon")["pct_of_determinable"] == pytest.approx(66.67)
    assert _row(tls, "Amazon")["pct_of_resolved"] == 40.0

    region = fields["hosting_region"]
    assert region["determinable"] == 2
    assert {r["id"] for r in region["top"]} == {"aws/us-east-1", "aws/eu-west-1"}
    assert _row(region, "aws/us-east-1")["name"] == "Amazon Web Services us-east-1"

    no_edge = fields["hosting_provider_without_edge"]
    assert no_edge["resolved"] == 3
    assert _row(no_edge, "aws")["count"] == 2


def test_single_provider_and_multi_provider_dns(records: list[DomainRecord]) -> None:
    single = summarize(records, dataset_info={})["single_provider"]
    assert single["dns_and_edge"]["count"] == 2  # a.com cloudflare, c.com aws
    assert single["dns_and_edge"]["comparable"] == 2
    assert single["dns_and_hosting"]["count"] == 4
    assert single["dns_and_hosting"]["comparable"] == 4  # d.com is self-hosted DNS, never comparable
    assert single["dns_and_front_door"]["count"] == 4
    assert single["dns_edge_and_hosting"]["count"] == 2
    assert single["dns_and_hosting"]["by_provider"][0] == {"id": "aws", "name": "Amazon Web Services", "count": 3}
    assert single["dns_multi_provider"] == {"count": 1, "pct_of_resolved": 20.0}


def test_aws_regions(records: list[DomainRecord]) -> None:
    aws = summarize(records, dataset_info={})["aws_regions"]
    assert (aws["aws_hosted"], aws["with_region"], aws["without_region"]) == (3, 2, 1)
    assert [r["region"] for r in aws["regions"]] == ["eu-west-1", "us-east-1"]
    assert aws["regions"][0]["pct_of_aws_with_region"] == 50.0


def test_rank_buckets_and_catalog_population(records: list[DomainRecord]) -> None:
    summary = summarize(records, dataset_info={})
    buckets = summary["rank_buckets"]
    assert buckets["top_100"]["counts"]["total"] == 2
    assert buckets["top_1k"]["counts"]["total"] == 3
    assert buckets["top_10k"]["counts"]["total"] == 5
    assert buckets["top_10k"]["counts"]["resolved"] == 4
    assert summary["catalog_only"]["counts"]["total"] == 1


def test_unclassified_and_top_asns(records: list[DomainRecord]) -> None:
    summary = summarize(records, dataset_info={})
    assert summary["unclassified"]["dns_ns_domains"] == []  # self-hosted is classified
    assert summary["unclassified"]["mx_domains"] == [{"key": "d.com", "count": 1}]
    assert summary["unclassified"]["hosting_asns"] == [{"key": "AS64512 EXAMPLE-NET", "count": 1}]
    assert summary["top_asns"][0] == {"asn": 16509, "org": "AMAZON-02", "count": 2, "pct_of_resolved": 40.0}


def test_edges_answering_from_unlisted_networks() -> None:
    rows = [
        rec("a.com", 1, edge_provider="akamai", hosting_provider="other", asns=[9498], asn_orgs=["BBIL-AP"]),
        rec("b.com", 2, edge_provider="akamai", hosting_provider="akamai", asns=[20940], asn_orgs=["AKAMAI"]),
        rec("c.com", 3, hosting_provider="other", asns=[64512], asn_orgs=["X"]),
    ]
    out = summarize(rows, dataset_info={})["edges_in_other_networks"]
    assert (out["count"], out["pct_of_resolved"]) == (1, pytest.approx(33.33))
    assert out["by_edge"] == [{"id": "akamai", "count": 1}]
    assert out["by_network"] == [{"key": "AS9498 BBIL-AP", "count": 1}]


def test_metadata_and_json_round_trip(records: list[DomainRecord]) -> None:
    datasets = {
        "aws": {"published": "2026-11-03-09-27-05", "fetched_at": "2026-11-03T13:26:22+00:00"},
        "fastly": {"published": None, "last_modified": None, "fetched_at": "2026-11-03T13:26:22+00:00"},
    }
    summary = summarize(records, dataset_info=datasets, vantage={"country": "IN"}, run={"wall_seconds": 1.5})
    assert summary["dataset_dates"] == {"aws": "2026-11-03-09-27-05", "fastly": "2026-11-03T13:26:22+00:00"}
    assert summary["vantage"] == {"country": "IN"}
    assert summary["run"] == {"wall_seconds": 1.5}
    assert orjson.loads(orjson.dumps(summary))["counts"]["total"] == 6
