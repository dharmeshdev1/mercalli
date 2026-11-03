import asyncio

import dns.message
import dns.name
import httpx
import pytest
import respx

from mercalli.census.net import HostLimiter, HttpOutcome, cname_chain, http_client, probe_http

UA = "Mercalli-test"


def test_cname_chain_follows_targets_in_order() -> None:
    response = dns.message.from_text(
        """id 1
opcode QUERY
rcode NOERROR
flags QR RD RA
;QUESTION
www.apple.com. IN A
;ANSWER
e6858.dscx.akamaiedge.net. 20 IN A 23.10.1.1
www.apple.com. 300 IN CNAME www.apple.com.edgekey.net.
www.apple.com.edgekey.net. 300 IN CNAME www.apple.com.edgekey.net.globalredir.akadns.net.
www.apple.com.edgekey.net.globalredir.akadns.net. 300 IN CNAME e6858.dscx.akamaiedge.net.
"""
    )
    assert cname_chain(dns.name.from_text("www.apple.com"), response) == (
        "www.apple.com.edgekey.net",
        "www.apple.com.edgekey.net.globalredir.akadns.net",
        "e6858.dscx.akamaiedge.net",
    )


def test_cname_chain_stops_on_a_loop() -> None:
    response = dns.message.from_text(
        """id 1
opcode QUERY
rcode NOERROR
flags QR RD RA
;QUESTION
a.example. IN A
;ANSWER
a.example. 60 IN CNAME b.example.
b.example. 60 IN CNAME a.example.
"""
    )
    assert cname_chain(dns.name.from_text("a.example"), response) == ("b.example", "a.example")


ADDRESSES = {
    "www.example.com": "192.0.2.10",
    "example.com": "192.0.2.11",
    "loop.example": "192.0.2.20",
    "legacy.example": "192.0.2.30",
    "a.example": "192.0.2.40",
    "b.example": "192.0.2.41",
    "slow.example": "192.0.2.50",
    "ua.example": "192.0.2.60",
    "v6.example": "2001:db8::7",
}


async def resolve(host: str) -> str | None:
    return ADDRESSES.get(host)


async def probe(host: str) -> HttpOutcome:
    async with http_client(4, UA) as client:
        return await probe_http(client, HostLimiter(4), host, resolve)


@respx.mock
async def test_redirect_headers_only_from_the_probed_host() -> None:
    first = respx.get("https://192.0.2.10/").mock(
        return_value=httpx.Response(301, headers={"location": "https://example.com/", "cf-ray": "1-BOM"})
    )
    second = respx.get("https://192.0.2.11/").mock(
        return_value=httpx.Response(200, headers={"server": "nginx", "x-vercel-id": "bom1::abc"})
    )
    out = await probe("www.example.com")
    assert out.status == 200
    assert out.server == "nginx"
    assert out.final_url == "https://example.com/"
    assert out.headers == {"location": "https://example.com/", "cf-ray": "1-BOM"}
    assert out.error is None
    sent = first.calls.last.request
    assert sent.headers["host"] == "www.example.com"
    assert sent.extensions["sni_hostname"] == "www.example.com"
    assert second.calls.last.request.headers["host"] == "example.com"


@respx.mock
async def test_relative_redirect_and_redirect_limit() -> None:
    route = respx.get(url__regex=r"https://192\.0\.2\.20/.*").mock(
        return_value=httpx.Response(302, headers={"location": "/again?x=1"})
    )
    out = await probe("loop.example")
    assert route.call_count == 6  # the first request plus five redirects
    assert out.status == 302
    assert out.final_url == "https://loop.example/again?x=1"


@respx.mock
async def test_ipv6_address_and_explicit_port() -> None:
    route = respx.get("https://[2001:db8::7]:8443/").mock(return_value=httpx.Response(204))
    respx.get("https://192.0.2.10/").mock(
        return_value=httpx.Response(301, headers={"location": "https://v6.example:8443/"})
    )
    out = await probe("www.example.com")
    assert out.status == 204
    assert route.calls.last.request.headers["host"] == "v6.example:8443"


@respx.mock
async def test_falls_back_to_plain_http_when_tls_connect_fails() -> None:
    respx.get("https://192.0.2.30/").mock(side_effect=httpx.ConnectError("no listener"))
    plain = respx.get("http://192.0.2.30/").mock(return_value=httpx.Response(200, headers={"server": "Apache"}))
    out = await probe("legacy.example")
    assert (out.status, out.server, out.final_url) == (200, "Apache", "http://legacy.example/")
    assert "sni_hostname" not in plain.calls.last.request.extensions


@respx.mock
async def test_failed_redirect_target_keeps_what_the_first_hop_showed() -> None:
    respx.get("https://192.0.2.40/").mock(
        return_value=httpx.Response(301, headers={"location": "https://b.example/", "x-amz-cf-id": "z"})
    )
    respx.get("https://192.0.2.41/").mock(side_effect=httpx.ConnectTimeout("slow"))
    out = await probe("a.example")
    assert out.status == 301
    assert out.headers["x-amz-cf-id"] == "z"
    assert out.error is not None
    assert out.error.startswith("redirect to b.example: ConnectTimeout")


@respx.mock
async def test_redirect_to_a_host_without_addresses() -> None:
    respx.get("https://192.0.2.40/").mock(
        return_value=httpx.Response(302, headers={"location": "https://nowhere.example/"})
    )
    out = await probe("a.example")
    assert out.status == 302
    assert out.error == "redirect to nowhere.example: ConnectError: no address for nowhere.example"


@respx.mock
async def test_timeout_on_first_hop_is_an_error_without_fallback() -> None:
    https = respx.get("https://192.0.2.50/").mock(side_effect=httpx.ReadTimeout("slow"))
    plain = respx.get("http://192.0.2.50/").mock(return_value=httpx.Response(200))
    out = await probe("slow.example")
    assert out.status is None
    assert out.error == "ReadTimeout: slow"
    assert (https.call_count, plain.call_count) == (1, 0)


@respx.mock
async def test_user_agent_is_sent() -> None:
    route = respx.get("https://192.0.2.60/").mock(return_value=httpx.Response(204))
    await probe("ua.example")
    assert route.calls.last.request.headers["user-agent"] == UA


@pytest.mark.parametrize(("per_host", "total", "expected"), [(2, 10, 2), (2, 1, 1), (5, 3, 3)])
async def test_host_limiter_caps_concurrency(per_host: int, total: int, expected: int) -> None:
    limiter = HostLimiter(total, per_host=per_host)
    active = peak = 0

    async def hit() -> None:
        nonlocal active, peak
        async with limiter.slot("Same.Example"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(hit() for _ in range(8)))
    assert peak == expected
