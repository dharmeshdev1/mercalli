from datetime import UTC, datetime
from pathlib import Path

import orjson
import pyarrow.parquet as pq

from mercalli.census.models import DomainRecord
from mercalli.census.output import parquet_schema, write_outputs


def test_parquet_schema_matches_the_record_model() -> None:
    assert parquet_schema().names == list(DomainRecord.model_fields)


def test_write_outputs_round_trip(tmp_path: Path) -> None:
    records = [
        DomainRecord(
            domain="example.com",
            rank=7,
            fetched_at=datetime(2026, 11, 3, 13, 0, tzinfo=UTC),
            ns=["kate.ns.cloudflare.com"],
            dns_provider="cloudflare",
            dns_providers=["cloudflare"],
            host="www.example.com",
            ips=["104.16.1.1", "2606:4700::1"],
            asns=[13335],
            asn_orgs=["CLOUDFLARENET - Cloudflare, Inc."],
            edge_provider="cloudflare",
            evidence={"edge": ["range:cloudflare/global", "header:cf-ray"], "dns": ["ns:*.ns.cloudflare.com"]},
        ),
        DomainRecord(
            domain="api.example.io", source="catalog", fetched_at=datetime(2026, 11, 3, tzinfo=UTC), error="x"
        ),
    ]
    out = tmp_path / "run"
    write_outputs(records, {"counts": {"total": 2}}, out)

    table = pq.read_table(out / "domains.parquet")
    assert table.num_rows == 2
    rows = table.to_pylist()
    assert rows[0]["evidence"] == [
        ("edge", ["range:cloudflare/global", "header:cf-ray"]),
        ("dns", ["ns:*.ns.cloudflare.com"]),
    ]
    assert rows[1]["rank"] is None
    assert rows[1]["source"] == "catalog"

    lines = (out / "domains.jsonl").read_bytes().splitlines()
    assert [orjson.loads(line)["domain"] for line in lines] == ["example.com", "api.example.io"]
    assert DomainRecord.model_validate_json(lines[0]) == records[0]
    assert orjson.loads((out / "summary.json").read_bytes()) == {"counts": {"total": 2}}
