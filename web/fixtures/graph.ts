import type { EdgeKind, GraphDoc, GraphEdge, GraphNode, Health } from "../src/lib/api/types.ts";
import { rng, weighted } from "./rng.ts";

/**
 * Development fixture for graph.json: 1,500 nodes and about 5,000 edges, the scale the graph
 * page must stay smooth at. Providers carry real infrastructure names; the dependent services
 * are generated names. The edges are invented for development only.
 */

interface ProviderSpec {
  id: string;
  name: string;
  category: string;
  kind: "service" | "provider";
  /** Which census kinds this provider can be found through, with a popularity weight. */
  roles: Partial<Record<"dns" | "edge" | "hosting", number>>;
  evidence: Partial<Record<"dns" | "edge" | "hosting", string>>;
}

const PROVIDERS: ProviderSpec[] = [
  { id: "cloudflare", name: "Cloudflare", category: "edge", kind: "service", roles: { dns: 30, edge: 34, hosting: 3 }, evidence: { dns: "dns:ns.cloudflare.com", edge: "header:cf-ray", hosting: "asn:AS13335" } },
  { id: "aws", name: "Amazon Web Services", category: "cloud", kind: "service", roles: { dns: 14, edge: 12, hosting: 38 }, evidence: { dns: "dns:awsdns", edge: "header:x-amz-cf-id", hosting: "asn:AS16509" } },
  { id: "gcp", name: "Google Cloud", category: "cloud", kind: "service", roles: { dns: 4, edge: 5, hosting: 16 }, evidence: { dns: "dns:googledomains.com", edge: "header:via 1.1 google", hosting: "asn:AS15169" } },
  { id: "azure", name: "Microsoft Azure", category: "cloud", kind: "service", roles: { dns: 4, edge: 3, hosting: 10 }, evidence: { dns: "dns:azure-dns.com", edge: "header:x-azure-ref", hosting: "asn:AS8075" } },
  { id: "akamai", name: "Akamai", category: "edge", kind: "service", roles: { dns: 3, edge: 9, hosting: 1 }, evidence: { dns: "dns:akam.net", edge: "header:x-akamai-transformed", hosting: "asn:AS20940" } },
  { id: "fastly", name: "Fastly", category: "edge", kind: "service", roles: { edge: 8 }, evidence: { edge: "header:x-served-by cache" } },
  { id: "vercel", name: "Vercel", category: "hosting", kind: "service", roles: { edge: 3, hosting: 5 }, evidence: { edge: "header:x-vercel-id", hosting: "header:server Vercel" } },
  { id: "netlify", name: "Netlify", category: "hosting", kind: "service", roles: { edge: 1, hosting: 2 }, evidence: { edge: "header:x-nf-request-id", hosting: "header:server Netlify" } },
  { id: "digitalocean", name: "DigitalOcean", category: "cloud", kind: "service", roles: { dns: 1, hosting: 4 }, evidence: { dns: "dns:digitalocean.com", hosting: "asn:AS14061" } },
  { id: "hetzner", name: "Hetzner", category: "cloud", kind: "provider", roles: { hosting: 3 }, evidence: { hosting: "asn:AS24940" } },
  { id: "ovh", name: "OVHcloud", category: "cloud", kind: "provider", roles: { dns: 1, hosting: 2 }, evidence: { dns: "dns:ovh.net", hosting: "asn:AS16276" } },
  { id: "linode", name: "Akamai Connected Cloud", category: "cloud", kind: "service", roles: { hosting: 1 }, evidence: { hosting: "asn:AS63949" } },
  { id: "ns1", name: "NS1", category: "dns", kind: "provider", roles: { dns: 3 }, evidence: { dns: "dns:nsone.net" } },
  { id: "godaddy", name: "GoDaddy DNS", category: "dns", kind: "provider", roles: { dns: 3 }, evidence: { dns: "dns:domaincontrol.com" } },
  { id: "dnsimple", name: "DNSimple", category: "dns", kind: "provider", roles: { dns: 1 }, evidence: { dns: "dns:dnsimple.com" } },
  { id: "heroku", name: "Heroku", category: "hosting", kind: "service", roles: { hosting: 2 }, evidence: { hosting: "header:via vegur" } },
  { id: "render", name: "Render", category: "hosting", kind: "service", roles: { hosting: 1 }, evidence: { hosting: "header:rndr-id" } },
  { id: "fly", name: "Fly.io", category: "hosting", kind: "service", roles: { hosting: 1 }, evidence: { hosting: "header:fly-request-id" } },
];

/** Platforms that services name in their own incident reports. */
const NAMED = ["github", "stripe", "twilio", "okta", "auth0", "sendgrid", "datadog", "slack", "openai", "mongodb", "atlassian", "pagerduty"];
const NAMED_NAMES: Record<string, [string, string]> = {
  github: ["GitHub", "devtools"],
  stripe: ["Stripe", "payments"],
  twilio: ["Twilio", "messaging"],
  okta: ["Okta", "identity"],
  auth0: ["Auth0", "identity"],
  sendgrid: ["SendGrid", "messaging"],
  datadog: ["Datadog", "observability"],
  slack: ["Slack", "collaboration"],
  openai: ["OpenAI", "ai"],
  mongodb: ["MongoDB Atlas", "data"],
  atlassian: ["Atlassian", "collaboration"],
  pagerduty: ["PagerDuty", "observability"],
};

const FIRST = ["Arc", "Basalt", "Cedar", "Delta", "Ember", "Flint", "Grove", "Harbor", "Iris", "Juniper", "Kestrel", "Lumen", "Mosaic", "Nimbus", "Orbit", "Pylon", "Quarry", "Ridge", "Sable", "Tidal", "Umber", "Vale", "Willow", "Yarrow", "Zephyr", "Aster", "Birch", "Cobalt", "Dune", "Fjord"];
const SECOND = ["Pay", "Mail", "Docs", "Base", "Ops", "Flow", "Desk", "Labs", "Data", "Stack", "Sync", "Send", "Logs", "Auth", "Shop", "Chat", "Forms", "Maps", "Cast", "Ledger", "Notes", "Board", "Queue", "Vault"];
const CATEGORIES: [string, number][] = [["devtools", 14], ["collaboration", 12], ["data", 11], ["messaging", 9], ["ai", 8], ["payments", 8], ["identity", 6], ["observability", 7], ["consumer", 9], ["commerce", 7], ["media", 4], ["hosting", 3]];
const HEALTH: [Health, number][] = [["operational", 88], ["degraded", 5], ["partial_outage", 2.5], ["major_outage", 1], ["maintenance", 1.5], ["unknown", 2]];

let cached: GraphDoc | null = null;

export function graphFixture(): GraphDoc {
  if (cached) return cached;
  const random = rng(20261103);
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const edgeKey = new Map<string, GraphEdge>();

  const addEdge = (source: string, target: string, kind: EdgeKind, evidence: string, weight: number) => {
    if (source === target) return;
    const key = `${source}>${target}`;
    const existing = edgeKey.get(key);
    if (existing) {
      if (!existing.kinds.includes(kind)) existing.kinds.push(kind);
      if (!existing.evidence.includes(evidence)) existing.evidence.push(evidence);
      existing.weight = Math.min(1, Math.round((existing.weight + weight * 0.5) * 100) / 100);
      return;
    }
    const e: GraphEdge = { source, target, kinds: [kind], weight, evidence: [evidence] };
    edgeKey.set(key, e);
    edges.push(e);
  };

  for (const p of PROVIDERS) {
    nodes.push({ id: p.id, name: p.name, category: p.category, kind: p.kind, health: p.kind === "service" ? "operational" : undefined, dependents: 0 });
  }
  for (const id of NAMED) {
    const [name, category] = NAMED_NAMES[id];
    nodes.push({ id, name, category, kind: "service", health: "operational", dependents: 0 });
  }

  const used = new Set(nodes.map((n) => n.name));
  const target = 1500;
  let serial = 0;
  while (nodes.length < target) {
    let name = `${FIRST[Math.floor(random() * FIRST.length)]}${SECOND[Math.floor(random() * SECOND.length)]}`;
    if (used.has(name)) name = `${name} ${["Cloud", "EU", "Pro", "Connect", "Edge", "One"][serial % 6]}`;
    if (used.has(name)) {
      serial++;
      continue;
    }
    used.add(name);
    const id = name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    nodes.push({
      id,
      name,
      category: weighted(random, CATEGORIES),
      kind: "service",
      health: weighted(random, HEALTH),
      dependents: 0,
    });
    serial++;
  }

  const providerFor = (role: "dns" | "edge" | "hosting") =>
    weighted(random, PROVIDERS.filter((p) => p.roles[role]).map((p) => [p, p.roles[role] ?? 0] as const));

  // Providers depend on each other a little (platforms run on clouds).
  addEdge("vercel", "aws", "hosting", "asn:AS16509", 0.9);
  addEdge("netlify", "aws", "hosting", "asn:AS16509", 0.8);
  addEdge("netlify", "gcp", "hosting", "asn:AS15169", 0.5);
  addEdge("heroku", "aws", "hosting", "asn:AS16509", 0.95);
  addEdge("render", "gcp", "hosting", "asn:AS15169", 0.7);
  addEdge("fly", "cloudflare", "dns", "dns:ns.cloudflare.com", 0.6);
  for (const id of NAMED) {
    addEdge(id, providerFor("dns").id, "dns", "dns:authoritative", 0.7);
    addEdge(id, providerFor("hosting").id, "hosting", "asn:origin", 0.8);
  }

  const dependents = nodes.filter((n) => !PROVIDERS.some((p) => p.id === n.id) && !NAMED.includes(n.id));
  for (const n of dependents) {
    const dns = providerFor("dns");
    addEdge(n.id, dns.id, "dns", dns.evidence.dns ?? "dns:authoritative", 0.6 + random() * 0.3);
    if (random() < 0.8) {
      const edge = providerFor("edge");
      addEdge(n.id, edge.id, "edge", edge.evidence.edge ?? "header:cdn", 0.6 + random() * 0.35);
    }
    const host = providerFor("hosting");
    addEdge(n.id, host.id, "hosting", host.evidence.hosting ?? "asn:origin", 0.55 + random() * 0.4);
    if (random() < 0.35) {
      const second = providerFor("hosting");
      addEdge(n.id, second.id, "hosting", second.evidence.hosting ?? "asn:origin", 0.3 + random() * 0.3);
    }
    const namedCount = random() < 0.5 ? 1 + Math.floor(random() * 2) : 0;
    for (let k = 0; k < namedCount; k++) {
      const t = NAMED[Math.floor(random() * NAMED.length)];
      const times = 1 + Math.floor(random() * 6);
      addEdge(n.id, t, "named", `named in ${times} past incident${times === 1 ? "" : "s"}`, 0.3 + random() * 0.4);
    }
    if (random() < 0.12) {
      const p = providerFor(random() < 0.5 ? "edge" : "hosting");
      const together = 2 + Math.floor(random() * 6);
      const of = together + 10 + Math.floor(random() * 40);
      addEdge(n.id, p.id, "cofailure", `co-failed ${together} of ${of} times`, 0.2 + random() * 0.3);
    }
  }

  const inDegree = new Map<string, number>();
  for (const e of edges) inDegree.set(e.target, (inDegree.get(e.target) ?? 0) + 1);
  for (const n of nodes) n.dependents = inDegree.get(n.id) ?? 0;
  for (const e of edges) e.weight = Math.round(e.weight * 100) / 100;

  cached = { generated_at: "2026-11-03T06:00:00Z", nodes, edges };
  return cached;
}
