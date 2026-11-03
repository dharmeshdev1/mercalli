# Dependency census

The census answers one question for a list of domains: which companies does each one need to stay
up? For every domain it records who serves its DNS, who answers its HTTP requests, whose network
the answering addresses belong to (and in which cloud region), who issued its TLS certificate and
who receives its email. Aggregated over the most visited domains, the same data says how
concentrated the web is on a handful of providers, which is the input Mercalli's blast radius
model needs.

The code lives in `src/mercalli/census/`. Results are written as one row per domain
(`domains.parquet`, `domains.jsonl`) plus an aggregate `summary.json`.

## Running it

```sh
python -m mercalli.census --top 10000 --extra-catalog --out runs/census \
    --cache-dir ~/.cache/mercalli/census --tranco path/to/tranco-top-1m.csv.zip
```

| Flag | Meaning |
| --- | --- |
| `--top N` | Survey the top N domains of the Tranco list (0 to skip Tranco). |
| `--extra-catalog` | Also survey every domain in the catalog. `catalog/services.yaml` is used when it has entries, otherwise `catalog/seed.yaml`; `--catalog` picks a file explicitly. |
| `--out DIR` | Where `domains.parquet`, `domains.jsonl` and `summary.json` go. |
| `--cache-dir DIR` | Where reference data (IP ranges, the IP to ASN map) is cached. |
| `--tranco PATH` | The Tranco zip. Downloaded from tranco-list.eu into the cache directory when missing. |
| `--refresh` | Download every reference dataset again instead of using the cache. |
| `--record-network` | Record the vantage network's country, operator and nearest Cloudflare site in the summary. The scheduled run on GitHub's runners sets it; leave it off on a personal connection, where it would publish your internet provider and rough location. |
| `--dns-concurrency`, `--http-concurrency` | Defaults 200 and 64. |
| `--nameservers` | Comma separated recursive resolvers. Default `1.1.1.1,8.8.8.8,9.9.9.9`. |

With the default concurrency, a run over the Tranco top 10,000 plus about 580 catalog-only domains
takes six to seven minutes of probing on an ordinary connection.

From Python, `census()` returns the records, `summarize()` aggregates them and `write_outputs()`
saves both. None of them print or read global state; `main()` is the only function that
configures logging or raises the process's open file limit.

Domains from Tranco carry their rank and `source = "tranco"`. Catalog domains that are not in the
selected Tranco slice carry `rank = None` and `source = "catalog"`. A catalog domain that is also
in the slice appears once, as a Tranco row.

## What is recorded per domain

| Field | Meaning |
| --- | --- |
| `domain`, `rank`, `source`, `fetched_at` | Input and the UTC time the row was finished. |
| `ns` | Name servers of the zone the domain lives in. |
| `dns_provider`, `dns_providers` | The provider holding the most name servers, and every provider present. |
| `host` | The name probed over HTTP and TLS. |
| `cname_chain` | CNAME targets followed from `host`, in order. |
| `ips` | IPv4 and IPv6 addresses of `host`. |
| `asns`, `asn_orgs` | Origin ASNs of those addresses and their registered names. |
| `edge_provider` | The CDN, security proxy or multi-tenant platform that answers HTTP, or null. |
| `hosting_provider`, `hosting_region` | Whose address space the answering addresses are in, and the region when the provider publishes one. |
| `tls_issuer` | The organization that issued the certificate `host` serves. |
| `mx_hosts`, `mx_provider` | Mail exchangers of the zone and the provider behind the most preferred ones. |
| `http_status`, `server_header` | Status and `Server` header of the last response after redirects. |
| `final_host` | The host the redirects ended on, when it is outside the domain's zone. |
| `evidence` | Per field, the signals that decided it (see below). |
| `error` | Every stage that failed, `; ` separated, or null. |

Provider fields hold ids from the taxonomy in `providers.py`, plus three reserved values: `self`
(name servers inside the domain's own registered domain), `other` (data was there, but no
provider in the taxonomy matched) and `none` (decided absence, such as a domain without MX
records). Null means the field could not be decided, for example because a lookup failed.

## How each field is decided

### Zone and DNS provider

The census walks up from the domain until a name has NS records of its own, so a catalog entry
such as `api.github.com` is attributed to the `github.com` zone. Names that turn out to be CNAMEs
are skipped during the walk, because a resolver would otherwise return the NS records of the
CNAME's target. Each name server is matched by domain suffix (`*.ns.cloudflare.com`,
`*.nsone.net`) or, for numbered families such as Route 53's `ns-NNN.awsdns-NN.org`, by pattern.
Name servers under the domain's own registered domain count as `self`.

A zone served by two providers lists both in `dns_providers`; `dns_provider` is the one holding
more name servers, ties broken by id. Multi-provider DNS matters for blast radius: such a zone
survives an outage at either provider, which is why the summary counts it separately.

### Probed host, addresses and CNAME chain

For a registered domain the census probes `www.<domain>` when it resolves, otherwise the apex,
because that is what people type and what most sites serve. A catalog entry that is already a
hostname below its zone is probed as it is. A and AAAA queries for the host give `ips`, and the
answer section of the A query gives `cname_chain`.

### Edge provider

The edge is whoever terminates the visitor's HTTP connection: a CDN (Cloudflare, Akamai, Fastly,
CloudFront, Azure Front Door, Google's front end), a security proxy (Imperva, Sucuri) or a
multi-tenant site platform (Vercel, Netlify, GitHub Pages, Shopify, Squarespace, WordPress.com
and others). Companies get one id across products, so CloudFront is `aws`, Azure Front Door is
`azure` and Google's front end is `gcp`.

Signals are taken in this order, and the first that applies decides:

1. **CNAME chain.** The first recognized target in the chain is where the domain owner sent
   traffic (`*.edgekey.net`, `*.cdn.cloudflare.net`, `*.vercel-dns.com`, `shops.myshopify.com`).
   DNS load balancers such as Azure Traffic Manager and Akamai GTM, and origin load balancers
   such as ELB or App Service, are deliberately not edges.
2. **A platform's documented anycast address.** Shopify's `23.227.38.0/24` sits inside
   Cloudflare's ASN and GitHub Pages' `185.199.108.0/22` inside Fastly's, so these have to be
   checked before step 3 or every apex record pointing at them would read as the CDN underneath.
3. **Addresses inside an edge network.** Published ranges flagged as edge (all of Cloudflare's
   and Fastly's lists, AWS `CLOUDFRONT`, Azure `AzureFrontDoor.*` except `.Backend`, GCP's
   `global` scope and Google's non-cloud ranges) and ASNs that carry only edge traffic (Akamai's,
   for instance).
4. **Response headers** from responses served by the probed host itself: `cf-ray`,
   `x-amz-cf-id`, `x-served-by: cache-*`, `server: AkamaiGHost`, `x-vercel-id`, `via: 1.1 google`
   and so on. When headers name both a CDN and a platform, the CDN wins, since a CDN can sit in
   front of a platform but rarely the other way round.

Addresses vote in steps 2 and 3 only when they would also decide hosting: the IPv4 addresses
when there are any, since those are what most clients reach and what the HTTP and TLS probes
connect to. Some sites split their address families across CDNs (amazon.com answered on
CloudFront over IPv4 and on Akamai over IPv6); the IPv4 side decides and the other is kept as
evidence.

Signals that point at a different provider are kept in the evidence with an `also:` prefix. A
Vercel site proxied through Cloudflare therefore reads `edge_provider = "cloudflare"` with
`also:vercel:header:x-vercel-id` in its evidence. A null edge means none of the signals matched:
the site is served from its origin, or by an edge network the taxonomy does not know.

### Hosting provider and region

For each answering address the census looks the address up in the published IP ranges of AWS,
Google Cloud, Azure, Oracle Cloud, Cloudflare, Fastly and DigitalOcean. When none matches, the
address's origin ASN is mapped to a provider; an ASN outside the taxonomy gives `other`. IPv4
addresses decide when present (dual-stack sites sometimes serve IPv6 from another network), and
the most common provider among them wins.

Regions come only from published ranges: the AWS region (`us-east-1`), the GCP scope
(`us-central1`), the Azure Service Tag region (`eastus`), the Oracle region (`us-ashburn-1`) and,
for DigitalOcean, the city of its geofeed (`amsterdam`, `north-bergen`), because DigitalOcean
publishes locations rather than region names. Global address space (CloudFront, Global
Accelerator, GCP global load balancers) has no region. An address found only by ASN has no region
either: an AWS address outside `ip-ranges.json`, which happens with customers' own address
blocks announced by AWS, is `aws` without a region.

### Why origins behind an edge are hidden

When a CDN fronts a site, the addresses in DNS are the CDN's, so `hosting_provider` is the CDN's
network and says nothing about where the origin runs. A Cloudflare-proxied site hosted on AWS
looks exactly like a Cloudflare-proxied site hosted in a basement, and nothing a visitor can
observe tells them apart; finding origins would mean scanning for them, which the census does
not do. The census records what is visible. `summary.json` reports `hosting_provider` a second
time over domains with no edge (`hosting_provider_without_edge`), which is the closest available
measure of where origins run, with the bias that sites behind a CDN are left out entirely.

### TLS issuer

A separate TLS handshake goes to the host's first IPv4 address (IPv6 when there is none) with the
host name in SNI. Certificate verification is off on purpose so that expired, self-signed and
mismatched certificates still show who issued them, and the issuer is read straight from the
certificate's DER bytes (`x509.py`). The issuer's organization, or its common name when there is
no organization, is normalized to the company that runs the CA: GeoTrust, Thawte and RapidSSL
intermediates are DigiCert, COMODO is Sectigo, Starfield is GoDaddy. Issuers the taxonomy does
not know are kept as their organization name with legal suffixes removed.

### Email provider

The MX records of the zone are sorted by preference and the most preferred exchangers decide,
since that is where mail arrives first: a domain with Proofpoint at preference 10 and Google at
20 is `proofpoint`. A zone without MX records, or with the RFC 7505 null MX, is `none`. A failed
lookup leaves the field null.

### HTTP

The census sends exactly one GET per domain to `https://<host>/`, falling back to `http://` only
when nothing accepts a TLS connection. Redirects are followed by hand, up to five, and every
response is closed as soon as its headers arrive, so no body is downloaded. Each hop connects to
the address the census resolved for that hop's host (redirect targets are resolved through the
same resolvers) and names the host in SNI and the `Host` header. That keeps the headers and the
DNS view describing the same server. Each hop has 8 seconds.

`http_status` and `server_header` come from the last response. `final_host` is set when the last
response came from outside the domain's zone, which is how redirects to a parent brand
(`twitter.com` to `x.com`) show up.

### Evidence and errors

Evidence strings name the signal and the value that matched:

| Prefix | Example | Meaning |
| --- | --- | --- |
| `ns:` | `ns:*.ns.cloudflare.com`, `ns:self`, `ns:other:example.net` | Name server suffix or pattern. |
| `cname:` | `cname:*.edgekey.net` | CNAME target suffix. |
| `prefix:` | `prefix:23.227.38.65` | Address inside a platform's anycast prefix. |
| `range:` | `range:aws/us-east-1/EC2`, `range:cloudflare/global` | Published range: dataset, region, service. |
| `asn:` | `asn:13335` | Origin ASN of an answering address. |
| `header:` | `header:cf-ray`, `header:server=AkamaiGHost` | Response header, with its value when a value pattern matched. |
| `mx:` | `mx:*.google.com`, `mx:none`, `mx:null`, `mx:other:example.org` | Mail exchanger suffix. |
| `issuer:` | `issuer:O=Let's Encrypt,CN=R11` | Raw certificate issuer. |
| `also:` | `also:vercel:header:x-vercel-id` | A signal for a provider other than the one chosen. |

`error` lists failures by stage: `dns: nxdomain`, `dns: no address (nodata)`, `dns: mx timeout`,
`http: TimeoutError`, `http: redirect to example.org: ConnectError: ...`,
`tls: ConnectionResetError: ...`. A row can have errors and still be fully classified, for
example when only the TLS handshake failed.

## Provider taxonomy

`providers.py` holds one `Provider` per company with its matchers: name server suffixes, edge
CNAME suffixes, response headers, platform anycast prefixes, ASNs, mail exchanger suffixes and
certificate issuer patterns. Its kinds (`dns`, `edge`, `hosting`, `email`, `ca`) follow from which
matchers it has.

Ids equal the Mercalli catalog's service ids wherever the catalog has the service, so census rows
join the dependency graph without a mapping table. That includes catalog services that serve
their own sites from their own networks (Dropbox, Notion, Zoom and others): their hosting
provider is themselves. One company gets one id: Route 53, CloudFront
and the AWS certificate authority are all `aws`. Brands that run separate infrastructure and
publish their own status page keep their own id, matching the catalog: GitHub Pages is `github`,
Heroku `heroku`, Firebase Hosting `firebase`, Linode `linode`. Email products use the names the
census reports (`google-workspace`, `microsoft-365`, `amazon-ses`, `cloudflare-email-routing`)
because mail outages rarely follow the parent cloud.

To add a provider, add a `Provider` entry. The index the classifiers use refuses a suffix or an
ASN claimed by two providers, and the tests build that index, so a conflict fails the test suite
rather than silently picking one. `summary.json` lists what fell into `other` (name server
domains, mail domains and ASNs) as the place to look for missing entries.

## Reference data

All datasets are cached in `--cache-dir` with a `.meta.json` next to each file recording the URL,
download time and `Last-Modified`. Without `--refresh` a cached copy is reused. A dataset that
fails to download falls back to its stale copy, and one with no copy at all is skipped: its
addresses are then attributed by ASN only, which keeps the provider but loses the region. Each
dataset's status and date are in `summary.json` under `datasets` and `dataset_dates`.

| Dataset | Source | Gives |
| --- | --- | --- |
| AWS | `ip-ranges.amazonaws.com/ip-ranges.json` | Region and service per prefix; `CLOUDFRONT` marks edge. |
| Google Cloud | `www.gstatic.com/ipranges/cloud.json` | Scope (region); `global` marks Google's front end. |
| Google | `www.gstatic.com/ipranges/goog.json` | Every Google address; checked after cloud.json, so what remains is Google's own front end. |
| Azure | Service Tags JSON linked from Microsoft download id 56519 | Region per tag; `AzureFrontDoor.*` marks edge. |
| Oracle Cloud | `docs.oracle.com/iaas/tools/public_ip_ranges.json` | Region per prefix. |
| Cloudflare | `www.cloudflare.com/ips-v4`, `ips-v6` | Edge ranges. |
| Fastly | `api.fastly.com/public-ip-list` | Edge ranges. |
| DigitalOcean | `digitalocean.com/geo/google.csv` | Geofeed city per prefix. |
| IP to ASN | `iptoasn.com/data/ip2asn-combined.tsv.gz` | Origin ASN and its name for every routed address. |

Overlapping prefixes are flattened once at load time into sorted, non-overlapping integer
intervals where each address maps to its most specific prefix, so a lookup is a single bisect
(`iprange.py`). AWS lists `13.32.0.0/15` under both `AMAZON` and `CLOUDFRONT`; identical prefixes
are merged, keeping the specific service, any region and the edge flag.

## Vantage point and resolver limits

A census runs from one place, and `summary.json` describes it under `vantage`: country and network
(from Cloudflare's trace endpoint and the IP to ASN map; the public address itself is not
stored), the resolvers, and how HTTP and TLS pick addresses. Three effects follow from having one
vantage point:

- **Geo-aware answers.** CDNs and large sites answer DNS with addresses near the asker, so another
  location can see other addresses, other regions and occasionally another provider for the same
  domain (a site may use one CDN in Europe and another in Asia). Hosting regions in particular
  describe the region serving this vantage point, not every region a site runs in.
- **Caches inside the local ISP.** Akamai, Google, Meta and Netflix place caches inside large ISPs,
  and those caches answer from the ISP's own address space. The edge is still recognized from
  the CNAME chain or headers, but the hosting provider reads `other` because no provider in the
  taxonomy owns the ISP's addresses. `edges_in_other_networks` in the summary counts these rows.
- **National filtering.** Sites blocked where the census runs reset or time out on HTTP and TLS
  while their DNS still resolves. Their DNS, hosting and MX fields are still measured; their edge
  can only come from the CNAME chain and addresses.

DNS queries go to public resolvers (`1.1.1.1`, `8.8.8.8`, `9.9.9.9` by default), rotated per
query, each with a 3 second budget and up to three attempts on timeouts or SERVFAIL. NXDOMAIN and
empty answers are final. The census sends no EDNS client subnet; Google Public DNS adds a
truncated one on its own, Cloudflare and Quad9 do not, so geo-aware answers mostly follow the
resolver's nearest site. Public resolvers rate limit heavy clients, and a run that hits those
limits shows it as `dns: ... timeout` or `servfail` errors; lower `--dns-concurrency` if they
appear in numbers.

## Politeness

Every request identifies itself with Mercalli's User-Agent, which links back to the project. HTTP
requests and TLS handshakes share one budget: at most `--http-concurrency` connections in flight
and never more than two at a time to the same host. Each domain gets one GET, bodies are never
downloaded and nothing is retried except DNS queries. Some sites answer automated clients with
403 or a challenge page; their headers still identify the edge, so those rows stay useful.

## Reading summary.json

Every share table covers **resolved domains**: those whose probed host had at least one address.
Domains that do not resolve (many of the Tranco list's top entries are infrastructure names such
as `akamaiedge.net` with no web server at all) are counted in `counts` and left out of every
share. Each table gives two percentages:

- `pct_of_determinable`: over resolved domains where the field could be decided. For
  `tls_issuer` that is domains where a handshake completed; for `hosting_region`, domains whose
  address is in a range that carries a region.
- `pct_of_resolved`: over all resolved domains, so undecided domains count against the share.

`edge_provider` is decided for every resolved domain (a provider or `none`), so its two
percentages are equal.

| Key | Content |
| --- | --- |
| `counts` | Totals: resolved, HTTP responses, `http_ok` (a final status below 400), status classes, TLS issuers found, off-domain redirects. |
| `fields` | Top 25 per field with counts, both percentages, the number of distinct values and the Herfindahl-Hirschman index. `hosting_region` keys are `provider/region`. |
| `single_provider` | How often the DNS provider is the same company as the edge, the hosting network, or the front door (the edge when there is one, otherwise the host), and how many zones use more than one DNS provider. |
| `aws_regions` | Every region of AWS-hosted domains, with the count of AWS-hosted domains that have no region. |
| `top_asns` | The network of each domain's first answering address, independent of the taxonomy. |
| `rank_buckets` | The same counts and top 10 tables for Tranco ranks 1 to 100, 1 to 1,000 and 1 to 10,000. |
| `catalog_only` | The same for catalog domains outside the Tranco slice. |
| `unclassified` | The most common name server domains, mail domains and ASNs that fell into `other`. |
| `edges_in_other_networks` | Rows with a known edge whose addresses no provider claims (see vantage effects). |
| `vantage`, `run`, `datasets`, `dataset_dates` | Where and when the run happened and which data it used. |

The Herfindahl-Hirschman index is the sum of squared percentage shares, from near 0 (fragmented)
to 10,000 (one provider holds everything). Shares are taken over determinable domains, and the
buckets `other`, `self` and `none` pool many unrelated operators, so they are treated as
fragmented and contribute nothing. The index is therefore a lower bound. For reference, the 2023
US merger guidelines treat an index above 1,800 as highly concentrated.

The single provider figures only compare named providers: a domain whose DNS is `self` or `other`
is never counted as matching anything.

## Reproducing a run

1. Fetch the Tranco list (or let the census download it) and run the command above.
2. Use `--refresh` to bring every reference dataset up to date; the dates used end up in
   `summary.json`.
3. Results vary between runs: DNS answers rotate, sites change providers and blocking varies by
   network. Compare runs by their `summary.json` rather than row by row, and note the `vantage`
   of each.

The tests (`tests/census/`) run without network access: classifier cases built from fixed name
servers, CNAME chains, headers and ASNs, IP lookups at range boundaries for IPv4 and IPv6, the
dataset parsers, the HTTP redirect logic against mocked responses, and the summary arithmetic on
synthetic records.

## Known limitations

- One vantage point, one moment. See the vantage section.
- Origins behind an edge are invisible, so hosting shares overweight edge networks.
- The edge taxonomy recognizes what it lists. A CDN that is not listed and has no recognizable
  header reads as no edge, which inflates `none`.
- Platform detection relies on CNAMEs, a few documented anycast prefixes and headers. A platform
  site reached through an apex record outside those prefixes, with headers stripped, is
  attributed to the network underneath.
- `self` is decided by comparing registered domains with a short list of two-level suffixes
  instead of the full public suffix list, which can misjudge unusual suffixes.
- AWS addresses outside `ip-ranges.json` (customer-owned blocks announced by AWS) have no region.
- The record describes the probed host. When `www.<domain>` only redirects to the apex and the
  two are served by different networks (a redirect service in front of a site on a CDN), the
  edge and hosting fields describe the redirector; the site behind it shows up only in
  `http_status` and `server_header`.
