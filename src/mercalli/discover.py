"""Find and verify status pages.

Two passes. The first checks every hand-written seed entry: it follows redirects, confirms the page
really serves a status API and records which software runs it. The second looks for status pages
nobody wrote down, by asking `status.<domain>` for a Statuspage API across the most visited domains
in the Tranco list (https://tranco-list.eu), a research ranking that is hard to manipulate.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from mercalli.catalog import SeedEntry, Service, slugify
from mercalli.domain import Category
from mercalli.http import PoliteClient
from mercalli.sources import vendors
from mercalli.sources.statuspage import flavor_of

TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"

_CUSTOM_PROBES = {
    "aws": vendors.AWS_CURRENT,
    "gcp": vendors.GCP_INCIDENTS,
    "azure": vendors.AZURE_FEED,
    "slack": vendors.SLACK_CURRENT,
    "heroku": vendors.HEROKU_CURRENT,
}


@dataclass
class Found:
    base_url: str
    page_id: str
    page_name: str
    flavor: str
    history: bool


def _base_candidates(entry: SeedEntry) -> list[str]:
    seen: list[str] = []

    def add(url: str) -> None:
        url = url.rstrip("/")
        if url and url not in seen:
            seen.append(url)

    add(entry.status)
    for domain in entry.domains[:1]:
        root = domain.removeprefix("www.")
        brand = root.split(".")[0]
        add(f"https://status.{root}")
        add(f"https://{brand}status.com")
        add(f"https://www.{brand}status.com")
        add(f"https://status.{brand}.io")
        add(f"https://{brand}.statuspage.io")
    return seen


def clean_page_name(raw: str) -> str:
    """Turn a page title like "Acme - System Status |" into "Acme"."""
    name = re.split(r"\s+[\u2013\u2014|:-]\s*", raw.strip(), maxsplit=1)[0]
    name = re.sub(r"\s*\b(system\s+)?status(\s+page)?\s*$", "", name, flags=re.IGNORECASE)
    return name.strip(" |-:")


async def probe_statuspage(client: PoliteClient, base: str) -> Found | None:
    """Return details when `base` serves a Statuspage style API, else None."""
    try:
        resp = await client.get(f"{base}/api/v2/summary.json")
    except Exception:  # any failure here, including odd redirects httpx refuses, means no API
        return None
    if resp.status_code != 200 or "json" not in resp.headers.get("content-type", ""):
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    page = data.get("page") if isinstance(data, dict) else None
    if not isinstance(page, dict) or "components" not in data or "status" not in data:
        return None
    final = str(resp.url)
    canonical = final.removesuffix("/api/v2/summary.json").rstrip("/")
    flavor = flavor_of(str(page.get("id", "")))
    history = False
    if flavor == "atlassian":
        try:
            h = await client.get(f"{canonical}/history.json?page=1")
            history = h.status_code == 200 and isinstance(h.json(), dict) and "months" in h.json()
        except Exception:
            history = False
    return Found(
        base_url=canonical,
        page_id=str(page.get("id", "")),
        page_name=str(page.get("name", "")),
        flavor=flavor,
        history=history,
    )


async def verify_entry(client: PoliteClient, entry: SeedEntry) -> Service | str:
    """Verify one seed entry. Returns the service, or a reason it could not be verified."""
    if entry.adapter in _CUSTOM_PROBES:
        try:
            resp = await client.get(_CUSTOM_PROBES[entry.adapter])
        except httpx.HTTPError as exc:
            return f"{type(exc).__name__}"
        if resp.status_code != 200:
            return f"HTTP {resp.status_code} from {entry.adapter} feed"
        return Service(
            id=entry.slug,
            name=entry.name,
            category=entry.category,
            adapter=entry.adapter,
            base_url=entry.status.rstrip("/"),
            domains=entry.domains,
            history=entry.adapter in ("aws", "slack"),
        )
    for base in _base_candidates(entry):
        found = await probe_statuspage(client, base)
        if found:
            return _statuspage_service(entry, found)
    # The page may have moved: load it, then try the API at wherever it landed. The same HTML also
    # says whether the page runs on status.io.
    try:
        landing = await client.get(entry.status)
    except httpx.HTTPError as exc:
        return f"{type(exc).__name__}"
    origin = f"{landing.url.scheme}://{landing.url.host}"
    if origin.rstrip("/") not in _base_candidates(entry):
        found = await probe_statuspage(client, origin)
        if found:
            return _statuspage_service(entry, found)
    m = vendors.STATUSIO_PAGE_ID.search(landing.text[:400_000]) if landing.status_code == 200 else None
    if m:
        try:
            api = await client.get(vendors.STATUSIO_API.format(page_id=m.group(1)))
        except httpx.HTTPError as exc:
            return f"{type(exc).__name__}"
        if api.status_code == 200 and "status_overall" in api.text[:5000]:
            return Service(
                id=entry.slug,
                name=entry.name,
                category=entry.category,
                adapter="statusio",
                base_url=origin,
                domains=entry.domains,
                page_id=m.group(1),
            )
    return f"no supported status API (HTTP {landing.status_code})"


def _statuspage_service(entry: SeedEntry, found: Found) -> Service:
    return Service(
        id=entry.slug,
        name=entry.name,
        category=entry.category,
        adapter="statuspage",
        base_url=found.base_url,
        domains=entry.domains,
        page_id=found.page_id,
        flavor=found.flavor,
        history=found.history,
    )


async def _safe_verify(client: PoliteClient, entry: SeedEntry) -> Service | str:
    try:
        return await verify_entry(client, entry)
    except Exception as exc:  # one broken page must not abort the whole catalog run
        return f"{type(exc).__name__}"


async def verify_seed(entries: list[SeedEntry], client: PoliteClient) -> tuple[list[Service], dict[str, str]]:
    results = await asyncio.gather(*(_safe_verify(client, e) for e in entries))
    services: list[Service] = []
    failures: dict[str, str] = {}
    for entry, res in zip(entries, results, strict=True):
        if isinstance(res, Service):
            services.append(res)
        else:
            failures[entry.slug] = res
    return _dedupe(services), failures


def _dedupe(services: list[Service]) -> list[Service]:
    """Two seed entries can resolve to one page (a product and its parent). Keep the first."""
    by_page: dict[str, Service] = {}
    for s in services:
        key = s.page_id or s.base_url
        if s.adapter != "statuspage":
            key = f"{s.adapter}:{s.id}"
        by_page.setdefault(key, s)
    return list(by_page.values())


async def load_tranco(client: PoliteClient, top_n: int, cache: Path | None = None) -> list[tuple[int, str]]:
    if cache and cache.exists():
        raw = cache.read_bytes()
    else:
        resp = await client.get(TRANCO_URL)
        resp.raise_for_status()
        raw = resp.content
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = zf.namelist()[0]
        text = zf.read(name).decode()
    rows: list[tuple[int, str]] = []
    for rank, domain in csv.reader(io.StringIO(text)):
        rows.append((int(rank), domain.strip().lower()))
        if len(rows) >= top_n:
            break
    return rows


async def discover_from_domains(
    client: PoliteClient, domains: list[tuple[int, str]], known: list[Service]
) -> list[Service]:
    """Look for `status.<domain>` Statuspage APIs that the catalog does not know yet."""
    known_pages = {s.page_id for s in known if s.page_id}
    known_ids = {s.id for s in known}
    known_hosts = {urlsplit(s.base_url).hostname for s in known}

    async def one(rank: int, domain: str) -> Service | None:
        try:
            found = await probe_statuspage(client, f"https://status.{domain}")
        except Exception:  # see probe_statuspage
            return None
        if not found or found.page_id in known_pages or urlsplit(found.base_url).hostname in known_hosts:
            return None
        name = clean_page_name(found.page_name) or domain
        return Service(
            id=brand if (brand := slugify(domain.split(".", maxsplit=1)[0])) not in known_ids else slugify(domain),
            name=name,
            category=Category.OTHER,
            adapter="statuspage",
            base_url=found.base_url,
            domains=[domain],
            page_id=found.page_id,
            flavor=found.flavor,
            history=found.history,
            curated=False,
            rank=rank,
        )

    results = await asyncio.gather(*(one(r, d) for r, d in domains))
    out: list[Service] = []
    seen_pages: set[str] = set()
    seen_ids = set(known_ids)
    for s in results:
        if s is None or (s.page_id and s.page_id in seen_pages):
            continue
        if s.id in seen_ids:
            s.id = slugify(s.domains[0])
        if s.id in seen_ids:
            continue
        seen_ids.add(s.id)
        if s.page_id:
            seen_pages.add(s.page_id)
        out.append(s)
    return out
