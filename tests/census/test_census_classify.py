import pytest

from mercalli.census.classify import (
    NameMatcher,
    classify_dns,
    classify_edge,
    classify_hosting,
    classify_issuer,
    classify_mx,
    registrable,
)
from mercalli.census.iprange import IpFacts, RangeInfo, parse_ip
from mercalli.census.providers import PROVIDERS, Pattern, by_id, display_name


def ip(address: str, asn: int | None = None, rng: RangeInfo | None = None) -> IpFacts:
    version, value = parse_ip(address)
    return IpFacts(ip=address, version=version, value=value, asn=asn, asn_org=None, range=rng)


CLOUDFLARE_RANGE = RangeInfo("cloudflare", "cloudflare", None, None, edge=True)
EC2_US_EAST_1 = RangeInfo("aws", "aws", "us-east-1", "EC2")
CLOUDFRONT = RangeInfo("aws", "aws", None, "CLOUDFRONT", edge=True)


# Taxonomy -------------------------------------------------------------------------------------------


def test_catalog_ids_the_graph_joins_on_are_present() -> None:
    required = {
        "aws", "gcp", "azure", "cloudflare", "fastly", "akamai", "vercel", "netlify", "flyio", "digitalocean",
        "linode", "hetzner", "ovhcloud", "oracle-cloud", "bunny", "imperva", "gcore", "ns1", "dnsimple",
        "namecheap", "github", "heroku", "render", "shopify", "squarespace", "wix", "webflow", "automattic",
        "wpengine", "kinsta", "pantheon", "vultr", "scaleway", "backblaze", "keycdn", "railway", "firebase",
        "supabase",
    }  # fmt: skip
    assert required <= set(by_id())


def test_every_provider_has_a_kind_and_the_index_has_no_conflicting_claims() -> None:
    assert all(p.kinds for p in PROVIDERS)
    # Building the decision index fails loudly on a suffix or ASN claimed by two providers.
    assert classify_dns(["kate.ns.cloudflare.com"], "example.com").primary == "cloudflare"


def test_kinds_are_derived_from_matchers() -> None:
    assert by_id()["aws"].kinds == {"dns", "edge", "hosting", "ca"}
    assert by_id()["google-workspace"].kinds == {"email"}
    assert display_name("other") == "Other (not in taxonomy)"
    assert display_name("not-a-provider") == "not-a-provider"


def test_name_matcher_prefers_longest_suffix_then_patterns() -> None:
    m = NameMatcher(
        [("cloudflare.net", "a"), ("cdn.cloudflare.net", "b"), (Pattern(r"\.awsdns-\d+\.com$", "*.awsdns"), "c")]
    )
    assert m.match("x.cdn.cloudflare.net") == m.match("X.CDN.Cloudflare.NET.")
    assert m.match("x.cdn.cloudflare.net").provider == "b"
    assert m.match("x.cdn.cloudflare.net").label == "*.cdn.cloudflare.net"
    assert m.match("cloudflare.net").label == "cloudflare.net"
    assert m.match("ns-1.awsdns-07.com").provider == "c"
    assert m.match("evilcloudflare.net") is None


def test_name_matcher_rejects_a_suffix_claimed_twice() -> None:
    with pytest.raises(ValueError, match="claimed by both"):
        NameMatcher([("example.net", "a"), ("example.net", "b")])


def test_registrable_handles_two_level_suffixes() -> None:
    assert registrable("ns1.bbc.co.uk") == "bbc.co.uk"
    assert registrable("a.ns.facebook.com") == "facebook.com"
    assert registrable("example.de") == "example.de"
    assert registrable("ns10.nic.in") == "nic.in"


# DNS ------------------------------------------------------------------------------------------------


def test_route53_numbered_name_servers() -> None:
    d = classify_dns(["ns-81.awsdns-10.com", "ns-659.awsdns-18.net", "ns-1984.awsdns-56.co.uk"], "netflix.com")
    assert (d.primary, d.providers, d.evidence) == ("aws", ["aws"], ["ns:*.awsdns-NN.*"])


def test_multi_provider_zone_lists_both_and_breaks_ties_by_id() -> None:
    ns = ["dns1.p08.nsone.net", "dns2.p08.nsone.net", "ns-421.awsdns-52.com", "ns-520.awsdns-01.net"]
    d = classify_dns(ns, "github.com")
    assert d.primary == "aws"
    assert d.providers == ["aws", "ns1"]
    assert d.evidence == ["ns:*.awsdns-NN.*", "ns:*.nsone.net"]


def test_majority_provider_wins() -> None:
    ns = ["a.ns.cloudflare.com", "b.ns.cloudflare.com", "ns1.p01.nsone.net"]
    assert classify_dns(ns, "example.com").primary == "cloudflare"


def test_self_hosted_and_unknown_name_servers() -> None:
    assert classify_dns(["a.ns.example.com", "b.ns.example.com"], "example.com").primary == "self"
    unknown = classify_dns(["ns1.tiny-dns.example"], "example.org")
    assert (unknown.primary, unknown.evidence) == ("other", ["ns:other:tiny-dns.example"])
    # A named provider outranks self or other at equal counts.
    mixed = classify_dns(["ns1.example.com", "kate.ns.cloudflare.com"], "example.com")
    assert mixed.providers == ["cloudflare", "self"]


def test_no_name_servers_is_undecided() -> None:
    assert classify_dns([], "example.com").primary is None


# Edge -----------------------------------------------------------------------------------------------


def test_akamai_cname_chain() -> None:
    chain = ["www.apple.com.edgekey.net", "e6858.dscx.akamaiedge.net"]
    d = classify_edge(chain, [ip("23.10.1.1", asn=20940)], {})
    assert d.value == "akamai"
    assert d.evidence[:2] == ["cname:*.edgekey.net", "cname:*.akamaiedge.net"]
    assert "asn:20940" in d.evidence


def test_first_recognized_cname_is_where_the_owner_sent_traffic() -> None:
    d = classify_edge(["shops.myshopify.com"], [ip("23.227.38.65", asn=13335)], {"cf-ray": "abc", "x-shopid": "1"})
    assert d.value == "shopify"
    assert "also:cloudflare:asn:13335" in d.evidence


def test_platform_anycast_prefix_beats_the_network_it_sits_in() -> None:
    d = classify_edge([], [ip("23.227.38.65", asn=13335)], {})
    assert (d.value, d.evidence[0]) == ("shopify", "prefix:23.227.38.65")
    pages = classify_edge([], [ip("185.199.108.153", asn=54113)], {"x-served-by": "cache-bom-123"})
    assert pages.value == "github"


def test_edge_range_decides_for_apex_records() -> None:
    d = classify_edge([], [ip("104.16.1.1", asn=13335, rng=CLOUDFLARE_RANGE)], {"cf-ray": "1-BOM"})
    assert (d.value, d.evidence) == ("cloudflare", ["range:cloudflare/global", "header:cf-ray"])


def test_cloudfront_range_maps_to_aws() -> None:
    d = classify_edge([], [ip("13.33.1.1", asn=16509, rng=CLOUDFRONT)], {"x-amz-cf-id": "x"})
    assert d.value == "aws"
    assert d.evidence == ["range:aws/global/CLOUDFRONT", "header:x-amz-cf-id"]


def test_ipv4_addresses_decide_when_families_disagree() -> None:
    # amazon.com in run 1: one IPv4 address on CloudFront, five IPv6 addresses on Akamai. The
    # probes connect over IPv4, so CloudFront is the edge and Akamai stays in the evidence.
    ips = [ip("18.239.65.161", asn=16509, rng=CLOUDFRONT)] + [
        ip(f"2a02:26f0:fd00:18{n}::3bd4", asn=20940) for n in range(5)
    ]
    d = classify_edge([], ips, {"x-amz-cf-id": "x"})
    assert d.value == "aws"
    assert d.evidence[0] == "range:aws/global/CLOUDFRONT"
    assert "also:akamai:asn:20940" in d.evidence
    assert classify_hosting(ips).provider == "aws"


def test_cdn_in_front_of_a_platform_keeps_the_platform_as_evidence() -> None:
    d = classify_edge([], [ip("104.16.1.1", rng=CLOUDFLARE_RANGE)], {"cf-ray": "1", "x-vercel-id": "bom1::abc"})
    assert d.value == "cloudflare"
    assert "also:vercel:header:x-vercel-id" in d.evidence


def test_headers_decide_when_addresses_say_nothing() -> None:
    isp_cache = classify_edge([], [ip("49.44.1.1", asn=55836)], {"server": "AkamaiGHost"})
    assert (isp_cache.value, isp_cache.evidence) == ("akamai", ["header:server=AkamaiGHost"])
    vercel = classify_edge([], [ip("3.4.5.6", asn=16509, rng=EC2_US_EAST_1)], {"x-vercel-id": "iad1::x"})
    assert vercel.value == "vercel"


def test_header_only_cdn_outranks_header_only_platform() -> None:
    d = classify_edge([], [ip("49.44.1.1", asn=55836)], {"x-vercel-id": "x", "akamai-grn": "0.1"})
    assert d.value == "akamai"


def test_github_pages_server_header_is_case_sensitive() -> None:
    assert classify_edge([], [ip("140.82.112.3", asn=36459)], {"server": "github.com"}).value is None
    assert classify_edge([], [ip("140.82.112.3", asn=36459)], {"server": "GitHub.com"}).value == "github"


def test_dns_only_and_origin_cnames_are_not_edges() -> None:
    chain = ["foo.trafficmanager.net", "foo.azurewebsites.net"]
    assert classify_edge(chain, [ip("20.1.1.1", asn=8075)], {}).value is None
    elb = ["app-123.us-east-1.elb.amazonaws.com"]
    assert classify_edge(elb, [ip("3.5.1.9", asn=16509, rng=EC2_US_EAST_1)], {"server": "nginx"}).value is None


def test_azure_front_door_cname_and_google_front_end_header() -> None:
    afd = ["contoso.azurefd.net", "contoso.t-0001.t-msedge.net"]
    assert classify_edge(afd, [ip("13.107.246.40", asn=8075)], {}).value == "azure"
    assert classify_edge([], [ip("34.1.1.1", asn=396982)], {"via": "1.1 google"}).value == "gcp"


# Hosting --------------------------------------------------------------------------------------------


def test_hosting_prefers_published_ranges_with_region() -> None:
    h = classify_hosting([ip("3.5.1.9", asn=16509, rng=EC2_US_EAST_1), ip("3.5.1.10", asn=16509, rng=EC2_US_EAST_1)])
    assert (h.provider, h.region, h.evidence) == ("aws", "us-east-1", ["range:aws/us-east-1/EC2"])


def test_hosting_falls_back_to_asn_then_other() -> None:
    assert classify_hosting([ip("88.198.1.1", asn=24940)]).provider == "hetzner"
    other = classify_hosting([ip("207.45.72.1", asn=64512)])
    assert (other.provider, other.region, other.evidence) == ("other", None, ["asn:64512"])
    assert classify_hosting([ip("192.0.2.1")]).provider is None
    assert classify_hosting([]).provider is None


def test_hosting_majority_and_ipv4_first() -> None:
    ips = [
        ip("1.1.1.1", asn=13335),
        ip("8.8.8.8", asn=15169),
        ip("8.8.4.4", asn=15169),
        ip("2001:db8::1", asn=16509),
        ip("2001:db8::2", asn=16509),
        ip("2001:db8::3", asn=16509),
    ]
    assert classify_hosting(ips).provider == "gcp"


# Mail -----------------------------------------------------------------------------------------------


def test_mx_most_preferred_exchanger_decides() -> None:
    records = [(10, "mxa-001.pphosted.com"), (10, "mxb-001.pphosted.com"), (20, "aspmx.l.google.com.")]
    d = classify_mx(records)
    assert (d.value, d.evidence) == ("proofpoint", ["mx:*.pphosted.com"])


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([(1, "aspmx.l.google.com"), (5, "alt1.aspmx.l.google.com")], "google-workspace"),
        ([(0, "contoso-com.mail.protection.outlook.com")], "microsoft-365"),
        ([(10, "inbound-smtp.us-east-1.amazonaws.com")], "amazon-ses"),
        ([(5, "route1.mx.cloudflare.net")], "cloudflare-email-routing"),
        ([(10, "mx.yandex.net")], "yandex"),
        ([(10, "mxbiz1.qq.com")], "tencent"),
        ([(10, "mail.example.org")], "other"),
        ([], "none"),
        ([(0, "")], "none"),
        (None, None),
    ],
)
def test_mx_providers(records: list[tuple[int, str]] | None, expected: str | None) -> None:
    assert classify_mx(records).value == expected


def test_null_mx_is_reported_as_such() -> None:
    assert classify_mx([(0, ".")]).evidence == ["mx:null"]


# Certificates ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("issuer", "expected"),
    [
        ({"O": "Let's Encrypt", "CN": "R11"}, "Let's Encrypt"),
        ({"O": "Google Trust Services", "CN": "WE1"}, "Google Trust Services"),
        ({"O": "DigiCert Inc", "CN": "GeoTrust TLS RSA CA G1"}, "DigiCert"),
        ({"O": "Sectigo Limited", "CN": "Sectigo RSA Domain Validation Secure Server CA"}, "Sectigo"),
        ({"O": "Amazon", "CN": "Amazon RSA 2048 M02"}, "Amazon"),
        ({"O": "Microsoft Corporation", "CN": "Microsoft Azure RSA TLS Issuing CA 03"}, "Microsoft"),
        ({"O": "Starfield Technologies, Inc.", "CN": "Starfield Secure Certificate Authority - G2"}, "GoDaddy"),
        ({"O": "GlobalSign nv-sa", "CN": "GlobalSign GCC R6 AlphaSSL CA 2023"}, "GlobalSign"),
        ({"O": "cPanel, Inc.", "CN": "cPanel, Inc. Certification Authority"}, "cPanel"),
        ({"O": "CloudFlare, Inc.", "CN": "CloudFlare Inc ECC CA-2"}, "Cloudflare"),
        ({"O": "Cybertrust Japan Co., Ltd.", "CN": "Cybertrust Japan SureServer CA G4"}, "Cybertrust Japan"),
        ({"O": "SwissSign AG", "CN": "SwissSign RSA TLS DV ICA 2022 - 1"}, "SwissSign"),
        ({"O": "InCommon", "CN": "InCommon RSA Server CA 2"}, "Internet2 (InCommon)"),
        ({"CN": "localhost"}, "localhost"),
        ({}, None),
        (None, None),
    ],
)
def test_issuer_normalization(issuer: dict[str, str] | None, expected: str | None) -> None:
    assert classify_issuer(issuer).value == expected


def test_issuer_evidence_records_the_raw_names() -> None:
    assert classify_issuer({"O": "Let's Encrypt", "CN": "E6"}).evidence == ["issuer:O=Let's Encrypt,CN=E6"]
