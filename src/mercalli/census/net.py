"""The census's three kinds of network traffic: DNS queries, one HTTP GET and one TLS handshake.

DNS goes to public recursive resolvers so results do not depend on whatever resolver the local
network hands out. HTTP and TLS share one politeness budget: a global cap on connections in flight
and at most two at a time to any single host.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import cache
from urllib.parse import urljoin, urlsplit, urlunsplit

import dns.asyncresolver
import dns.exception
import dns.message
import dns.name
import dns.rdatatype
import dns.resolver
import dns.rrset
import httpx

from mercalli.census.x509 import issuer_of

DEFAULT_NAMESERVERS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")
MAX_REDIRECTS = 5
HTTP_TIMEOUT_S = 8.0
PER_HOST_CONNECTIONS = 2

type AddressResolver = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True)
class Lookup:
    """One DNS answer. `status` is ok, nodata, nxdomain, timeout, servfail or error."""

    status: str
    rrset: dns.rrset.RRset | None = None
    chain: tuple[str, ...] = ()
    canonical: str | None = None

    @property
    def failed(self) -> bool:
        return self.status in ("timeout", "servfail", "error")

    def texts(self) -> list[str]:
        if self.rrset is None:
            return []
        return [rd.to_text() for rd in self.rrset]

    def hostnames(self) -> list[str]:
        """Targets of NS, CNAME and similar records, lowercased and without the trailing dot."""
        if self.rrset is None:
            return []
        return [rd.target.to_text(omit_final_dot=True).lower() for rd in self.rrset]

    def mail_exchangers(self) -> list[tuple[int, str]]:
        if self.rrset is None:
            return []
        return [(rd.preference, rd.exchange.to_text(omit_final_dot=True).lower()) for rd in self.rrset]


def cname_chain(qname: dns.name.Name, response: dns.message.Message) -> tuple[str, ...]:
    """The CNAME targets a resolver followed from `qname`, in order."""
    chain: list[str] = []
    seen: set[dns.name.Name] = set()
    current = qname
    while current not in seen:
        seen.add(current)
        rrset = next((r for r in response.answer if r.rdtype == dns.rdatatype.CNAME and r.name == current), None)
        if rrset is None:
            break
        current = rrset[0].target
        chain.append(current.to_text(omit_final_dot=True).lower())
    return tuple(chain)


class Resolver:
    """dnspython's async resolver with bounded concurrency and retries on timeouts and SERVFAIL."""

    def __init__(
        self,
        nameservers: Sequence[str] = DEFAULT_NAMESERVERS,
        *,
        concurrency: int = 200,
        query_s: float = 3.0,
        attempts: int = 3,
    ) -> None:
        self._sem = asyncio.Semaphore(concurrency)
        self._attempts = attempts
        self._resolver = dns.asyncresolver.Resolver(configure=False)
        self._resolver.nameservers = list(nameservers)
        self._resolver.rotate = True
        self._resolver.timeout = query_s
        self._resolver.lifetime = query_s
        # 1232 bytes is the EDNS buffer size the DNS flag day settled on to avoid fragmentation.
        self._resolver.use_edns(0, 0, 1232)
        # Many domains redirect to the same few hosts; caching saves repeating those lookups.
        self._resolver.cache = dns.resolver.LRUCache(100_000)

    async def query(self, name: str, rdtype: str) -> Lookup:
        try:
            qname = dns.name.from_text(name)
        except dns.exception.DNSException:
            return Lookup("error")
        status = "error"
        for _ in range(self._attempts):
            try:
                async with self._sem:
                    answer = await self._resolver.resolve(qname, rdtype, search=False, raise_on_no_answer=False)
            except dns.resolver.NXDOMAIN:
                return Lookup("nxdomain")
            except dns.exception.Timeout:
                status = "timeout"
                continue
            except dns.resolver.NoNameservers:
                status = "servfail"
                continue
            except dns.exception.DNSException:
                return Lookup("error")
            chain = cname_chain(qname, answer.response) if answer.response is not None else ()
            canonical = answer.canonical_name.to_text(omit_final_dot=True).lower()
            return Lookup("ok" if answer.rrset is not None else "nodata", answer.rrset, chain, canonical)
        return Lookup(status)

    async def zone_of(self, name: str) -> tuple[str | None, list[str], str]:
        """Find the zone `name` belongs to by walking up until a name has its own NS records.

        Returns (zone, name servers, status). Names that are CNAMEs are skipped over, because the
        resolver would otherwise report the NS records of the CNAME's target.
        """
        labels = name.split(".")
        for i in range(len(labels) - 1):
            candidate = ".".join(labels[i:])
            lookup = await self.query(candidate, "NS")
            if lookup.status == "nxdomain" and i == 0:
                return None, [], "nxdomain"
            if lookup.failed:
                return None, [], lookup.status
            if lookup.rrset is not None and lookup.canonical == candidate:
                return candidate, sorted(lookup.hostnames()), "ok"
        return None, [], "nodata"


class HostLimiter:
    """A global cap on connections plus a per-host cap, shared by HTTP and TLS probes."""

    def __init__(self, total: int, per_host: int = PER_HOST_CONNECTIONS) -> None:
        self._total = asyncio.Semaphore(total)
        self._hosts: defaultdict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_host))

    @asynccontextmanager
    async def slot(self, host: str) -> AsyncIterator[None]:
        # Taking the host slot first means a busy host never holds a global slot while it waits.
        async with self._hosts[host.lower()], self._total:
            yield


@dataclass(frozen=True)
class HttpOutcome:
    """The result of one GET with redirects followed by hand.

    `headers` merges the responses served by the probed host itself, since those are the ones
    that describe its edge. `status`, `server` and `final_url` describe the last response.
    """

    status: int | None = None
    server: str | None = None
    final_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def http_client(concurrency: int, user_agent: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": user_agent, "Accept": "*/*"},
        follow_redirects=False,
        # Only response headers are read, never trusted for anything, and requests go to an IP
        # address with the hostname in SNI, so certificate checks would only get in the way. The
        # TLS probe records who issued the certificate.
        verify=False,
        timeout=httpx.Timeout(HTTP_TIMEOUT_S),
        limits=httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=0),
        trust_env=False,
    )


async def probe_http(
    client: httpx.AsyncClient, limiter: HostLimiter, host: str, resolve: AddressResolver
) -> HttpOutcome:
    """GET https://host/ (falling back to http:// when nothing answers TLS) and stop at headers.

    Each hop connects to the address `resolve` returns and names the host in SNI and the Host
    header, so the headers come from the same server the census classified from DNS, and the
    operating system resolver never sees the census's query volume.
    """
    try:
        return await _follow(client, limiter, resolve, f"https://{host}/", host)
    except httpx.ConnectError:
        pass
    except _HOP_ERRORS as exc:
        return HttpOutcome(error=_describe_error(exc))
    try:
        return await _follow(client, limiter, resolve, f"http://{host}/", host)
    except _HOP_ERRORS as exc:
        return HttpOutcome(error=_describe_error(exc))


_HOP_ERRORS = (httpx.HTTPError, httpx.InvalidURL, TimeoutError, UnicodeError, ValueError)


async def _follow(
    client: httpx.AsyncClient, limiter: HostLimiter, resolve: AddressResolver, url: str, host: str
) -> HttpOutcome:
    headers: dict[str, str] = {}
    last: tuple[int, httpx.Headers, str] | None = None
    for hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        hop_host = parts.hostname or ""
        try:
            hop_host = hop_host.encode("idna").decode("ascii")
            address = await resolve(hop_host)
            if address is None:
                raise httpx.ConnectError(f"no address for {hop_host}")
            netloc = f"[{address}]" if ":" in address else address
            host_header = hop_host
            if parts.port is not None:
                netloc, host_header = f"{netloc}:{parts.port}", f"{hop_host}:{parts.port}"
            target = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
            extensions = {"sni_hostname": hop_host} if parts.scheme == "https" else {}
            async with (
                limiter.slot(hop_host),
                asyncio.timeout(HTTP_TIMEOUT_S),
                client.stream("GET", target, headers={"Host": host_header}, extensions=extensions) as resp,
            ):
                last = (resp.status_code, resp.headers, url)
        except _HOP_ERRORS as exc:
            if hop == 0:
                raise
            return _outcome(last, headers, f"redirect to {hop_host}: {_describe_error(exc)}")
        status, response_headers, _ = last
        if hop_host == host:
            for name, value in response_headers.items():
                headers[name] = f"{headers[name]}, {value}" if name in headers else value
        location = response_headers.get("location")
        if status not in _REDIRECTS or not location:
            break
        url = urljoin(url, location)
        if urlsplit(url).scheme not in ("http", "https"):
            break
    return _outcome(last, headers, None)


_REDIRECTS = frozenset({301, 302, 303, 307, 308})


def _outcome(last: tuple[int, httpx.Headers, str] | None, headers: dict[str, str], error: str | None) -> HttpOutcome:
    if last is None:
        return HttpOutcome(headers=headers, error=error)
    status, response_headers, url = last
    return HttpOutcome(
        status=status,
        server=response_headers.get("server"),
        final_url=url,
        headers=headers,
        error=error,
    )


def _describe_error(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0][:120] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


@cache
def _tls_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # The point is to read the issuer of whatever certificate is served, valid or not.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def probe_tls(limiter: HostLimiter, host: str, ip: str) -> tuple[dict[str, str] | None, str | None]:
    """Handshake with `ip` using `host` as SNI and return the certificate issuer, or an error."""
    try:
        async with limiter.slot(host), asyncio.timeout(HTTP_TIMEOUT_S):
            _, writer = await asyncio.open_connection(ip, 443, ssl=_tls_context(), server_hostname=host)
            try:
                ssl_object = writer.get_extra_info("ssl_object")
                der = ssl_object.getpeercert(binary_form=True) if ssl_object is not None else None
            finally:
                writer.close()
                with contextlib.suppress(OSError, TimeoutError):
                    async with asyncio.timeout(1.0):
                        await writer.wait_closed()
    except (OSError, TimeoutError) as exc:
        return None, _describe_error(exc)
    if not der:
        return None, "no peer certificate"
    try:
        return issuer_of(der), None
    except ValueError as exc:
        return None, f"unreadable certificate: {exc}"
