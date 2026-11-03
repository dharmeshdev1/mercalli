"""Published IP ranges and an IP to ASN map, downloaded once and cached on disk.

Every dataset is optional. A dataset that cannot be downloaded falls back to a stale cached copy,
and a dataset with neither is skipped and reported as unavailable: the census then attributes
those addresses by ASN alone, which loses regions but not providers.
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import io
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import orjson

from mercalli.census.iprange import IpFacts, RangeInfo, RangeTable, parse_ip
from mercalli.http import PoliteClient

log = logging.getLogger(__name__)

AWS_URL = "https://ip-ranges.amazonaws.com/ip-ranges.json"
GCP_URL = "https://www.gstatic.com/ipranges/cloud.json"
GOOGLE_URL = "https://www.gstatic.com/ipranges/goog.json"
AZURE_DETAILS_URL = "https://www.microsoft.com/en-us/download/details.aspx?id=56519"
ORACLE_URL = "https://docs.oracle.com/iaas/tools/public_ip_ranges.json"
CLOUDFLARE_V4_URL = "https://www.cloudflare.com/ips-v4"
CLOUDFLARE_V6_URL = "https://www.cloudflare.com/ips-v6"
FASTLY_URL = "https://api.fastly.com/public-ip-list"
DIGITALOCEAN_URL = "https://digitalocean.com/geo/google.csv"
IPTOASN_URL = "https://iptoasn.com/data/ip2asn-combined.tsv.gz"

_AZURE_LINK = re.compile(r"https://download\.microsoft\.com/[^\"'\s<>]+/ServiceTags_Public_(\d{8})\.json")
# Services that every address of a cloud carries; a more specific service name is more useful.
_GENERIC_SERVICES = frozenset({"AMAZON", "AzureCloud"})

type UrlSource = str | Callable[[PoliteClient], Awaitable[str]]
type Parsed = tuple[list[tuple[str, RangeInfo]], str | None]


class ReferenceData:
    """IP range tables in lookup order, plus the IP to ASN map."""

    def __init__(
        self,
        tables: Sequence[tuple[str, RangeTable[RangeInfo]]],
        asn: RangeTable[int] | None,
        asn_orgs: dict[int, str],
        datasets: dict[str, dict[str, Any]],
    ) -> None:
        self.tables = list(tables)
        self.asn = asn
        self.asn_orgs = asn_orgs
        self.datasets = datasets

    def lookup(self, ip: str) -> IpFacts:
        version, value = parse_ip(ip)
        info: RangeInfo | None = None
        for _, table in self.tables:
            info = table.lookup(version, value)
            if info is not None:
                break
        asn = self.asn.lookup(version, value) if self.asn is not None else None
        org = self.asn_orgs.get(asn) if asn is not None else None
        return IpFacts(ip=ip, version=version, value=value, asn=asn, asn_org=org, range=info)


def merge_info(a: RangeInfo, b: RangeInfo) -> RangeInfo:
    """Combine two entries for the same prefix: keep any region, the edge flag and a specific service."""
    services = [
        i.service for i in sorted((a, b), key=lambda i: not i.edge) if i.service and i.service not in _GENERIC_SERVICES
    ]
    return RangeInfo(
        provider=a.provider,
        dataset=a.dataset,
        region=a.region or b.region,
        service=services[0] if services else (a.service or b.service),
        edge=a.edge or b.edge,
    )


def parse_aws(files: Sequence[bytes]) -> Parsed:
    doc = orjson.loads(files[0])
    items: list[tuple[str, RangeInfo]] = []
    for key, prefix_key in (("prefixes", "ip_prefix"), ("ipv6_prefixes", "ipv6_prefix")):
        for p in doc.get(key, []):
            region = p.get("region")
            service = p.get("service")
            info = RangeInfo(
                provider="aws",
                dataset="aws",
                region=None if region in (None, "GLOBAL") else region,
                service=service,
                # CLOUDFRONT_ORIGIN_FACING is where CloudFront fetches from origins, not an edge.
                edge=service == "CLOUDFRONT",
            )
            items.append((p[prefix_key], info))
    return items, doc.get("createDate")


def parse_gcp(files: Sequence[bytes]) -> Parsed:
    doc = orjson.loads(files[0])
    items: list[tuple[str, RangeInfo]] = []
    for p in doc.get("prefixes", []):
        prefix = p.get("ipv4Prefix") or p.get("ipv6Prefix")
        scope = p.get("scope")
        if not prefix:
            continue
        # The "global" scope holds global load balancer addresses, served by Google's front end.
        is_global = scope == "global"
        items.append((prefix, RangeInfo("gcp", "gcp", None if is_global else scope, None, edge=is_global)))
    return items, doc.get("creationTime")


def parse_google(files: Sequence[bytes]) -> Parsed:
    """Every Google address. Checked after cloud.json, so what is left is Google's own front end."""
    doc = orjson.loads(files[0])
    items = [
        (prefix, RangeInfo("gcp", "google", None, None, edge=True))
        for p in doc.get("prefixes", [])
        if (prefix := p.get("ipv4Prefix") or p.get("ipv6Prefix"))
    ]
    return items, doc.get("creationTime")


def parse_azure(files: Sequence[bytes]) -> Parsed:
    doc = orjson.loads(files[0].removeprefix(b"\xef\xbb\xbf"))
    items: list[tuple[str, RangeInfo]] = []
    for value in doc.get("values", []):
        name = value.get("name", "")
        props = value.get("properties", {})
        # Front Door's client-facing anycast space is split across Frontend, FirstParty and
        # MicrosoftSecurity; Backend is the address space Front Door uses to reach origins.
        edge = name.startswith("AzureFrontDoor.") and name != "AzureFrontDoor.Backend"
        info = RangeInfo(
            provider="azure",
            dataset="azure",
            region=props.get("region") or None,
            service=props.get("systemService") or name.split(".")[0],
            edge=edge,
        )
        items.extend((prefix, info) for prefix in props.get("addressPrefixes", []))
    change = doc.get("changeNumber")
    return items, f"changeNumber {change}" if change is not None else None


def parse_oracle(files: Sequence[bytes]) -> Parsed:
    doc = orjson.loads(files[0])
    items = [
        (c["cidr"], RangeInfo("oracle-cloud", "oracle", r.get("region"), "+".join(c.get("tags", [])) or None))
        for r in doc.get("regions", [])
        for c in r.get("cidrs", [])
    ]
    return items, doc.get("last_updated_timestamp")


def parse_cloudflare(files: Sequence[bytes]) -> Parsed:
    items = [
        (line.strip(), RangeInfo("cloudflare", "cloudflare", None, None, edge=True))
        for data in files
        for line in data.decode().splitlines()
        if line.strip()
    ]
    return items, None


def parse_fastly(files: Sequence[bytes]) -> Parsed:
    doc = orjson.loads(files[0])
    prefixes = list(doc.get("addresses", [])) + list(doc.get("ipv6_addresses", []))
    return [(p, RangeInfo("fastly", "fastly", None, None, edge=True)) for p in prefixes], None


def parse_digitalocean(files: Sequence[bytes]) -> Parsed:
    """DigitalOcean publishes a geofeed rather than region names, so the region is the feed's city."""
    items: list[tuple[str, RangeInfo]] = []
    for row in csv.reader(io.StringIO(files[0].decode())):
        if len(row) < 4 or not row[0] or row[0].startswith("#"):
            continue
        city = re.sub(r"[^a-z0-9]+", "-", row[3].lower()).strip("-") or row[1].lower() or None
        items.append((row[0], RangeInfo("digitalocean", "digitalocean", city, None)))
    return items, None


def parse_iptoasn(data: bytes) -> tuple[RangeTable[int], dict[int, str]]:
    """iptoasn.com's combined dump: start, end, ASN, country, AS description per line."""
    v4: list[tuple[int, int, int]] = []
    v6: list[tuple[int, int, int]] = []
    orgs: dict[int, str] = {}
    for line in gzip.decompress(data).decode("utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 5 or parts[2] == "0":  # ASN 0 marks space that is not routed
            continue
        version, start = parse_ip(parts[0])
        _, end = parse_ip(parts[1])
        asn = int(parts[2])
        (v4 if version == 4 else v6).append((start, end, asn))
        orgs.setdefault(asn, parts[4])
    return RangeTable.from_intervals(v4, v6), orgs


async def discover_azure_url(client: PoliteClient) -> str:
    """Microsoft republishes Service Tags weekly under a new dated file name."""
    resp = await client.get(AZURE_DETAILS_URL)
    resp.raise_for_status()
    links = {m.group(1): m.group(0) for m in _AZURE_LINK.finditer(resp.text)}
    if not links:
        raise ValueError("no ServiceTags_Public link on the download page")
    return links[max(links)]


@dataclass(frozen=True)
class _Spec:
    name: str
    files: tuple[tuple[str, UrlSource], ...]
    parse: Callable[[Sequence[bytes]], Parsed]


_RANGE_SPECS = (
    _Spec(
        "cloudflare",
        (("cloudflare-ips-v4.txt", CLOUDFLARE_V4_URL), ("cloudflare-ips-v6.txt", CLOUDFLARE_V6_URL)),
        parse_cloudflare,
    ),
    _Spec("fastly", (("fastly-public-ip-list.json", FASTLY_URL),), parse_fastly),
    _Spec("aws", (("aws-ip-ranges.json", AWS_URL),), parse_aws),
    _Spec("gcp", (("gcp-cloud.json", GCP_URL),), parse_gcp),
    _Spec("google", (("google-goog.json", GOOGLE_URL),), parse_google),
    _Spec("azure", (("azure-service-tags.json", discover_azure_url),), parse_azure),
    _Spec("oracle", (("oracle-public-ip-ranges.json", ORACLE_URL),), parse_oracle),
    _Spec("digitalocean", (("digitalocean-geofeed.csv", DIGITALOCEAN_URL),), parse_digitalocean),
)


@dataclass(frozen=True)
class _Fetched:
    data: bytes
    meta: dict[str, Any]
    status: str


def _read_cache(path: Path) -> tuple[bytes, dict[str, Any]] | None:
    if not path.exists():
        return None
    meta_path = path.with_name(path.name + ".meta.json")
    meta = orjson.loads(meta_path.read_bytes()) if meta_path.exists() else {}
    return path.read_bytes(), meta


def _write_cache(path: Path, data: bytes, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.with_name(path.name + ".meta.json").write_bytes(orjson.dumps(meta, option=orjson.OPT_INDENT_2))


async def _fetch(client: PoliteClient, path: Path, source: UrlSource, refresh: bool) -> _Fetched:
    cached = await asyncio.to_thread(_read_cache, path)
    if cached is not None and not refresh:
        return _Fetched(cached[0], cached[1], "cached")
    try:
        url = source if isinstance(source, str) else await source(client)
        resp = await client.get(url)
        resp.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        if cached is None:
            raise
        log.warning("refresh of %s failed (%s), using the cached copy", path.name, exc)
        return _Fetched(cached[0], {**cached[1], "refresh_error": repr(exc)}, "stale")
    meta = {
        "url": url,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "last_modified": resp.headers.get("last-modified"),
    }
    await asyncio.to_thread(_write_cache, path, resp.content, meta)
    return _Fetched(resp.content, meta, "downloaded")


def _url_date(url: object) -> str | None:
    """The publication date carried in a file name such as ServiceTags_Public_20261026.json."""
    match = re.search(r"_(\d{4})(\d{2})(\d{2})\.json$", url) if isinstance(url, str) else None
    return "-".join(match.groups()) if match else None


def _describe(fetched: Sequence[_Fetched], published: str | None, entries: int) -> dict[str, Any]:
    first = fetched[0]
    return {
        "status": "stale" if any(f.status == "stale" for f in fetched) else first.status,
        "url": [f.meta.get("url") for f in fetched] if len(fetched) > 1 else first.meta.get("url"),
        "fetched_at": first.meta.get("fetched_at"),
        "last_modified": first.meta.get("last_modified"),
        "published": published,
        "entries": entries,
    }


async def _load_ranges(
    client: PoliteClient, cache_dir: Path, spec: _Spec, refresh: bool
) -> tuple[RangeTable[RangeInfo] | None, dict[str, Any]]:
    try:
        fetched = [await _fetch(client, cache_dir / name, source, refresh) for name, source in spec.files]
        items, published = await asyncio.to_thread(spec.parse, [f.data for f in fetched])
        table = await asyncio.to_thread(RangeTable.from_prefixes, items, merge_info)
        if dated := _url_date(fetched[0].meta.get("url")):
            published = f"{dated} ({published})" if published else dated
    except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
        log.warning("dataset %s unavailable: %s", spec.name, exc)
        return None, {"status": "unavailable", "error": repr(exc)}
    return table, _describe(fetched, published, len(items))


async def _load_asn(
    client: PoliteClient, cache_dir: Path, refresh: bool
) -> tuple[RangeTable[int] | None, dict[int, str], dict[str, Any]]:
    try:
        fetched = await _fetch(client, cache_dir / "ip2asn-combined.tsv.gz", IPTOASN_URL, refresh)
        table, orgs = await asyncio.to_thread(parse_iptoasn, fetched.data)
    except (httpx.HTTPError, ValueError, OSError, EOFError) as exc:
        log.warning("dataset iptoasn unavailable: %s", exc)
        return None, {}, {"status": "unavailable", "error": repr(exc)}
    return table, orgs, _describe([fetched], fetched.meta.get("last_modified"), len(table))


async def load_reference_data(
    cache_dir: Path, *, refresh: bool = False, client: PoliteClient | None = None
) -> ReferenceData:
    """Load every dataset from `cache_dir`, downloading what is missing (or everything on refresh)."""
    owned = client is None
    http = client or PoliteClient(concurrency=8, timeout=60.0)
    try:
        ranges, asn = await asyncio.gather(
            asyncio.gather(*(_load_ranges(http, cache_dir, spec, refresh) for spec in _RANGE_SPECS)),
            _load_asn(http, cache_dir, refresh),
        )
    finally:
        if owned:
            await http.aclose()
    tables = [(spec.name, table) for spec, (table, _) in zip(_RANGE_SPECS, ranges, strict=True) if table is not None]
    datasets = {spec.name: info for spec, (_, info) in zip(_RANGE_SPECS, ranges, strict=True)}
    asn_table, asn_orgs, asn_info = asn
    datasets["iptoasn"] = asn_info
    return ReferenceData(tables, asn_table, asn_orgs, datasets)
