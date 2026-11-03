"""One HTTP client for every outbound request, with manners.

Most status pages are hosted by a handful of companies, so a burst of requests to "different"
vendors can land on the same infrastructure. The client caps total concurrency, keeps at most a
couple of requests in flight per host and identifies itself with a link back to the project.
"""

from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx

from mercalli import __version__

USER_AGENT = f"Mercalli/{__version__} (+https://github.com/dharmeshdev1/mercalli)"


class PoliteClient:
    """An httpx client with a global and per-host concurrency limit and simple retries."""

    def __init__(
        self,
        *,
        concurrency: int = 32,
        per_host: int = 2,
        timeout: float = 10.0,
        retries: int = 2,
        user_agent: str = USER_AGENT,
    ) -> None:
        self._global = asyncio.Semaphore(concurrency)
        self._per_host: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_host))
        self._retries = retries
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 6.0)),
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept": "application/json, text/plain, */*"},
            limits=httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency),
        )

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @asynccontextmanager
    async def _slot(self, url: str) -> AsyncIterator[None]:
        # Take the host's slot before a global one. The other order lets requests queued behind one
        # busy host sit on every global slot, which serializes everything behind that host.
        host = urlsplit(url).hostname or ""
        async with self._per_host[host], self._global:
            yield

    async def get(self, url: str, **kwargs: object) -> httpx.Response:
        """GET with retries on connection errors and 429 or 5xx responses."""
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                async with self._slot(url):
                    resp = await self._client.get(url, **kwargs)  # type: ignore[arg-type]
                if (resp.status_code == 429 or resp.status_code >= 500) and attempt < self._retries:
                    await asyncio.sleep(_backoff(attempt, resp))
                    continue
                return resp
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last = exc
                if attempt < self._retries:
                    await asyncio.sleep(_backoff(attempt, None))
                    continue
        assert last is not None
        raise last

    async def get_json(self, url: str, **kwargs: object) -> object:
        resp = await self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def get_json_timed(self, url: str, **kwargs: object) -> tuple[object, float]:
        """JSON and the request's own round trip in milliseconds, excluding time spent queued."""
        resp = await self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json(), resp.elapsed.total_seconds() * 1000


def _backoff(attempt: int, resp: httpx.Response | None) -> float:
    if resp is not None:
        retry_after = resp.headers.get("retry-after", "")
        if retry_after.isdigit():
            return min(float(retry_after), 30.0)
    return min(0.5 * (2**attempt) + random.random() * 0.25, 8.0)
