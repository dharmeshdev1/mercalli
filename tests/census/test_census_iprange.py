import gzip

import orjson
import pytest

from mercalli.census.iprange import RangeInfo, RangeTable, parse_ip
from mercalli.census.refdata import (
    ReferenceData,
    merge_info,
    parse_aws,
    parse_azure,
    parse_digitalocean,
    parse_gcp,
    parse_google,
    parse_iptoasn,
)


def test_parse_ip_versions() -> None:
    assert parse_ip("10.0.0.1") == (4, 0x0A000001)
    assert parse_ip("2001:db8::1") == (6, 0x20010DB8 << 96 | 1)


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        ("9.255.255.255", None),
        ("10.0.0.0", "a"),
        ("10.0.0.255", "a"),
        ("10.0.1.0", None),
        ("2001:db7:ffff:ffff:ffff:ffff:ffff:ffff", None),
        ("2001:db8::", "b"),
        ("2001:db8:ffff:ffff:ffff:ffff:ffff:ffff", "b"),
        ("2001:db9::", None),
    ],
)
def test_lookup_boundaries_v4_and_v6(ip: str, expected: str | None) -> None:
    table = RangeTable.from_prefixes([("10.0.0.0/24", "a"), ("2001:db8::/32", "b")])
    assert table.lookup_ip(ip) == expected


def test_nested_prefixes_resolve_to_the_innermost() -> None:
    table = RangeTable.from_prefixes(
        [
            ("10.0.0.0/16", "outer"),
            ("10.0.0.0/24", "first-child"),
            ("10.0.5.0/24", "second-child"),
            ("10.0.5.128/25", "grandchild"),
        ]
    )
    assert table.lookup_ip("10.0.0.10") == "first-child"
    assert table.lookup_ip("10.0.1.0") == "outer"
    assert table.lookup_ip("10.0.4.255") == "outer"
    assert table.lookup_ip("10.0.5.0") == "second-child"
    assert table.lookup_ip("10.0.5.127") == "second-child"
    assert table.lookup_ip("10.0.5.128") == "grandchild"
    assert table.lookup_ip("10.0.5.255") == "grandchild"
    assert table.lookup_ip("10.0.6.0") == "outer"
    assert table.lookup_ip("10.0.255.255") == "outer"
    assert table.lookup_ip("10.1.0.0") is None


def test_ipv4_space_edges() -> None:
    table = RangeTable.from_prefixes([("0.0.0.0/8", "low"), ("255.255.255.255/32", "high")])
    assert table.lookup_ip("0.0.0.0") == "low"
    assert table.lookup_ip("255.255.255.255") == "high"
    assert table.lookup_ip("255.255.255.254") is None


def test_duplicate_prefix_uses_merge() -> None:
    table = RangeTable.from_prefixes([("10.0.0.0/8", "x"), ("10.0.0.0/8", "y")], merge=lambda a, b: a + b)
    assert table.lookup_ip("10.1.2.3") == "xy"


def test_from_intervals_for_asn_dumps() -> None:
    table = RangeTable.from_intervals([(200, 300, 2), (100, 199, 1)], [(1 << 100, (1 << 100) + 9, 3)])
    assert table.lookup(4, 150) == 1
    assert table.lookup(4, 300) == 2
    assert table.lookup(4, 301) is None
    assert table.lookup(6, (1 << 100) + 9) == 3


def test_merge_info_keeps_specific_service_region_and_edge() -> None:
    generic = RangeInfo("aws", "aws", None, "AMAZON")
    edge = RangeInfo("aws", "aws", None, "CLOUDFRONT", edge=True)
    regional = RangeInfo("aws", "aws", "us-east-1", "EC2")
    assert merge_info(generic, edge) == RangeInfo("aws", "aws", None, "CLOUDFRONT", edge=True)
    assert merge_info(regional, generic) == RangeInfo("aws", "aws", "us-east-1", "EC2")


def _table(parsed: tuple[list[tuple[str, RangeInfo]], str | None]) -> RangeTable[RangeInfo]:
    return RangeTable.from_prefixes(parsed[0], merge_info)


def test_parse_aws_cloudfront_global_and_regional() -> None:
    doc = {
        "createDate": "2026-11-03-09-27-05",
        "prefixes": [
            {"ip_prefix": "13.32.0.0/15", "region": "GLOBAL", "service": "AMAZON"},
            {"ip_prefix": "13.32.0.0/15", "region": "GLOBAL", "service": "CLOUDFRONT"},
            {"ip_prefix": "3.5.0.0/16", "region": "us-east-1", "service": "AMAZON"},
            {"ip_prefix": "3.5.1.0/24", "region": "us-east-1", "service": "EC2"},
            {"ip_prefix": "3.6.0.0/24", "region": "us-west-2", "service": "CLOUDFRONT_ORIGIN_FACING"},
        ],
        "ipv6_prefixes": [{"ipv6_prefix": "2600:9000::/28", "region": "GLOBAL", "service": "CLOUDFRONT"}],
    }
    parsed = parse_aws([orjson.dumps(doc)])
    assert parsed[1] == "2026-11-03-09-27-05"
    table = _table(parsed)
    assert table.lookup_ip("13.33.1.1") == RangeInfo("aws", "aws", None, "CLOUDFRONT", edge=True)
    assert table.lookup_ip("3.5.1.9") == RangeInfo("aws", "aws", "us-east-1", "EC2")
    assert table.lookup_ip("3.5.2.9") == RangeInfo("aws", "aws", "us-east-1", "AMAZON")
    assert table.lookup_ip("3.6.0.1").edge is False
    assert table.lookup_ip("2600:9000::1").edge is True


def test_parse_azure_regions_and_front_door() -> None:
    doc = {
        "changeNumber": 424,
        "values": [
            {
                "name": "AzureCloud",
                "properties": {"region": "", "systemService": "", "addressPrefixes": ["20.0.0.0/8"]},
            },
            {
                "name": "AzureCloud.eastus",
                "properties": {"region": "eastus", "systemService": "", "addressPrefixes": ["20.1.0.0/16"]},
            },
            {
                "name": "Storage.EastUS",
                "properties": {"region": "eastus", "systemService": "AzureStorage", "addressPrefixes": ["20.1.2.0/24"]},
            },
            {
                "name": "AzureFrontDoor.Frontend",
                "properties": {"region": "", "systemService": "", "addressPrefixes": ["13.107.246.0/24"]},
            },
            {
                "name": "AzureFrontDoor.Backend",
                "properties": {"region": "", "systemService": "", "addressPrefixes": ["147.243.0.0/16"]},
            },
        ],
    }
    parsed = parse_azure([b"\xef\xbb\xbf" + orjson.dumps(doc)])
    assert parsed[1] == "changeNumber 424"
    table = _table(parsed)
    assert table.lookup_ip("20.1.2.3") == RangeInfo("azure", "azure", "eastus", "AzureStorage")
    assert table.lookup_ip("20.1.3.3") == RangeInfo("azure", "azure", "eastus", "AzureCloud")
    assert table.lookup_ip("20.9.0.1") == RangeInfo("azure", "azure", None, "AzureCloud")
    assert table.lookup_ip("13.107.246.40").edge is True
    assert table.lookup_ip("147.243.1.1").edge is False


def test_parse_gcp_global_scope_is_edge() -> None:
    doc = {
        "creationTime": "2026-11-03T00:14:03",
        "prefixes": [
            {"ipv4Prefix": "34.120.0.0/16", "service": "Google Cloud", "scope": "global"},
            {"ipv6Prefix": "2600:1900::/35", "service": "Google Cloud", "scope": "us-central1"},
        ],
    }
    table = _table(parse_gcp([orjson.dumps(doc)]))
    assert table.lookup_ip("34.120.9.9") == RangeInfo("gcp", "gcp", None, None, edge=True)
    assert table.lookup_ip("2600:1900::5") == RangeInfo("gcp", "gcp", "us-central1", None)


def test_parse_digitalocean_geofeed_city() -> None:
    feed = b"5.101.96.0/21,NL,NL-NH,Amsterdam,1098 XH\n24.144.64.0/22,US,US-NJ,North Bergen,07047\n"
    table = _table(parse_digitalocean([feed]))
    assert table.lookup_ip("24.144.65.1") == RangeInfo("digitalocean", "digitalocean", "north-bergen", None)


def test_parse_iptoasn_skips_unrouted_space() -> None:
    dump = (
        "1.0.0.0\t1.0.0.255\t13335\tUS\tCLOUDFLARENET - Cloudflare, Inc.\n"
        "1.0.1.0\t1.0.3.255\t0\tNone\tNot routed\n"
        "2600:1f00::\t2600:1fff:ffff:ffff:ffff:ffff:ffff:ffff\t16509\tUS\tAMAZON-02 - Amazon.com, Inc.\n"
    )
    table, orgs = parse_iptoasn(gzip.compress(dump.encode()))
    assert table.lookup_ip("1.0.0.255") == 13335
    assert table.lookup_ip("1.0.1.1") is None
    assert table.lookup_ip("2600:1f18::1") == 16509
    assert orgs[16509] == "AMAZON-02 - Amazon.com, Inc."
    assert 0 not in orgs


def test_reference_lookup_prefers_cloud_ranges_over_google_catch_all() -> None:
    cloud = _table(parse_gcp([orjson.dumps({"prefixes": [{"ipv4Prefix": "34.1.0.0/16", "scope": "us-east1"}]})]))
    google = _table(parse_google([orjson.dumps({"prefixes": [{"ipv4Prefix": "34.0.0.0/8"}]})]))
    asn = RangeTable.from_intervals([(parse_ip("34.0.0.0")[1], parse_ip("34.255.255.255")[1], 15169)], [])
    ref = ReferenceData([("gcp", cloud), ("google", google)], asn, {15169: "GOOGLE - Google LLC"}, {})
    regional = ref.lookup("34.1.2.3")
    assert regional.range == RangeInfo("gcp", "gcp", "us-east1", None)
    assert (regional.asn, regional.asn_org) == (15169, "GOOGLE - Google LLC")
    assert ref.lookup("34.200.0.1").range == RangeInfo("gcp", "google", None, None, edge=True)
    assert ref.lookup("8.8.8.8").range is None
