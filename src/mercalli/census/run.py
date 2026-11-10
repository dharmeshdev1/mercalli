"""Probe each domain and classify what it depends on."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from mercalli.census.classify import (
    classify_dns,
    classify_edge,
    classify_hosting,
    classify_issuer,
    classify_mx,
    within,
)
from mercalli.census.iprange import parse_ip
from mercalli.census.models import DomainRecord, Source
from mercalli.census.net import (
    DEFAULT_NAMESERVERS,
    AddressResolver,
    HostLimiter,
    Resolver,
    http_client,
    probe_http,
    probe_tls,
)
from mercalli.census.refdata import ReferenceData, load_reference_data
from mercalli.domain import utcnow
from mercalli.http import USER_AGENT

log = logging.getLogger(__name__)

_PROGRESS_EVERY = 500


@dataclass(frozen=True)
class _Context:
    resolver: Resolver
    limiter: HostLimiter
    client: httpx.AsyncClient
    reference: ReferenceData


async def census(
    domains: list[tuple[int | None, str]],
    *,
    cache_dir: Path,
    refresh: bool = False,
    dns_concurrency: int = 200,
    http_concurrency: int = 64,
    nameservers: Sequence[str] = DEFAULT_NAMESERVERS,
    reference: ReferenceData | None = None,
) -> list[DomainRecord]:
    """Survey `domains`, given as (Tranco rank or None, domain) pairs, and return one record each.

    A None rank marks a domain that came from the Mercalli catalog rather than the Tranco list.
    Reference data is loaded from `cache_dir` (downloaded when missing or when `refresh` is set)
    unless the caller passes it in.
    """
    reference = reference or await load_reference_data(cache_dir, refresh=refresh)
    resolver = Resolver(nameservers, concurrency=dns_concurrency)
    limiter = HostLimiter(http_concurrency)
    async with http_client(http_concurrency, USER_AGENT) as client:
        ctx = _Context(resolver, limiter, client, reference)
        return await _gather_with_progress([_survey(rank, domain, ctx) for rank, domain in domains])


async def _gather_with_progress(jobs: list[Awaitable[DomainRecord]]) -> list[DomainRecord]:
    done = 0
    started = time.monotonic()

    async def tracked(job: Awaitable[DomainRecord]) -> DomainRecord:
        nonlocal done
        record = await job
        done += 1
        if done % _PROGRESS_EVERY == 0 or done == len(jobs):
            log.info("census: %d/%d domains in %.0f s", done, len(jobs), time.monotonic() - started)
        return record

    return list(await asyncio.gather(*(tracked(j) for j in jobs)))


def _address_book(resolver: Resolver, host: str, address: str) -> AddressResolver:
    """Addresses for the hosts a redirect chain visits, starting from the probed host's own."""
    known: dict[str, str | None] = {host: address}

    async def resolve(name: str) -> str | None:
        if name not in known:
            found = (await resolver.query(name, "A")).texts() or (await resolver.query(name, "AAAA")).texts()
            known[name] = found[0] if found else None
        return known[name]

    return resolve


def _sorted_ips(addresses: list[str]) -> list[str]:
    return sorted(set(addresses), key=parse_ip)


async def _survey(rank: int | None, domain: str, ctx: _Context) -> DomainRecord:
    domain = domain.strip().lower().rstrip(".")
    source: Source = "tranco" if rank is not None else "catalog"
    errors: list[str] = []

    zone, ns_hosts, ns_status = await ctx.resolver.zone_of(domain)
    if ns_status == "nxdomain":
        return DomainRecord(domain=domain, rank=rank, source=source, fetched_at=utcnow(), error="dns: nxdomain")
    if ns_status != "ok":
        errors.append(f"dns: ns {ns_status}")

    mx_job = asyncio.create_task(ctx.resolver.query(zone or domain, "MX"))
    # A registered domain is probed as www.<domain> when that resolves, the way people type it.
    # A catalog entry that is already a hostname below its zone (api.github.com) is probed as is.
    candidates = [f"www.{domain}", domain] if zone in (None, domain) else [domain]
    host: str | None = None
    chain: tuple[str, ...] = ()
    ips: list[str] = []
    address_status = "nodata"
    for candidate in candidates:
        a, aaaa = await asyncio.gather(ctx.resolver.query(candidate, "A"), ctx.resolver.query(candidate, "AAAA"))
        found = a.texts() + aaaa.texts()
        if found:
            host, chain, ips = candidate, a.chain or aaaa.chain, _sorted_ips(found)
            break
        if a.failed or aaaa.failed:
            address_status = a.status if a.failed else aaaa.status
    if host is None:
        errors.append(f"dns: no address ({address_status})")
    mx = await mx_job
    if mx.failed:
        errors.append(f"dns: mx {mx.status}")

    facts = [ctx.reference.lookup(ip) for ip in ips]
    http_status = server = final_host = None
    headers: dict[str, str] = {}
    issuer: dict[str, str] | None = None
    if host is not None:
        address = next((ip for ip in ips if ":" not in ip), ips[0])
        http, (issuer, tls_error) = await asyncio.gather(
            probe_http(ctx.client, ctx.limiter, host, _address_book(ctx.resolver, host, address)),
            probe_tls(ctx.limiter, host, address),
        )
        http_status, server, headers = http.status, http.server, http.headers
        final = urlsplit(http.final_url).hostname if http.final_url else None
        if final and not within(final, zone or domain):
            final_host = final
        if http.error:
            errors.append(f"http: {http.error}")
        if tls_error:
            errors.append(f"tls: {tls_error}")

    dns_decision = classify_dns(ns_hosts, zone)
    edge = classify_edge(chain, facts, headers) if host is not None else None
    hosting = classify_hosting(facts)
    tls = classify_issuer(issuer)
    mail = classify_mx(None if mx.failed else mx.mail_exchangers())

    asns = list(dict.fromkeys(f.asn for f in facts if f.asn is not None))
    orgs = {f.asn: f.asn_org or "" for f in facts if f.asn is not None}
    evidence = {
        "dns": dns_decision.evidence,
        "edge": edge.evidence if edge else [],
        "hosting": hosting.evidence,
        "tls": tls.evidence,
        "mx": mail.evidence,
    }
    return DomainRecord(
        domain=domain,
        rank=rank,
        source=source,
        fetched_at=utcnow(),
        ns=ns_hosts,
        dns_provider=dns_decision.primary,
        dns_providers=dns_decision.providers,
        host=host,
        cname_chain=list(chain),
        ips=ips,
        asns=asns,
        asn_orgs=[orgs[a] for a in asns],
        edge_provider=edge.value if edge else None,
        hosting_provider=hosting.provider,
        hosting_region=hosting.region,
        tls_issuer=tls.value,
        mx_hosts=[h for _, h in sorted(mx.mail_exchangers())],
        mx_provider=mail.value,
        http_status=http_status,
        server_header=server,
        final_host=final_host,
        evidence={k: v for k, v in evidence.items() if v},
        error="; ".join(errors) or None,
    )
