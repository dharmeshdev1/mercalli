import type { CensusBucket, CensusField, CensusFieldSummary, CensusFields, CensusSummary } from "../src/lib/api/types.ts";

/**
 * Development fixture for census-summary.json, in the shape the census writes. Provider names are
 * real; every count and share is invented for development and never shipped.
 */

type Row = [id: string, name: string, pct: number];

const TOPS: Record<Exclude<CensusField, "hosting_provider_without_edge">, Row[]> = {
  dns_provider: [["cloudflare", "Cloudflare", 26.1], ["aws", "Amazon Web Services", 21.4], ["gcp", "Google Cloud", 6.2], ["azure", "Microsoft Azure", 4.1], ["akamai", "Akamai", 3.8], ["self", "Self-hosted", 3.1], ["other", "Other (not in taxonomy)", 19.6]],
  edge_provider: [["none", "None", 30.2], ["cloudflare", "Cloudflare", 28.7], ["aws", "Amazon Web Services", 10.9], ["akamai", "Akamai", 9.1], ["fastly", "Fastly", 4.6], ["gcp", "Google Cloud", 4.2]],
  hosting_provider: [["cloudflare", "Cloudflare", 30.4], ["other", "Other (not in taxonomy)", 20.8], ["aws", "Amazon Web Services", 18.1], ["akamai", "Akamai", 9.4], ["gcp", "Google Cloud", 5.9], ["fastly", "Fastly", 4.4]],
  hosting_region: [["aws/us-east-1", "Amazon Web Services us-east-1", 28.3], ["aws/us-west-2", "Amazon Web Services us-west-2", 11.9], ["aws/eu-west-1", "Amazon Web Services eu-west-1", 10.7], ["aws/ap-northeast-1", "Amazon Web Services ap-northeast-1", 9.2]],
  tls_issuer: [["Google Trust Services", "Google Trust Services", 28.1], ["Let's Encrypt", "Let's Encrypt", 21.3], ["DigiCert", "DigiCert", 14.9], ["Sectigo", "Sectigo", 9.8], ["Amazon", "Amazon", 8.2]],
  mx_provider: [["google-workspace", "Google Workspace", 29.7], ["other", "Other (not in taxonomy)", 22.4], ["microsoft-365", "Microsoft 365", 19.8], ["self", "Self-hosted", 6.1], ["proofpoint", "Proofpoint", 4.0]],
};

function fields(resolved: number, scale: number): CensusFields {
  const out: CensusFields = {};
  (Object.keys(TOPS) as (keyof typeof TOPS)[]).forEach((field) => {
    const determinable = field === "hosting_region" ? Math.round(resolved * 0.07) : Math.round(resolved * 0.99);
    const summary: CensusFieldSummary = {
      determinable,
      resolved,
      distinct: 20 + TOPS[field].length * 4,
      hhi: Math.round(TOPS[field].reduce((s, [, , p]) => s + p * p, 0) * scale),
      top: TOPS[field].map(([id, name, pct]) => {
        const count = Math.round((pct / 100) * determinable);
        return { id, name, count, pct_of_determinable: pct, pct_of_resolved: Math.round((count / resolved) * 10000) / 100 };
      }),
    };
    out[field] = summary;
  });
  return out;
}

function bucket(total: number, resolved: number, scale: number): CensusBucket {
  return { counts: { total, resolved, http_ok: Math.round(resolved * 0.76) }, fields: fields(resolved, scale) };
}

export const censusFixture: CensusSummary = {
  generated_at: "2026-11-01T04:00:00Z",
  vantage: {
    description: "One vantage point. Another location can see other addresses and sometimes other providers for the same domain.",
    dns_resolvers: ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
  },
  counts: { total: 10500, by_source: { tranco: 10000, catalog: 500 }, resolved: 9300, http_ok: 7050 },
  fields: fields(9300, 1),
  single_provider: {
    dns_and_edge: { count: 3500, comparable: 5500, pct_of_comparable: 63.6, pct_of_resolved: 37.6 },
    dns_edge_and_hosting: { count: 3300, comparable: 5200, pct_of_comparable: 63.5, pct_of_resolved: 35.5 },
  },
  aws_regions: { aws_hosted: 1700, with_region: 480, without_region: 1220, regions: [] },
  rank_buckets: { top_100: bucket(100, 80, 0.8), top_1k: bucket(1000, 880, 0.9), top_10k: bucket(10000, 8820, 1) },
};
