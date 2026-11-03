"""Write census results: Parquet for analysis, JSON Lines for grep, JSON for the summary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import orjson

from mercalli.census.models import DomainRecord


def parquet_schema() -> Any:
    """The Parquet schema, one column per DomainRecord field."""
    # pyarrow belongs to the optional ml extra; importing it here keeps the census importable
    # (and runnable as a library) without it.
    import pyarrow as pa

    strings = pa.list_(pa.string())
    return pa.schema(
        [
            ("domain", pa.string()),
            ("rank", pa.int64()),
            ("source", pa.string()),
            ("fetched_at", pa.timestamp("us", tz="UTC")),
            ("ns", strings),
            ("dns_provider", pa.string()),
            ("dns_providers", strings),
            ("host", pa.string()),
            ("cname_chain", strings),
            ("ips", strings),
            ("asns", pa.list_(pa.int64())),
            ("asn_orgs", strings),
            ("edge_provider", pa.string()),
            ("hosting_provider", pa.string()),
            ("hosting_region", pa.string()),
            ("tls_issuer", pa.string()),
            ("mx_hosts", strings),
            ("mx_provider", pa.string()),
            ("http_status", pa.int32()),
            ("server_header", pa.string()),
            ("final_host", pa.string()),
            ("evidence", pa.map_(pa.string(), strings)),
            ("error", pa.string()),
        ]
    )


def write_outputs(records: Sequence[DomainRecord], summary: Mapping[str, Any], out_dir: Path) -> None:
    """Write domains.parquet, domains.jsonl and summary.json into `out_dir`."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [r.model_dump() for r in records]
    for row in rows:
        # Arrow builds map columns from (key, value) pairs.
        row["evidence"] = list(row["evidence"].items())
    table = pa.Table.from_pylist(rows, schema=parquet_schema())
    pq.write_table(table, out_dir / "domains.parquet", compression="zstd")
    with (out_dir / "domains.jsonl").open("wb") as fh:
        for r in records:
            fh.write(orjson.dumps(r.model_dump(mode="json")) + b"\n")
    (out_dir / "summary.json").write_bytes(orjson.dumps(summary, option=orjson.OPT_INDENT_2) + b"\n")
