"""Longest-prefix lookups over published IP ranges.

Cloud providers publish overlapping prefixes: AWS lists 13.32.0.0/15 under both AMAZON and
CLOUDFRONT, and Azure nests every regional tag inside the catch-all AzureCloud tag. The tables
here flatten such nesting once, at load time, into sorted non-overlapping integer intervals where
every address maps to its most specific prefix. A lookup is then one bisect.
"""

from __future__ import annotations

import ipaddress
import socket
from bisect import bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass

type Interval[T] = tuple[int, int, T]

_MAX = {4: 2**32 - 1, 6: 2**128 - 1}


def parse_ip(text: str) -> tuple[int, int]:
    """Return (version, integer value) for an IPv4 or IPv6 address."""
    if ":" in text:
        return 6, int.from_bytes(socket.inet_pton(socket.AF_INET6, text), "big")
    return 4, int.from_bytes(socket.inet_pton(socket.AF_INET, text), "big")


def prefix_bounds(prefix: str) -> tuple[int, int, int]:
    """Return (version, first address, last address) of a CIDR prefix."""
    net = ipaddress.ip_network(prefix.strip(), strict=False)
    return net.version, int(net.network_address), int(net.broadcast_address)


class RangeTable[T]:
    """Sorted, non-overlapping address intervals for IPv4 and IPv6, each carrying a payload."""

    def __init__(self, v4: list[Interval[T]], v6: list[Interval[T]]) -> None:
        self._starts = {4: [s for s, _, _ in v4], 6: [s for s, _, _ in v6]}
        self._ends = {4: [e for _, e, _ in v4], 6: [e for _, e, _ in v6]}
        self._payloads = {4: [p for _, _, p in v4], 6: [p for _, _, p in v6]}

    @classmethod
    def from_prefixes(cls, items: Iterable[tuple[str, T]], merge: Callable[[T, T], T] | None = None) -> RangeTable[T]:
        """Build from (cidr, payload) pairs.

        A prefix listed more than once is combined with `merge` (the later payload wins when no
        merge is given). Nested prefixes resolve to the innermost one.
        """
        exact: dict[tuple[int, int, int], T] = {}
        for prefix, payload in items:
            key = prefix_bounds(prefix)
            if key in exact and merge is not None:
                exact[key] = merge(exact[key], payload)
            else:
                exact[key] = payload
        by_version: dict[int, list[Interval[T]]] = {4: [], 6: []}
        for (version, start, end), payload in exact.items():
            by_version[version].append((start, end, payload))
        return cls(_flatten(by_version[4], _MAX[4]), _flatten(by_version[6], _MAX[6]))

    @classmethod
    def from_intervals(cls, v4: list[Interval[T]], v6: list[Interval[T]]) -> RangeTable[T]:
        """Build from intervals that are already disjoint, such as an IP to ASN dump."""
        return cls(sorted(v4, key=lambda i: i[0]), sorted(v6, key=lambda i: i[0]))

    def lookup(self, version: int, value: int) -> T | None:
        starts = self._starts[version]
        i = bisect_right(starts, value) - 1
        if i >= 0 and value <= self._ends[version][i]:
            return self._payloads[version][i]
        return None

    def lookup_ip(self, ip: str) -> T | None:
        return self.lookup(*parse_ip(ip))

    def __len__(self) -> int:
        return len(self._starts[4]) + len(self._starts[6])


def _flatten[T](entries: list[Interval[T]], max_value: int) -> list[Interval[T]]:
    """Turn nested-or-disjoint intervals (CIDR blocks) into disjoint ones, innermost payload wins.

    Sorting by start, then by descending end, puts every block before the blocks it contains. A
    stack holds the blocks covering the cursor, innermost on top, and each step emits the stretch
    of address space owned by the top of the stack.
    """
    entries.sort(key=lambda e: (e[0], -e[1]))
    out: list[Interval[T]] = []
    stack: list[tuple[int, T]] = []
    cursor = 0

    def emit_until(limit: int) -> None:
        nonlocal cursor
        while stack and cursor <= limit:
            end, payload = stack[-1]
            stop = min(end, limit)
            if cursor <= stop:
                out.append((cursor, stop, payload))
                cursor = stop + 1
            if end <= limit:
                stack.pop()
            else:
                break

    for start, end, payload in entries:
        emit_until(start - 1)
        cursor = start
        stack.append((end, payload))
    emit_until(max_value)
    return out


@dataclass(frozen=True, slots=True)
class RangeInfo:
    """What a published range says about an address."""

    provider: str
    dataset: str
    region: str | None = None
    service: str | None = None
    edge: bool = False

    @property
    def label(self) -> str:
        parts = [self.dataset, self.region or "global"]
        if self.service:
            parts.append(self.service)
        return "/".join(parts)


@dataclass(frozen=True, slots=True)
class IpFacts:
    """Everything the reference data knows about one answering address."""

    ip: str
    version: int
    value: int
    asn: int | None = None
    asn_org: str | None = None
    range: RangeInfo | None = None
