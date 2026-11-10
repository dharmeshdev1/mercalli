"""Download the public incident history of every status page that publishes one.

Atlassian Statuspage keeps a page's entire history online. `/history.json?page=N` lists one quarter
of incidents per page, newest first, and `/incidents/<code>.json` returns one incident with its
full timeline. Raw responses are cached on disk exactly as received, so a run can stop and resume,
and the dataset can be rebuilt from the cache without touching the network again.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from mercalli.catalog import Service
from mercalli.domain import Impact, Incident
from mercalli.http import PoliteClient
from mercalli.sources import vendors
from mercalli.sources.statuspage import parse_incident, parse_time

MAX_PAGES = 64  # sixteen years of quarters, more than any page has existed
_VAR = re.compile(r"<var data-var='\w+'>([^<]*)</var>")
_MONTHS = {
    m: i
    for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)
}


@dataclass(frozen=True)
class HistoryEntry:
    """One incident as the history index lists it, before its detail is fetched."""

    service_id: str
    code: str
    title: str
    impact: Impact
    start: datetime | None
    end: datetime | None


# Offsets for the zone abbreviations Statuspage prints after history times. Ambiguous ones (IST, CST)
# are settled by the page's own time zone setting.
_ABBR_MINUTES = {
    "UTC": 0,
    "GMT": 0,
    "WET": 0,
    "WEST": 60,
    "BST": 60,
    "IST_IE": 60,
    "CET": 60,
    "CEST": 120,
    "EET": 120,
    "EEST": 180,
    "MSK": 180,
    "IST": 330,
    "PKT": 300,
    "SGT": 480,
    "HKT": 480,
    "AWST": 480,
    "JST": 540,
    "KST": 540,
    "ACST": 570,
    "ACDT": 630,
    "AEST": 600,
    "AEDT": 660,
    "NZST": 720,
    "NZDT": 780,
    "HST": -600,
    "AKST": -540,
    "AKDT": -480,
    "PST": -480,
    "PDT": -420,
    "MST": -420,
    "MDT": -360,
    "CST": -360,
    "CDT": -300,
    "EST": -300,
    "EDT": -240,
    "AST": -240,
    "ADT": -180,
    "NST": -210,
    "NDT": -150,
    "BRT": -180,
    "ART": -180,
}
_GMT_OFFSET = re.compile(r"^GMT([+-])(\d{1,2}):?(\d{2})?$")


def zone_offset(token: str, page_zone: str | None) -> timedelta | None:
    """The UTC offset a history timestamp was printed in, or None when it cannot be told."""
    m = _GMT_OFFSET.match(token)
    if m:
        gmt_minutes = int(m.group(2)) * 60 + int(m.group(3) or 0)
        return timedelta(minutes=gmt_minutes if m.group(1) == "+" else -gmt_minutes)
    zone = (page_zone or "").lower()
    if token == "IST" and any(c in zone for c in ("dublin", "london")):
        token = "IST_IE"
    if token == "CST" and any(c in zone for c in ("beijing", "chongqing", "hong kong", "taipei", "shanghai")):
        return timedelta(hours=8)
    minutes = _ABBR_MINUTES.get(token)
    return timedelta(minutes=minutes) if minutes is not None else None


def parse_history_time(
    text: str, year: int, month: int | None = None, page_zone: str | None = None
) -> tuple[datetime | None, datetime | None]:
    """Parse strings like `Oct 23, 23:41 - Oct 24, 00:23 PDT` into UTC.

    The year comes from the month header. An incident is listed under the month it ended in, so one
    that began on Dec 31 can sit under January: when the start month is later than the header month,
    it began the year before. Times are printed in the page's own zone, named by the trailing token.
    """
    plain = _VAR.sub(r"\1", text).strip()
    token = plain.rsplit(" ", 1)[-1] if " " in plain else ""
    offset = zone_offset(token, page_zone) if token and not token[0].isdigit() else timedelta(0)
    if offset is None:
        return None, None
    if token and not token[0].isdigit():
        plain = plain[: -len(token)].strip()
    parts = [p.strip() for p in plain.split(" - ")]

    def one(part: str, default_month: int | None, default_day: int | None) -> tuple[datetime | None, int, int]:
        m = re.match(r"^(?:(\w{3}) (\d{1,2}), )?(\d{1,2}):(\d{2})$", part)
        if not m:
            return None, default_month or 1, default_day or 1
        month = _MONTHS.get(m.group(1) or "", default_month or 1)
        day = int(m.group(2)) if m.group(2) else (default_day or 1)
        return datetime(year, month, day, int(m.group(3)), int(m.group(4)), tzinfo=UTC), month, day

    start, month_, day = one(parts[0], None, None)
    if start and month and start.month > month:
        start = start.replace(year=start.year - 1)
    end = None
    if len(parts) > 1:
        end, _, _ = one(parts[1], month_, day)
        if start and end and end.year != start.year and end.month >= start.month:
            end = end.replace(year=start.year)
        if start and end and end < start:
            end = end.replace(year=end.year + 1)
    return (start - offset if start else None), (end - offset if end else None)


def parse_history_page(service_id: str, data: dict) -> list[HistoryEntry]:
    page_zone = ((data.get("page_status") or {}).get("page") or {}).get("time_zone")
    out = []
    for month in data.get("months") or []:
        year = int(month.get("year") or 0)
        mnum = _MONTHS.get(str(month.get("name", ""))[:3])
        for inc in month.get("incidents") or []:
            start, end = parse_history_time(inc.get("timestamp") or "", year, mnum, page_zone) if year else (None, None)
            out.append(
                HistoryEntry(
                    service_id=service_id,
                    code=str(inc.get("code")),
                    title=(inc.get("name") or "").strip(),
                    impact=Impact(inc.get("impact")) if inc.get("impact") in Impact._value2member_map_ else Impact.NONE,
                    start=start,
                    end=end,
                )
            )
    return out


class RawCache:
    """Raw responses on disk: one file per month of history index, one per incident timeline, and one
    merged file per service for providers that only publish a recent feed."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def month_path(self, service_id: str, month: str) -> Path:
        return self.root / "history" / service_id / f"{month}.json"

    def incident_path(self, service_id: str, code: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", code)
        return self.root / "incidents" / service_id / f"{safe}.json"

    def feed_path(self, service_id: str) -> Path:
        return self.root / "feeds" / f"{service_id}.json"

    @staticmethod
    def write(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")))
        tmp.replace(path)

    def save_months(self, service_id: str, data: dict) -> list[str]:
        """Split one history page into one file per month, each a valid history document."""
        page = {"page_status": {"page": ((data.get("page_status") or {}).get("page") or {})}}
        saved = []
        for month in data.get("months") or []:
            key = _month_key(month)
            if key:
                self.write(self.month_path(service_id, key), {**page, "months": [month]})
                saved.append(key)
        return saved

    def history_entries(self, service_id: str) -> Iterator[HistoryEntry]:
        for p in sorted((self.root / "history" / service_id).glob("*.json")):
            yield from parse_history_page(service_id, json.loads(p.read_text()))

    def incidents(self, service: Service) -> Iterator[Incident]:
        for p in sorted((self.root / "incidents" / service.id).glob("*.json")):
            raw = json.loads(p.read_text())
            try:
                yield parse_incident(service.id, raw, service.base_url)
            except (AssertionError, ValueError, KeyError):
                continue

    def migrate(self, service_id: str) -> None:
        """Convert caches from older layouts: numbered history pages and dated feed snapshots."""
        hdir = self.root / "history" / service_id
        for p in sorted(hdir.glob("[0-9][0-9][0-9].json")):
            self.save_months(service_id, json.loads(p.read_text()))
            p.unlink()
        fdir = self.root / "feeds" / service_id
        if fdir.is_dir():
            for p in sorted(fdir.glob("*.json")):
                merge_feed(self, service_id, None, json.loads(p.read_text()))
                p.unlink()
            fdir.rmdir()


def _month_key(month: dict) -> str | None:
    number = _MONTHS.get(str(month.get("name", ""))[:3])
    year = month.get("year")
    return f"{int(year):04d}-{number:02d}" if number and year else None


def page_months(page: int, today: datetime) -> list[str]:
    """The months history page `page` covers today: page 1 is this month and the two before."""
    end = today.year * 12 + today.month - 1 - 3 * (page - 1)
    return [f"{m // 12:04d}-{m % 12 + 1:02d}" for m in range(end - 2, end + 1)]


async def backfill_history_index(
    client: PoliteClient, service: Service, cache: RawCache, *, refresh_pages: int = 2, today: datetime | None = None
) -> int:
    """Fetch the history index for one service. Returns how many pages were read.

    Pages are rolling three month windows counted back from today, so they shift every month. The
    cache is kept per calendar month instead, and a page is only fetched when one of its months is
    missing, or when it is one of the `refresh_pages` newest, which can still change.
    """
    today = today or datetime.now(UTC)
    cache.migrate(service.id)
    created_at: datetime | None = None
    for p in (cache.root / "history" / service.id).glob("*.json"):
        created_at = parse_time(
            ((json.loads(p.read_text()).get("page_status") or {}).get("page") or {}).get("created_at")
        )
        break
    pages = 0
    for page in range(1, MAX_PAGES + 1):
        months = page_months(page, today)
        window_start = datetime(int(months[0][:4]), int(months[0][5:]), 1, tzinfo=UTC)
        have_all = all(cache.month_path(service.id, m).exists() for m in months)
        if page > refresh_pages and have_all and created_at is not None:
            pages += 1
            if window_start <= created_at:
                break
            continue
        try:
            resp = await client.get(f"{service.base_url}/history.json?page={page}")
        except httpx.HTTPError:
            break
        if resp.status_code != 200:
            break
        try:
            data = resp.json()
        except ValueError:
            break
        cache.save_months(service.id, data)
        pages += 1
        if created_at is None:
            created_at = parse_time(((data.get("page_status") or {}).get("page") or {}).get("created_at"))
        start = parse_time(data.get("start_time"))
        if not data.get("months") or (created_at and start and start <= created_at):
            break
    return pages


def feed_items(adapter: str, payload: object) -> dict[str, dict]:
    """Key the incidents of one feed response by their vendor id, whatever its shape."""
    if adapter == "aws" and isinstance(payload, dict):
        return {str(ev.get("arn")): ev for events in payload.values() for ev in events if ev.get("arn")}
    if adapter == "statuspage" and isinstance(payload, dict):
        return {str(i.get("id")): i for i in payload.get("incidents") or [] if i.get("id")}
    if isinstance(payload, list):
        return {str(i.get("id")): i for i in payload if isinstance(i, dict) and i.get("id") is not None}
    return {}


def merge_feed(cache: RawCache, service_id: str, adapter: str | None, payload: object) -> int:
    """Merge one feed response into the service's file. Newer copies of an incident replace older."""
    path = cache.feed_path(service_id)
    current = json.loads(path.read_text()) if path.exists() else {"adapter": adapter, "items": {}}
    adapter = adapter or current.get("adapter") or _guess_adapter(payload)
    current["adapter"] = adapter
    items = feed_items(adapter or "", payload)
    current["items"].update(items)
    cache.write(path, current)
    return len(items)


def _guess_adapter(payload: object) -> str:
    if isinstance(payload, dict) and "incidents" in payload:
        return "statuspage"
    if isinstance(payload, dict):
        return "aws"
    return "list"


async def backfill_details(
    client: PoliteClient, service: Service, cache: RawCache, *, since: datetime, max_per_service: int | None = None
) -> tuple[int, int]:
    """Fetch full timelines for incidents that began on or after `since`. Returns (fetched, failed).

    With `max_per_service`, only the most recent incidents are kept, so a vendor that posts thousands
    of narrow incidents (one per mobile carrier, say) cannot drown out everyone else in the dataset.
    """
    entries = sorted(
        (e for e in cache.history_entries(service.id) if e.start is None or e.start >= since),
        key=lambda e: e.start or since,
        reverse=True,
    )
    if max_per_service is not None:
        entries = entries[:max_per_service]
    wanted = [e for e in entries if not cache.incident_path(service.id, e.code).exists()]

    async def one(entry: HistoryEntry) -> bool:
        try:
            resp = await client.get(f"{service.base_url}/incidents/{entry.code}.json")
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        try:
            data = resp.json()
        except ValueError:
            return False
        cache.write(cache.incident_path(service.id, entry.code), data)
        return True

    results = await asyncio.gather(*(one(e) for e in wanted))
    ok = sum(results)
    return ok, len(results) - ok


async def snapshot_feeds(client: PoliteClient, services: list[Service], cache: RawCache) -> int:
    """Merge the recent feeds of providers without a paged history (AWS keeps a year, others less)."""
    merged = 0
    for service in services:
        try:
            if service.adapter == "aws":
                resp = await client.get(vendors.AWS_HISTORY)
                payload: object = vendors._decode_aws(resp.content)
            elif service.adapter == "gcp":
                payload = await client.get_json(vendors.GCP_INCIDENTS)
            elif service.adapter == "slack":
                payload = await client.get_json(vendors.SLACK_HISTORY)
            elif service.adapter == "heroku":
                payload = await client.get_json(vendors.HEROKU_INCIDENTS)
            elif service.adapter == "statuspage" and not service.history:
                payload = await client.get_json(f"{service.base_url}/api/v2/incidents.json")
            else:
                continue
        except (httpx.HTTPError, ValueError):
            continue
        cache.migrate(service.id)
        merge_feed(cache, service.id, service.adapter, payload)
        merged += 1
    return merged
