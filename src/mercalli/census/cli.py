"""Command line entry point: `python -m mercalli.census --top 10000 --extra-catalog --out DIR`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import platform
import sys
import time
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from mercalli.catalog import catalog_dir, load_seed, load_services
from mercalli.census.net import DEFAULT_NAMESERVERS
from mercalli.census.output import write_outputs
from mercalli.census.refdata import ReferenceData, load_reference_data
from mercalli.census.run import census
from mercalli.census.summary import summarize
from mercalli.discover import load_tranco
from mercalli.http import USER_AGENT, PoliteClient

try:
    import resource
except ImportError:  # Windows has no resource module and no per-process descriptor limit to raise
    resource = None  # type: ignore[assignment]

log = logging.getLogger("mercalli.census")

TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
DEFAULT_CACHE = Path("~/.cache/mercalli/census")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m mercalli.census",
        description="Map which DNS, edge, hosting, certificate and email providers domains depend on.",
    )
    p.add_argument("--top", type=int, default=10_000, help="survey the top N Tranco domains (0 for none)")
    p.add_argument("--extra-catalog", action="store_true", help="also survey every domain in the catalog")
    p.add_argument("--out", type=Path, required=True, help="directory for domains.parquet, domains.jsonl, summary.json")
    p.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE, help="where reference data is cached")
    p.add_argument("--tranco", type=Path, help="Tranco top-1m zip (default: <cache-dir>/tranco-top-1m.csv.zip)")
    p.add_argument("--catalog", type=Path, help="services.yaml or seed.yaml (default: services.yaml, else seed.yaml)")
    p.add_argument("--refresh", action="store_true", help="download every reference dataset again")
    p.add_argument(
        "--record-network",
        action="store_true",
        help="record the vantage network's operator and location in the summary (for shared CI runners)",
    )
    p.add_argument("--dns-concurrency", type=int, default=200)
    p.add_argument("--http-concurrency", type=int, default=64)
    p.add_argument(
        "--nameservers",
        default=",".join(DEFAULT_NAMESERVERS),
        help="comma separated recursive resolvers, rotated per query",
    )
    return p


def catalog_domains(path: Path | None = None) -> tuple[list[str], Path]:
    """Every domain the catalog names, from the verified services.yaml when it has entries."""
    if path is None:
        verified = catalog_dir() / "services.yaml"
        path = verified if load_services(verified) else catalog_dir() / "seed.yaml"
    entries = load_services(path) if path.name == "services.yaml" else load_seed(path)
    domains = {d.strip().lower().rstrip(".") for e in entries for d in e.domains}
    return sorted(d for d in domains if d), path


def _tranco_info(path: Path, top: int) -> dict[str, Any]:
    """The Tranco zip carries no list id, so the date of its CSV member stands in for one."""
    info: dict[str, Any] = {"path": str(path), "top": top}
    if path.exists():
        with zipfile.ZipFile(path) as zf:
            y, m, d, *_ = zf.infolist()[0].date_time
        info["published"] = f"{y:04d}-{m:02d}-{d:02d}"
    return info


async def describe_vantage(
    client: PoliteClient, reference: ReferenceData, nameservers: Sequence[str], *, record_network: bool = False
) -> dict[str, Any]:
    """Where the census ran from. The public address itself is never recorded.

    The network's country, operator and nearest Cloudflare site are only recorded with
    `record_network`, which the scheduled CI run sets. On a personal connection they would publish
    the operator's internet provider and rough location along with the results.
    """
    info: dict[str, Any] = {
        "description": (
            "One vantage point. Geo-aware DNS and anycast mean another location can see other "
            "addresses, regions and sometimes other providers for the same domain."
        ),
        "dns_resolvers": list(nameservers),
        "dns_note": (
            "Census DNS queries go to these public resolvers, rotated per query. The census sends no "
            "client subnet option; Google Public DNS adds a truncated one, Cloudflare and Quad9 do not, "
            "so geo-aware answers follow the resolver's nearest site."
        ),
        "http_note": (
            "HTTP requests and TLS handshakes connect to the first IPv4 address the census resolved "
            "(IPv6 when there is none), naming the host in SNI and the Host header. Redirect targets "
            "are resolved through the same resolvers."
        ),
        "user_agent": USER_AGENT,
    }
    if not record_network:
        return info
    info["platform"] = platform.platform()
    try:
        resp = await client.get(TRACE_URL)
        fields = dict(line.split("=", 1) for line in resp.text.splitlines() if "=" in line)
        facts = reference.lookup(fields["ip"])
    except (httpx.HTTPError, KeyError, ValueError, OSError) as exc:
        info["network_error"] = repr(exc)
        return info
    info.update(
        country=fields.get("loc"),
        network_asn=facts.asn,
        network_org=facts.asn_org,
        cloudflare_colo=fields.get("colo"),
        ip_version=facts.version,
    )
    return info


def _raise_open_file_limit(needed: int) -> None:
    # Hundreds of DNS sockets plus HTTP and TLS connections exceed macOS's default of 256.
    if resource is None:
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = needed if hard == resource.RLIM_INFINITY else min(needed, hard)
    if soft != resource.RLIM_INFINITY and soft < want:
        resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))


async def _run(args: argparse.Namespace) -> int:
    cache_dir: Path = args.cache_dir.expanduser()
    tranco_path: Path = (args.tranco or cache_dir / "tranco-top-1m.csv.zip").expanduser()
    nameservers = [ns.strip() for ns in args.nameservers.split(",") if ns.strip()]

    async with PoliteClient(concurrency=8, timeout=60.0) as client:
        tranco = await load_tranco(client, args.top, tranco_path) if args.top > 0 else []
        reference = await load_reference_data(cache_dir, refresh=args.refresh, client=client)
        vantage = await describe_vantage(client, reference, nameservers, record_network=args.record_network)

    domains: list[tuple[int | None, str]] = list(tranco)
    catalog_path: Path | None = None
    if args.extra_catalog:
        listed = {d for _, d in tranco}
        extra, catalog_path = catalog_domains(args.catalog)
        domains += [(None, d) for d in extra if d not in listed]
    log.info("surveying %d domains (%d Tranco, %d catalog only)", len(domains), len(tranco), len(domains) - len(tranco))

    started_at = datetime.now(UTC)
    clock = time.monotonic()
    records = await census(
        domains,
        cache_dir=cache_dir,
        refresh=args.refresh,
        dns_concurrency=args.dns_concurrency,
        http_concurrency=args.http_concurrency,
        nameservers=nameservers,
        reference=reference,
    )
    wall = round(time.monotonic() - clock, 1)
    datasets = {**reference.datasets, "tranco": _tranco_info(tranco_path, args.top)}
    run = {
        "started_at": started_at.isoformat(timespec="seconds"),
        "wall_seconds": wall,
        "domains": len(domains),
        "tranco_top": args.top,
        "catalog_file": str(catalog_path) if catalog_path else None,
        "dns_concurrency": args.dns_concurrency,
        "http_concurrency": args.http_concurrency,
    }
    summary = summarize(records, dataset_info=datasets, vantage=vantage, run=run)
    out_dir: Path = args.out.expanduser()
    await asyncio.to_thread(write_outputs, records, summary, out_dir)
    log.info("wrote %d records to %s in %.1f s of probing", len(records), out_dir, wall)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _raise_open_file_limit(args.dns_concurrency + 4 * args.http_concurrency + 256)
    return asyncio.run(_run(args))
