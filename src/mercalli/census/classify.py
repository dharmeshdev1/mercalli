"""Decide providers from raw observations. Pure functions: no network, no clock.

Each decision returns the provider id and the evidence strings that decided it, so every row of
the census can be audited by hand.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache

from mercalli.census.iprange import IpFacts, RangeTable
from mercalli.census.providers import NONE, OTHER, PROVIDERS, PSEUDO, SELF, NameRule, Pattern, by_id

# Second-level labels under which registrations happen one level deeper (example.co.uk). A rough
# stand-in for the public suffix list, used only to group and compare name server domains.
_SECOND_LEVEL = frozenset({"co", "com", "net", "org", "gov", "edu", "ac", "ne", "or", "go", "gob"})


@dataclass(frozen=True)
class Match:
    provider: str
    label: str


@dataclass(frozen=True)
class Decision:
    value: str | None
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DnsDecision:
    primary: str | None
    providers: list[str]
    evidence: list[str]


@dataclass(frozen=True)
class HostingDecision:
    provider: str | None
    region: str | None
    evidence: list[str]


class NameMatcher:
    """Map hostnames to providers by domain suffix, most specific suffix first."""

    def __init__(self, rules: Iterable[tuple[NameRule, str]]) -> None:
        self._suffixes: dict[str, str] = {}
        self._patterns: list[tuple[re.Pattern[str], str, str]] = []
        for rule, provider in rules:
            if isinstance(rule, Pattern):
                self._patterns.append((re.compile(rule.regex), rule.label, provider))
                continue
            owner = self._suffixes.setdefault(rule, provider)
            if owner != provider:
                raise ValueError(f"suffix {rule!r} claimed by both {owner} and {provider}")

    def match(self, host: str) -> Match | None:
        host = host.lower().rstrip(".")
        labels = host.split(".")
        for i in range(len(labels)):
            suffix = ".".join(labels[i:])
            provider = self._suffixes.get(suffix)
            if provider is not None:
                return Match(provider, suffix if i == 0 else f"*.{suffix}")
        for regex, label, provider in self._patterns:
            if regex.search(host):
                return Match(provider, label)
        return None


@dataclass(frozen=True)
class _HeaderRule:
    provider: str
    name: str
    regex: re.Pattern[str] | None


@dataclass(frozen=True)
class _Index:
    ns: NameMatcher
    edge_cnames: NameMatcher
    mx: NameMatcher
    headers: tuple[_HeaderRule, ...]
    asn_provider: dict[int, str]
    edge_asns: frozenset[int]
    platform_prefixes: RangeTable[str]
    issuers: tuple[tuple[re.Pattern[str], str], ...]
    platforms: frozenset[str]


@cache
def _index() -> _Index:
    providers = by_id()
    asn_provider: dict[int, str] = {}
    for p in PROVIDERS:
        for asn in p.asns:
            owner = asn_provider.setdefault(asn, p.id)
            if owner != p.id:
                raise ValueError(f"ASN {asn} claimed by both {owner} and {p.id}")
    headers = tuple(
        _HeaderRule(
            p.id,
            h.name.lower(),
            re.compile(h.pattern, 0 if h.case_sensitive else re.IGNORECASE) if h.pattern else None,
        )
        for p in PROVIDERS
        for h in p.headers
    )
    return _Index(
        ns=NameMatcher((rule, p.id) for p in PROVIDERS for rule in p.ns),
        edge_cnames=NameMatcher((rule, p.id) for p in PROVIDERS for rule in p.edge_cnames),
        mx=NameMatcher((rule, p.id) for p in PROVIDERS for rule in p.mx),
        headers=headers,
        asn_provider=asn_provider,
        edge_asns=frozenset(asn for p in PROVIDERS if p.edge_asn for asn in p.asns),
        platform_prefixes=RangeTable.from_prefixes((cidr, p.id) for p in PROVIDERS for cidr in p.edge_prefixes),
        issuers=tuple((re.compile(rx, re.IGNORECASE), p.ca_name or p.name) for p in PROVIDERS for rx in p.issuers),
        platforms=frozenset(p.id for p in providers.values() if p.platform),
    )


def registrable(host: str) -> str:
    """The registered domain of a hostname, approximately (see _SECOND_LEVEL)."""
    labels = host.lower().rstrip(".").split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def within(host: str, zone: str) -> bool:
    return host == zone or host.endswith("." + zone)


def classify_dns(ns_hosts: Sequence[str], zone: str | None) -> DnsDecision:
    """Name servers to DNS providers. The primary is the provider holding the most NS records.

    Zones served by more than one provider list all of them, which matters for blast radius: a
    zone on two providers survives an outage at either.
    """
    if not ns_hosts:
        return DnsDecision(None, [], [])
    idx = _index()
    per_host: list[tuple[str, str]] = []
    for host in ns_hosts:
        m = idx.ns.match(host)
        if m is not None:
            per_host.append((m.provider, f"ns:{m.label}"))
        elif zone and (within(host, zone) or registrable(host) == registrable(zone)):
            per_host.append((SELF, "ns:self"))
        else:
            per_host.append((OTHER, f"ns:other:{registrable(host)}"))
    counts = Counter(p for p, _ in per_host)
    ordered = sorted(counts, key=lambda p: (-counts[p], p in PSEUDO, p))
    evidence = _unique(label for p in ordered for q, label in per_host if q == p)
    return DnsDecision(ordered[0], ordered, evidence)


def classify_edge(chain: Sequence[str], ips: Sequence[IpFacts], headers: Mapping[str, str]) -> Decision:
    """Who answers HTTP for the host: a CDN, a security proxy or a multi-tenant platform.

    Signals in order of authority:

    1. The CNAME chain. The first recognized target is where the domain owner sent traffic.
    2. A platform's documented anycast address (Shopify's 23.227.38.0/24 sits inside Cloudflare's
       ASN, so this has to beat step 3).
    3. Addresses inside an edge network: Cloudflare and Fastly ranges, CloudFront, Azure Front
       Door, Google's front end, or an ASN that only carries edge traffic.
    4. Response headers. CDN headers win over platform headers because a CDN can sit in front
       of a platform but rarely the other way round.

    Signals pointing at other providers are kept as `also:` evidence, which is how a Vercel site
    proxied through Cloudflare shows up.
    """
    idx = _index()
    signals: list[tuple[str, str]] = []
    decided: str | None = None

    for target in chain:
        m = idx.edge_cnames.match(target)
        if m is not None:
            signals.append((m.provider, f"cname:{m.label}"))
            decided = decided or m.provider

    # Only the addresses that decide hosting vote, so a site on CloudFront over IPv4 and Akamai
    # over IPv6 gets the edge its probes actually reached; the other family stays as evidence.
    voters = {f.ip for f in _deciding(ips)}
    platform_hits: Counter[str] = Counter()
    edge_hits: Counter[str] = Counter()
    for f in ips:
        platform = idx.platform_prefixes.lookup(f.version, f.value)
        if platform is not None:
            signals.append((platform, f"prefix:{f.ip}"))
            if f.ip in voters:
                platform_hits[platform] += 1
        network = _edge_network(f, idx)
        if network is not None:
            signals.append(network)
            if f.ip in voters:
                edge_hits[network[0]] += 1

    header_hits = _header_matches(headers)
    signals.extend((m.provider, m.label) for m in header_hits)

    if decided is None and platform_hits:
        decided = platform_hits.most_common(1)[0][0]
    if decided is None and edge_hits:
        decided = edge_hits.most_common(1)[0][0]
    if decided is None and header_hits:
        networks = [m.provider for m in header_hits if m.provider not in idx.platforms]
        decided = (networks or [m.provider for m in header_hits])[0]

    if decided is None:
        return Decision(None, _unique(f"also:{p}:{label}" for p, label in signals))
    evidence = _unique(label for p, label in signals if p == decided)
    evidence += _unique(f"also:{p}:{label}" for p, label in signals if p != decided)
    return Decision(decided, evidence)


def _edge_network(f: IpFacts, idx: _Index) -> tuple[str, str] | None:
    """The edge network an address belongs to, with the evidence label, if any."""
    if f.range is not None and f.range.edge:
        return f.range.provider, f"range:{f.range.label}"
    if f.asn is not None and f.asn in idx.edge_asns:
        return idx.asn_provider[f.asn], f"asn:{f.asn}"
    return None


def _header_matches(headers: Mapping[str, str]) -> list[Match]:
    out: list[Match] = []
    for rule in _index().headers:
        value = headers.get(rule.name)
        if value is None:
            continue
        if rule.regex is None:
            out.append(Match(rule.provider, f"header:{rule.name}"))
        elif rule.regex.search(value):
            out.append(Match(rule.provider, f"header:{rule.name}={value[:40]}"))
    return out


def _deciding(ips: Sequence[IpFacts]) -> list[IpFacts]:
    """IPv4 addresses when there are any: what most clients reach and what the probes connect to.

    Dual-stack sites sometimes serve IPv6 from a different network than IPv4.
    """
    return [f for f in ips if f.version == 4] or list(ips)


def classify_hosting(ips: Sequence[IpFacts]) -> HostingDecision:
    """Whose address space the answering IPs belong to: published ranges first, then the ASN.

    When an edge network fronts the site these are the edge's addresses, not the origin's.
    """
    idx = _index()
    per_ip: list[tuple[int, str, str | None, str]] = []
    for f in ips:
        if f.range is not None:
            per_ip.append((f.version, f.range.provider, f.range.region, f"range:{f.range.label}"))
        elif f.asn is not None:
            per_ip.append((f.version, idx.asn_provider.get(f.asn, OTHER), None, f"asn:{f.asn}"))
    if not per_ip:
        return HostingDecision(None, None, [])
    pool = [e for e in per_ip if e[0] == 4] or per_ip
    provider = Counter(e[1] for e in pool).most_common(1)[0][0]
    chosen = [e for e in pool if e[1] == provider]
    regions = Counter(e[2] for e in chosen if e[2])
    region = regions.most_common(1)[0][0] if regions else None
    return HostingDecision(provider, region, _unique(e[3] for e in chosen))


def classify_mx(records: Sequence[tuple[int, str]] | None) -> Decision:
    """Mail exchangers to an email provider, judged by the most preferred exchangers.

    `records` is None when the lookup failed, which is different from a domain without mail.
    """
    if records is None:
        return Decision(None, [])
    hosts = [(pref, host.lower().rstrip(".")) for pref, host in records]
    if not hosts:
        return Decision(NONE, ["mx:none"])
    if all(not host for _, host in hosts):
        return Decision(NONE, ["mx:null"])  # RFC 7505 null MX: the domain accepts no mail
    best = min(pref for pref, host in hosts if host)
    primary = [host for pref, host in hosts if pref == best and host]
    idx = _index()
    matches = [m for m in (idx.mx.match(h) for h in primary) if m is not None]
    if not matches:
        return Decision(OTHER, [f"mx:other:{registrable(primary[0])}"])
    provider = Counter(m.provider for m in matches).most_common(1)[0][0]
    return Decision(provider, _unique(f"mx:{m.label}" for m in matches if m.provider == provider))


_ORG_SUFFIX = re.compile(
    r"[,.]?\s+(Co\.?,?\s*Ltd\.?|Inc\.?|LLC|L\.L\.C\.|Ltd\.?|Limited|S\.A\.|SAS|AG|B\.V\.|GmbH|nv-sa|Corporation|Co\.)$",
    re.IGNORECASE,
)


def classify_issuer(issuer: Mapping[str, str] | None) -> Decision:
    """Normalize a certificate issuer to the organization that runs the CA."""
    if not issuer:
        return Decision(None, [])
    org = issuer.get("O") or ""
    common = issuer.get("CN") or ""
    evidence = [f"issuer:O={org},CN={common}" if org else f"issuer:CN={common}"]
    for regex, name in _index().issuers:
        if (org and regex.search(org)) or (common and regex.search(common)):
            return Decision(name, evidence)
    raw = (org or common).strip()
    return Decision(_ORG_SUFFIX.sub("", raw) or None, evidence)


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))
