"""The providers the census can recognize, and the signals that give each one away.

Ids that name a service in the Mercalli catalog are the catalog's ids, so census rows join the
dependency graph without a mapping table. A company gets one id across DNS, edge, hosting and
certificates, because one company's bad day tends to take its products down together: Route 53
and CloudFront are `aws`, Azure Front Door is `azure`, Google's front end is `gcp`. Brands that
run separate infrastructure and publish their own status page keep their own id, the same choice
the catalog makes for GitHub, Heroku, Firebase and Linode. Email products follow the names the
census reports (`google-workspace`, `microsoft-365`) because a mail outage rarely tracks the
parent cloud.

Matching rules:

* Name suffixes (`ns`, `edge_cnames`, `mx`) match the name itself and everything below it.
  `Pattern` covers the few families a suffix cannot express, such as Route 53's numbered
  `awsdns-NN` domains.
* `edge_cnames` lists CNAME targets that hand the request to the provider's edge or platform.
  Targets that only balance DNS (Azure Traffic Manager, Akamai GTM) or point at an origin load
  balancer (ELB, App Service) are deliberately absent.
* `edge_prefixes` are the documented anycast front doors of multi-tenant platforms, needed
  because those addresses sit inside another network's ASN (Shopify inside Cloudflare, GitHub
  Pages inside Fastly).
* `asns` attribute hosting. `edge_asn` marks networks where every customer-facing address is an
  edge node, so the ASN alone proves an edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

SELF = "self"
OTHER = "other"
NONE = "none"
PSEUDO = frozenset({SELF, OTHER, NONE})

# Providers whose own published IP ranges the reference data loads (see refdata.py).
RANGE_PROVIDERS = frozenset({"aws", "gcp", "azure", "oracle-cloud", "cloudflare", "fastly", "digitalocean"})


@dataclass(frozen=True)
class Pattern:
    """A regular expression on a hostname, for families a suffix cannot describe."""

    regex: str
    label: str


@dataclass(frozen=True)
class Header:
    """A response header that gives a provider away, optionally with a value pattern."""

    name: str
    pattern: str | None = None
    case_sensitive: bool = False


type NameRule = str | Pattern


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    platform: bool = False  # multi-tenant site platform, as opposed to a network
    ns: tuple[NameRule, ...] = ()
    edge_cnames: tuple[NameRule, ...] = ()
    headers: tuple[Header, ...] = ()
    edge_prefixes: tuple[str, ...] = ()
    asns: tuple[int, ...] = ()
    edge_asn: bool = False
    mx: tuple[NameRule, ...] = ()
    issuers: tuple[str, ...] = ()  # regexes on the certificate issuer organization or common name
    ca_name: str | None = None  # how the company signs certificates, when that differs from `name`

    @property
    def kinds(self) -> frozenset[str]:
        kinds: set[str] = set()
        if self.ns:
            kinds.add("dns")
        if self.edge_cnames or self.headers or self.edge_prefixes or self.edge_asn:
            kinds.add("edge")
        if self.asns or self.id in RANGE_PROVIDERS:
            kinds.add("hosting")
        if self.mx:
            kinds.add("email")
        if self.issuers:
            kinds.add("ca")
        return frozenset(kinds)


PROVIDERS: tuple[Provider, ...] = (
    # Clouds. These publish IP ranges, so hosting and region come from the range data first.
    Provider(
        id="aws",
        name="Amazon Web Services",
        ns=(
            Pattern(r"\.awsdns-\d+\.(com|net|org|co\.uk)$", "*.awsdns-NN.*"),
            "amzndns.com",
            "amzndns.net",
            "amzndns.org",
            "amzndns.co.uk",
        ),
        edge_cnames=("cloudfront.net", "amplifyapp.com"),
        headers=(
            Header("x-amz-cf-id"),
            Header("x-amz-cf-pop"),
            Header("via", r"\(CloudFront\)"),
            Header("server", r"^CloudFront$"),
        ),
        asns=(16509, 14618, 7224, 8987, 21664, 36263, 62785, 214101),
        issuers=(r"^Amazon$",),
        ca_name="Amazon",
    ),
    Provider(
        id="gcp",
        name="Google Cloud",
        ns=("googledomains.com", "google.com", "zdns.google"),
        edge_cnames=(
            "googlehosted.com",
            "ghs.google.com",
            "l.google.com",
            "appspot.com",
            "run.app",
            "googleusercontent.com",
            "googleapis.com",
        ),
        headers=(
            Header("via", r"\b1\.1 google\b"),
            Header("server", r"^(gws|ESF|sffe|GSE|Google Frontend)$"),
        ),
        asns=(
            15169,
            396982,
            19527,
            36040,
            43515,
            139070,
            139190,
            16550,
            36383,
            36384,
            36411,
            395973,
            394089,
            396178,
            41264,
            214609,
            214611,
        ),
        issuers=(r"^Google Trust Services",),
        ca_name="Google Trust Services",
    ),
    Provider(
        id="azure",
        name="Microsoft Azure",
        ns=("azure-dns.com", "azure-dns.net", "azure-dns.org", "azure-dns.info", "msft.net"),
        edge_cnames=("azurefd.net", "azureedge.net", "msedge.net"),
        headers=(Header("x-azure-ref"), Header("x-msedge-ref"), Header("x-fd-int-roxy-purgeid")),
        asns=(8075, 8068, 8069, 8070, 12076, 3598, 6584, 63314, 45139, 35106, 5761),
        issuers=(r"^Microsoft",),
        ca_name="Microsoft",
    ),
    Provider(
        id="oracle-cloud",
        name="Oracle Cloud",
        ns=("oraclecloud.net", "dynect.net"),
        asns=(31898, 1215, 1217, 20037, 20054, 395010, 54253, 393773, 11049, 14544, 46558, 6142),
    ),
    Provider(id="ibm-cloud", name="IBM Cloud", ns=("softlayer.com",), asns=(36351,)),
    Provider(
        id="alibaba",
        name="Alibaba Cloud",
        ns=("alidns.com", "hichina.com", "alibabadns.com", "aliyun.com"),
        edge_cnames=(
            "alikunlun.com",
            "alikunlun.net",
            Pattern(r"\.kunlun[a-z0-9]*\.com$", "*.kunlun*.com"),
            "alicdn.com",
            "tbcache.com",
            "cdngslb.com",
        ),
        headers=(Header("eagleid"), Header("x-swift-cachetime"), Header("x-swift-savetime")),
        asns=(45102, 37963, 24429, 134963, 203513),
        mx=("mxhichina.com", "aliyun.com", "alibaba-inc.com"),
    ),
    Provider(
        id="tencent",
        name="Tencent Cloud",
        ns=("dnspod.net", "dnspod.com", "dnsv2.com", "dnsv3.com", "dnsv4.com", "dnsv5.com", "qq.com"),
        edge_cnames=(
            "cdn.dnsv1.com",
            "cdn.dnsv1.com.cn",
            "dsa.dnsv1.com",
            "qcloudcdn.com",
            "cdntip.com",
            Pattern(r"\.eo\.dnse\d+\.com$", "*.eo.dnseN.com"),
        ),
        headers=(Header("x-nws-log-uuid"), Header("eo-log-uuid")),
        asns=(132203, 45090, 133478, 137876, 139341),
        mx=("qq.com",),
    ),
    Provider(
        id="huawei",
        name="Huawei Cloud",
        ns=("hwclouds-dns.com", "hwclouds-dns.net", "huaweicloud-dns.cn", "huaweicloud-dns.com", "huaweicloud-dns.org"),
        edge_cnames=(Pattern(r"\.cdnhwc\d+\.com$", "*.cdnhwcN.com"),),
        headers=(Header("server", r"^CloudWAF"),),
        asns=(136907, 55990, 131444, 141180, 151610, 200756, 206798),
    ),
    Provider(
        id="baidu",
        name="Baidu",
        edge_cnames=("bdydns.com", "jomodns.com", "shifen.com"),
        asns=(55967, 38365),
    ),
    Provider(
        id="bytedance",
        name="ByteDance (Volcano Engine)",
        headers=(Header("server", r"^volc-dcdn"),),
        asns=(396986, 138699, 137718, 11983),
        mx=("feishu.cn", "larksuite.com"),
    ),
    Provider(
        id="yandex",
        name="Yandex",
        ns=("yandex.net", "yandex.ru"),
        asns=(13238, 200350, 202611, 210656, 215013),
        mx=("yandex.net", "yandex.ru"),
    ),
    # Edge networks and security proxies.
    Provider(
        id="cloudflare",
        name="Cloudflare",
        # foundationdns.* serves Cloudflare's enterprise Foundation DNS zones.
        ns=("ns.cloudflare.com", "cloudflare.com", "foundationdns.com", "foundationdns.net", "foundationdns.org"),
        edge_cnames=("cdn.cloudflare.net", "cloudflare.net", "pages.dev", "workers.dev"),
        headers=(Header("cf-ray"), Header("server", r"^cloudflare$")),
        asns=(
            13335,
            209242,
            132892,
            202623,
            203898,
            139242,
            14789,
            22061,
            394536,
            395747,
            400095,
            402542,
        ),
        edge_asn=True,
        issuers=(r"^Cloudflare",),
    ),
    Provider(
        id="fastly",
        name="Fastly",
        edge_cnames=("fastly.net", "fastlylb.net"),
        headers=(Header("x-served-by", r"^cache-"), Header("x-fastly-request-id"), Header("fastly-debug-digest")),
        asns=(54113, 394192),
        edge_asn=True,
        issuers=(r"^Certainly",),
        ca_name="Certainly",
    ),
    Provider(
        id="akamai",
        name="Akamai",
        ns=("akam.net", "akamaiedge.net", "akadns.net"),
        edge_cnames=(
            "edgekey.net",
            "edgesuite.net",
            "akamaiedge.net",
            "akamai.net",
            "akamaized.net",
            "akamaihd.net",
            "akamaitechnologies.com",
            "edgekey-staging.net",
            "edgesuite-staging.net",
            "akamaiedge-staging.net",
        ),
        headers=(
            Header("server", r"^Akamai(GHost|NetStorage)"),
            Header("akamai-grn"),
            Header("x-akamai-transformed"),
            Header("akamai-cache-status"),
            Header("x-akamai-request-id"),
            Header("server-timing", r"\bak_p\b"),
        ),
        asns=(20940, 16625, 12222, 21342, 24319, 31108, 33905, 34164, 35994, 36183, 393560, 32787, 49846, 213120),
        edge_asn=True,
    ),
    Provider(
        id="bunny",
        name="Bunny",
        ns=("bunny.net",),
        edge_cnames=("b-cdn.net", "bunnycdn.com"),
        headers=(Header("server", r"^BunnyCDN"), Header("cdn-pullzone"), Header("cdn-requestid")),
        asns=(200325,),
        edge_asn=True,
    ),
    Provider(
        id="imperva",
        name="Imperva",
        edge_cnames=("incapdns.net", "impervadns.net"),
        headers=(Header("x-iinfo"), Header("x-cdn", r"Imperva|Incapsula")),
        asns=(19551,),
        edge_asn=True,
    ),
    Provider(
        id="sucuri",
        name="Sucuri",
        headers=(Header("x-sucuri-id"), Header("x-sucuri-cache"), Header("server", r"^Sucuri")),
        asns=(30148,),
        edge_asn=True,
    ),
    Provider(
        id="gcore",
        name="Gcore",
        ns=("gcorelabs.net", "gcdn.services"),
        edge_cnames=("gcdn.co",),
        asns=(199524, 202422, 210559),
    ),
    Provider(
        id="edgio",
        name="Edgio (Edgecast, Limelight)",
        edge_cnames=(
            "edgecastcdn.net",
            "edgecastdns.net",
            "llnwd.net",
            "llnwi.net",
            "edgio.net",
            "edgio.link",
            "systemcdn.net",
            "phicdn.net",
        ),
        headers=(Header("server", r"^(ECAcc|ECS|ECD) \("), Header("server", r"^EdgePrism")),
        asns=(14210, 38621),
        edge_asn=True,
    ),
    Provider(id="keycdn", name="KeyCDN", edge_cnames=("kxcdn.com",), headers=(Header("server", r"^keycdn"),)),
    Provider(
        id="cdn77",
        name="CDN77",
        edge_cnames=("cdn77.org",),
        headers=(Header("server", r"^CDN77"),),
        asns=(60068, 211612, 212238),
        edge_asn=True,
    ),
    Provider(
        id="stackpath",
        name="StackPath",
        edge_cnames=("stackpathdns.com", "stackpathcdn.com", "hwcdn.net", "netdna-cdn.com", "netdna-ssl.com"),
        headers=(Header("x-hw"),),
    ),
    Provider(id="cachefly", name="CacheFly", edge_cnames=("cachefly.net",), asns=(30081,), edge_asn=True),
    Provider(
        id="ddos-guard",
        name="DDoS-Guard",
        headers=(Header("server", r"^ddos-guard$"),),
        asns=(57724, 59692),
        edge_asn=True,
    ),
    Provider(
        id="qrator",
        name="Qrator",
        headers=(Header("server", r"^QRATOR$"),),
        asns=(200449, 209671, 211112, 51115),
        edge_asn=True,
    ),
    Provider(
        id="arvancloud",
        name="ArvanCloud",
        ns=("arvancdn.ir", "arvancdn.com"),
        headers=(Header("server", r"^ArvanCloud"),),
        asns=(202468, 205585, 208006),
    ),
    Provider(
        id="azion",
        name="Azion",
        edge_cnames=("azioncdn.net", "azionedge.net"),
        headers=(Header("server", r"^azion"),),
        asns=(52580,),
        edge_asn=True,
    ),
    Provider(id="medianova", name="Medianova", edge_cnames=("mncdn.com",), asns=(21245,), edge_asn=True),
    Provider(
        id="cdnetworks",
        name="CDNetworks",
        edge_cnames=("cdngc.net", "gccdn.net", "panthercdn.com", "cdnetworks.net", "cdnga.net"),
        asns=(36408, 38107, 204720),
        edge_asn=True,
    ),
    Provider(
        id="wangsu",
        name="Wangsu (ChinaNetCenter)",
        edge_cnames=(
            "wscdns.com",
            "wsglb0.com",
            "wsdvs.com",
            "ourwebcdn.com",
            "ourglb0.com",
            "lxdns.com",
            "chinanetcenter.com",
            "cdn20.com",
            "wswebcdn.com",
            "wscloudcdn.com",
        ),
        headers=(Header("x-ws-request-id"),),
        asns=(54994,),
    ),
    Provider(
        id="f5",
        name="F5 Distributed Cloud",
        edge_cnames=("ves.io",),
        headers=(Header("server", r"^volt-adc"),),
        asns=(35280,),
        edge_asn=True,
    ),
    Provider(id="servicepipe", name="ServicePipe", asns=(201706,), edge_asn=True),
    Provider(id="myra", name="Myra Security", headers=(Header("server", r"^myracloud"),)),
    Provider(id="sotoon", name="Sotoon", headers=(Header("server", r"^Sotoon"),)),
    # Multi-tenant site and application platforms.
    Provider(
        id="github",
        name="GitHub",
        platform=True,
        edge_cnames=("github.io",),
        edge_prefixes=("185.199.108.0/22", "2606:50c0:8000::/46"),
        # GitHub Pages sends "GitHub.com"; github.com itself sends a lowercase "github.com".
        headers=(Header("server", r"^GitHub\.com$", case_sensitive=True),),
        asns=(36459,),
    ),
    Provider(
        id="vercel",
        name="Vercel",
        platform=True,
        ns=("vercel-dns.com",),
        edge_cnames=("vercel-dns.com", "vercel.app", Pattern(r"\.vercel-dns-\d+\.com$", "*.vercel-dns-NNN.com")),
        edge_prefixes=("76.76.21.0/24",),
        headers=(Header("x-vercel-id"), Header("server", r"^Vercel$")),
    ),
    Provider(
        id="netlify",
        name="Netlify",
        platform=True,
        edge_cnames=("netlify.app", "netlify.com", "netlifyglobalcdn.com"),
        edge_prefixes=("75.2.60.5/32",),
        headers=(Header("x-nf-request-id"), Header("server", r"^Netlify$")),
    ),
    Provider(
        id="flyio",
        name="Fly.io",
        platform=True,
        ns=("flydns.net",),
        edge_cnames=("fly.dev",),
        headers=(Header("fly-request-id"), Header("server", r"^Fly/"), Header("via", r"\bfly\.io\b")),
        asns=(40509,),
        edge_asn=True,
    ),
    Provider(
        id="firebase",
        name="Firebase Hosting",
        platform=True,
        edge_cnames=("web.app", "firebaseapp.com"),
        edge_prefixes=("199.36.158.100/32",),
    ),
    Provider(
        id="heroku",
        name="Heroku",
        platform=True,
        edge_cnames=("herokudns.com", "herokuapp.com", "herokussl.com"),
        headers=(Header("via", r"\bvegur\b"),),
    ),
    Provider(
        id="render",
        name="Render",
        platform=True,
        edge_cnames=("onrender.com",),
        headers=(Header("rndr-id"), Header("x-render-origin-server")),
        asns=(397273,),
        edge_asn=True,
    ),
    Provider(
        id="railway",
        name="Railway",
        platform=True,
        edge_cnames=("railway.app",),
        headers=(Header("x-railway-request-id"),),
        asns=(400940,),
        edge_asn=True,
    ),
    Provider(id="supabase", name="Supabase", platform=True, edge_cnames=("supabase.co",)),
    Provider(
        id="shopify",
        name="Shopify",
        platform=True,
        edge_cnames=("myshopify.com",),
        edge_prefixes=("23.227.38.0/24",),
        headers=(Header("x-shopid"), Header("x-shopify-stage"), Header("powered-by", r"^Shopify")),
        asns=(62679,),
    ),
    Provider(
        id="squarespace",
        name="Squarespace",
        platform=True,
        edge_cnames=("squarespace.com",),
        headers=(Header("server", r"^Squarespace"),),
        asns=(53831,),
        edge_asn=True,
    ),
    Provider(
        id="wix",
        name="Wix",
        platform=True,
        ns=("wixdns.net",),
        edge_cnames=("wixdns.net",),
        headers=(Header("x-wix-request-id"), Header("server", r"^Pepyaka")),
        asns=(58182, 14353),
        edge_asn=True,
    ),
    Provider(id="webflow", name="Webflow", platform=True, edge_cnames=("webflow.com", "webflow.io")),
    Provider(
        id="automattic",
        name="Automattic (WordPress.com, WordPress VIP)",
        platform=True,
        ns=("wordpress.com",),
        edge_cnames=("wordpress.com", "wpvip.com", "go-vip.co", "go-vip.net", "wpcomstaging.com"),
        headers=(
            Header("host-header", r"^a9130478a60e5f9135f765b23f26593b$"),
            Header("x-powered-by", r"WordPress VIP|WPVIP"),
        ),
        asns=(2635,),
        edge_asn=True,
    ),
    Provider(
        id="wpengine",
        name="WP Engine",
        platform=True,
        edge_cnames=("wpengine.com", "wpenginepowered.com"),
        headers=(Header("x-powered-by", r"WP Engine"), Header("wpe-backend")),
    ),
    Provider(
        id="kinsta",
        name="Kinsta",
        platform=True,
        edge_cnames=("kinsta.cloud",),
        headers=(Header("ki-cache-type"), Header("ki-edge"), Header("x-kinsta-cache")),
    ),
    Provider(
        id="pantheon",
        name="Pantheon",
        platform=True,
        edge_cnames=("pantheonsite.io", "pantheon.io"),
        headers=(Header("x-pantheon-styx-hostname"), Header("x-styx-req-id")),
    ),
    Provider(
        id="hubspot",
        name="HubSpot CMS",
        platform=True,
        edge_cnames=("hubspot.net", "hs-sites.com", "hs-sites-eu1.com", "hubspotpagebuilder.com"),
    ),
    Provider(id="framer", name="Framer", platform=True, edge_cnames=("framer.app", "framer.website")),
    Provider(id="ghost", name="Ghost", platform=True, edge_cnames=("ghost.io",)),
    Provider(id="substack", name="Substack", platform=True, edge_cnames=("substack-custom-domains.com",)),
    Provider(id="deno", name="Deno Deploy", platform=True, edge_cnames=("deno.dev",)),
    Provider(id="replit", name="Replit", platform=True, edge_cnames=("replit.app", "repl.co")),
    Provider(id="upsun", name="Upsun", platform=True, edge_cnames=("platformsh.site",)),
    # Hosting networks.
    Provider(id="linode", name="Linode (Akamai Connected Cloud)", ns=("linode.com",), asns=(63949,)),
    Provider(id="digitalocean", name="DigitalOcean", ns=("digitalocean.com",), asns=(14061, 393406, 46652)),
    Provider(
        id="hetzner",
        name="Hetzner",
        ns=("hetzner.com", "hetzner.de", "your-server.de", "second-ns.de", "second-ns.com"),
        asns=(24940, 213230, 212317, 215859),
    ),
    Provider(
        id="ovhcloud",
        name="OVHcloud",
        ns=("ovh.net", "ovh.ca", "anycast.me"),
        asns=(16276, 35540),
        mx=("ovh.net",),
    ),
    Provider(id="vultr", name="Vultr", ns=("vultr.com",), asns=(20473, 46407)),
    Provider(id="scaleway", name="Scaleway", ns=("scaleway.com",), asns=(12876, 202023, 29447, 205537)),
    Provider(id="backblaze", name="Backblaze", asns=(40401,)),
    Provider(id="equinix-metal", name="Equinix Metal", asns=(54825,)),
    Provider(id="hostinger", name="Hostinger", ns=("dns-parking.com",), asns=(47583, 204915)),
    Provider(
        id="godaddy",
        name="GoDaddy",
        ns=("domaincontrol.com",),
        asns=(26496, 398101),
        mx=("secureserver.net",),
        issuers=(r"^GoDaddy", r"^Starfield"),
    ),
    Provider(
        id="ionos",
        name="IONOS",
        ns=("ui-dns.com", "ui-dns.de", "ui-dns.org", "ui-dns.biz"),
        asns=(8560,),
        mx=("ionos.com", "ionos.de", "kundenserver.de"),
    ),
    Provider(
        id="namecheap",
        name="Namecheap",
        ns=("registrar-servers.com", "namecheaphosting.com"),
        asns=(22612,),
        # eforward*.registrar-servers.com is Namecheap's free mail forwarding.
        mx=("privateemail.com", "registrar-servers.com"),
    ),
    Provider(
        id="newfold", name="Newfold Digital (Bluehost, HostGator)", ns=("bluehost.com", "hostgator.com"), asns=(46606,)
    ),
    Provider(
        id="rackspace",
        name="Rackspace",
        ns=("rackspace.com", "stabletransit.com"),
        asns=(19994, 33070, 27357, 12200, 15395),
        mx=("emailsrvr.com",),
    ),
    Provider(
        id="leaseweb",
        name="Leaseweb",
        asns=(60781, 19148, 27411, 28753, 30633, 205544, 134351, 133752, 136988),
    ),
    Provider(id="contabo", name="Contabo", asns=(51167, 40021, 141995)),
    Provider(id="m247", name="M247", asns=(9009,)),
    Provider(id="zenlayer", name="Zenlayer", asns=(21859, 139625, 29752)),
    Provider(id="selectel", name="Selectel", ns=("selectel.org", "selectel.ru"), asns=(49505, 50340)),
    Provider(id="advancedhosters", name="AdvancedHosters", asns=(39572,)),
    Provider(id="timeweb", name="Timeweb", asns=(9123,)),
    Provider(id="dreamhost", name="DreamHost", ns=("dreamhost.com",), asns=(26347,)),
    Provider(id="liquidweb", name="Liquid Web", asns=(32244,)),
    Provider(id="inmotion", name="InMotion Hosting", asns=(54641,)),
    Provider(id="a2-hosting", name="A2 Hosting", asns=(55293,)),
    Provider(id="hostpapa", name="HostPapa", asns=(36352,)),
    Provider(id="strato", name="Strato", asns=(6724,)),
    Provider(id="aruba", name="Aruba", asns=(31034,)),
    Provider(id="netcup", name="netcup", asns=(197540,)),
    Provider(id="infomaniak", name="Infomaniak", asns=(29222,)),
    Provider(id="transip", name="TransIP", ns=("transip.net", "transip.nl", "transip.eu"), asns=(20857,)),
    Provider(
        id="gandi", name="Gandi", ns=("gandi.net",), asns=(29169, 203476), mx=("gandi.net",), issuers=(r"^Gandi",)
    ),
    # Companies that run their own networks for their own sites.
    Provider(id="meta", name="Meta", ns=("facebook.com", "instagram.com", "whatsapp.net"), asns=(32934, 54115, 63293)),
    Provider(id="apple", name="Apple", ns=("apple.com",), asns=(714, 6185), issuers=(r"^Apple",)),
    Provider(
        id="yahoo",
        name="Yahoo",
        ns=("yahoo.com", "yahoodns.net"),
        asns=(
            7233,
            10229,
            10230,
            10310,
            10880,
            14196,
            14776,
            14777,
            14778,
            14779,
            14780,
            16515,
            18293,
            23879,
            23926,
            26101,
            34010,
            36088,
            36646,
            36647,
            36752,
            43428,
            56173,
            203070,
            203219,
            203220,
        ),
        mx=("yahoodns.net",),
    ),
    Provider(id="x", name="X", ns=("twtrdns.net",), asns=(13414,)),
    Provider(id="wikimedia", name="Wikimedia", ns=("wikimedia.org",), asns=(14907,)),
    Provider(id="netflix", name="Netflix", asns=(2906, 40027)),
    Provider(id="salesforce", name="Salesforce", asns=(14340,)),
    Provider(id="vk", name="VK", ns=("mail.ru",), asns=(47541, 47764), mx=("mail.ru", "vk.com")),
    Provider(id="naver", name="Naver", asns=(23576,)),
    # Catalog services that serve their own sites from their own networks.
    Provider(id="dropbox", name="Dropbox", asns=(19679,)),
    Provider(id="notion", name="Notion", asns=(33191,)),
    Provider(id="roblox", name="Roblox", asns=(22697,)),
    Provider(id="zoom", name="Zoom", asns=(30103,)),
    Provider(id="servicenow", name="ServiceNow", asns=(16839,)),
    Provider(id="new-relic", name="New Relic", asns=(23467,)),
    Provider(id="qualys", name="Qualys", asns=(27385,)),
    Provider(id="quantcast", name="Quantcast", asns=(27281,)),
    Provider(id="bigcommerce", name="BigCommerce", asns=(399566,)),
    Provider(id="redhat", name="Red Hat", asns=(21785,)),
    Provider(id="dailymotion", name="Dailymotion", asns=(41690,)),
    Provider(id="wasabi", name="Wasabi", asns=(395717,)),
    Provider(id="squiz", name="Squiz", asns=(55532,)),
    Provider(id="constantcontact", name="Constant Contact", asns=(40444,)),
    Provider(id="getresponse", name="GetResponse", asns=(46469,)),
    # Managed DNS.
    Provider(id="ns1", name="NS1", ns=("nsone.net",)),
    Provider(
        id="dnsimple",
        name="DNSimple",
        ns=("dnsimple.com", "dnsimple-edge.net", "dnsimple-edge.org", "dnsimple-edge.com"),
    ),
    # Tiggee LLC is the company behind DNS Made Easy.
    Provider(id="dns-made-easy", name="DNS Made Easy", ns=("dnsmadeeasy.com",), asns=(16552,)),
    Provider(id="constellix", name="Constellix", ns=("constellix.com", "constellix.net")),
    Provider(
        id="ultradns",
        name="UltraDNS (Vercara)",
        asns=(12008,),
        ns=("ultradns.com", "ultradns.net", "ultradns.org", "ultradns.biz", "ultradns.info", "ultradns.co.uk"),
    ),
    Provider(id="porkbun", name="Porkbun", ns=("porkbun.com",)),
    Provider(id="easydns", name="easyDNS", ns=("easydns.com", "easydns.net", "easydns.org", "easydns.info")),
    Provider(id="markmonitor", name="MarkMonitor", ns=("markmonitor.com",)),
    Provider(id="csc", name="CSC", ns=("cscdns.net", "cscdns.uk")),
    Provider(id="cloudns", name="ClouDNS", ns=("cloudns.net",)),
    Provider(id="hurricane-electric", name="Hurricane Electric", ns=("he.net",)),
    Provider(id="name", name="Name.com", ns=("name.com",)),
    Provider(id="network-solutions", name="Network Solutions", ns=("worldnic.com",)),
    Provider(id="reg-ru", name="REG.RU", ns=("reg.ru",), asns=(197695,)),
    Provider(id="ru-center", name="RU-CENTER", ns=("nic.ru",)),
    Provider(id="siteground", name="SiteGround", ns=("siteground.net",), mx=("mailspamprotection.com",)),
    Provider(id="nic-india", name="NIC India", ns=("nic.in",), asns=(4758, 141501)),
    Provider(id="centralnic", name="CentralNic", ns=("centralnic.net",)),
    Provider(id="cira", name="CIRA D-Zone", ns=("d-zone.ca",)),
    Provider(id="eurodns", name="EuroDNS", ns=("eurodns.com",)),
    Provider(id="iij", name="IIJ (Internet Initiative Japan)", ns=("iij.ad.jp",), asns=(2497,)),
    # Email.
    Provider(id="google-workspace", name="Google Workspace", mx=("google.com", "googlemail.com")),
    # outlook.cn is Microsoft 365 operated by 21Vianet in China.
    Provider(id="microsoft-365", name="Microsoft 365", mx=("outlook.com", "hotmail.com", "mx.microsoft", "outlook.cn")),
    # gpphosted.com is Proofpoint's service for US government customers.
    Provider(
        id="proofpoint", name="Proofpoint", mx=("pphosted.com", "ppe-hosted.com", "gpphosted.com", "proofpoint.com")
    ),
    Provider(id="mimecast", name="Mimecast", mx=("mimecast.com", "mimecast.co.za", "mimecast-offshore.com")),
    Provider(
        id="zoho",
        name="Zoho",
        ns=("zohocorp.com", "zns-53.com", "zns-53.net"),
        mx=("zoho.com", "zoho.eu", "zoho.in", "zoho.com.au", "zoho.jp", "zohomail.com"),
    ),
    Provider(id="amazon-ses", name="Amazon SES", mx=("amazonaws.com",)),
    Provider(id="cloudflare-email-routing", name="Cloudflare Email Routing", mx=("mx.cloudflare.net",)),
    Provider(id="barracuda", name="Barracuda", mx=("barracudanetworks.com",)),
    Provider(id="fastmail", name="Fastmail", mx=("messagingengine.com",)),
    Provider(id="mailgun", name="Mailgun", mx=("mailgun.org",)),
    Provider(id="sendgrid", name="SendGrid", mx=("sendgrid.net",)),
    Provider(id="cisco-secure-email", name="Cisco Secure Email", mx=("iphmx.com",)),
    Provider(id="broadcom-email-security", name="Broadcom Email Security.cloud", mx=("messagelabs.com",)),
    Provider(id="cloudflare-email-security", name="Cloudflare Email Security", mx=("cf-emailsecurity.net",)),
    Provider(id="netease", name="NetEase Mail", mx=("netease.com", "163.com", "qiye163mail.com")),
    Provider(id="proton", name="Proton Mail", mx=("protonmail.ch",)),
    Provider(id="hornetsecurity", name="Hornetsecurity", mx=("hornetsecurity.com",)),
    Provider(id="trellix", name="Trellix Email Security", mx=("fireeyecloud.com",)),
    Provider(id="fortinet", name="Fortinet FortiMail Cloud", mx=("fortimailcloud.com",)),
    Provider(id="sophos", name="Sophos Email", mx=("sophos.com",)),
    Provider(id="trend-micro", name="Trend Micro Email Security", mx=("trendmicro.com", "trendmicro.eu")),
    # Certificate authorities that are not already listed above under their parent company.
    Provider(id="letsencrypt", name="Let's Encrypt", issuers=(r"^Let's Encrypt",)),
    Provider(
        id="digicert",
        name="DigiCert",
        # DigiCert DNS, the successor brand of DNS Made Easy, answers as *.digicertdns.com.
        ns=("digicertdns.com", "digicertdns.net"),
        asns=(32575,),
        issuers=(r"^DigiCert", r"^GeoTrust", r"^Thawte", r"^RapidSSL", r"^Symantec", r"^VeriSign"),
    ),
    Provider(id="sectigo", name="Sectigo", issuers=(r"^Sectigo", r"^COMODO", r"USERTrust")),
    Provider(id="globalsign", name="GlobalSign", issuers=(r"^GlobalSign",)),
    Provider(id="entrust", name="Entrust", issuers=(r"^Entrust", r"^AffirmTrust")),
    Provider(id="zerossl", name="ZeroSSL", issuers=(r"^ZeroSSL",)),
    Provider(id="ssl-com", name="SSL.com", issuers=(r"^SSL Corp",)),
    Provider(id="certum", name="Certum", issuers=(r"^Unizeto", r"^Asseco", r"^Certum")),
    Provider(id="actalis", name="Actalis", issuers=(r"^Actalis",)),
    Provider(id="buypass", name="Buypass", issuers=(r"^Buypass",)),
    Provider(id="harica", name="HARICA", issuers=(r"^Hellenic Academic", r"^HARICA")),
    Provider(id="trustasia", name="TrustAsia", issuers=(r"^TrustAsia",)),
    Provider(id="identrust", name="IdenTrust", issuers=(r"^IdenTrust",)),
    Provider(id="internet2", name="Internet2 (InCommon)", issuers=(r"^Internet2", r"^InCommon")),
    Provider(id="secom", name="SECOM Trust Systems", issuers=(r"^SECOM Trust",)),
)

_PSEUDO_NAMES = {SELF: "Self-hosted", OTHER: "Other (not in taxonomy)", NONE: "None"}


@cache
def by_id() -> dict[str, Provider]:
    providers: dict[str, Provider] = {}
    for p in PROVIDERS:
        if p.id in providers or p.id in PSEUDO:
            raise ValueError(f"duplicate provider id: {p.id}")
        providers[p.id] = p
    return providers


def display_name(provider_id: str) -> str:
    if provider_id in _PSEUDO_NAMES:
        return _PSEUDO_NAMES[provider_id]
    provider = by_id().get(provider_id)
    return provider.name if provider else provider_id
