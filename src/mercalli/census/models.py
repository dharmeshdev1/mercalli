"""The row the dependency census produces for each domain."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["tranco", "catalog"]


class DomainRecord(BaseModel):
    """What one domain depends on, as seen from one vantage point at one moment.

    `ns` and `mx_hosts` describe the zone the domain lives in. `host` is the name that was probed
    over HTTP and TLS, and every address field describes that host. Provider fields hold taxonomy
    ids (see providers.py), plus "self", "other" and "none" where the taxonomy has no answer.
    """

    domain: str
    rank: int | None = None
    source: Source = "tranco"
    fetched_at: datetime
    ns: list[str] = Field(default_factory=list)
    dns_provider: str | None = None
    dns_providers: list[str] = Field(default_factory=list)
    host: str | None = None
    cname_chain: list[str] = Field(default_factory=list)
    ips: list[str] = Field(default_factory=list)
    asns: list[int] = Field(default_factory=list)
    asn_orgs: list[str] = Field(default_factory=list)
    edge_provider: str | None = None
    hosting_provider: str | None = None
    hosting_region: str | None = None
    tls_issuer: str | None = None
    mx_hosts: list[str] = Field(default_factory=list)
    mx_provider: str | None = None
    http_status: int | None = None
    server_header: str | None = None
    final_host: str | None = None
    evidence: dict[str, list[str]] = Field(default_factory=dict)
    error: str | None = None

    @property
    def resolved(self) -> bool:
        return bool(self.ips)
