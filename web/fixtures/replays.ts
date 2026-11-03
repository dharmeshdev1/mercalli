import type { Impact, Replay, ReplayEvent, ReplaysDoc } from "../src/lib/api/types.ts";

/**
 * Development fixture for replays.json: one cascade from an edge network outage. Every name in
 * it is made up, so nothing here can be mistaken for a real vendor's history.
 */

type E = [name: string, category: string, offset: number, duration: number | null, impact: Impact, named: boolean, title: string];

const EVENTS: E[] = [
  ["Cobalt Docs", "devtools", 3, 141, "major", true, "Errors loading documentation sites"],
  ["Lumen Pay", "payments", 4, 128, "major", true, "Checkout pages failing to load"],
  ["Harbor Chat", "messaging", 5, 96, "minor", false, "Delayed message delivery"],
  ["Sable Shop", "commerce", 6, 150, "critical", true, "Storefronts unavailable"],
  ["Pylon Auth", "identity", 7, 118, "major", true, "Elevated login failures"],
  ["Orbit Board", "collaboration", 8, 132, "minor", false, "Slow page loads"],
  ["Ridge Stack", "devtools", 9, 64, "minor", true, "API requests returning 502"],
  ["Tidal Cast", "media", 11, 87, "minor", false, "Video playback errors"],
  ["Quarry Forms", "collaboration", 12, 101, "minor", true, "Form submissions failing"],
  ["Aster Notes", "consumer", 13, 75, "minor", false, "Sync delays"],
  ["Ember Mail", "messaging", 14, 140, "major", true, "Webhook deliveries delayed"],
  ["Flint Queue", "devtools", 16, 58, "minor", false, "Job processing delays"],
  ["Grove Ledger", "payments", 18, 122, "major", true, "Payment confirmation emails delayed"],
  ["Mosaic Data", "data", 21, 90, "minor", false, "Dashboard queries timing out"],
  ["Kestrel Sync", "devtools", 24, 70, "minor", true, "Elevated error rates on the API"],
  ["Willow Shop", "commerce", 26, 112, "major", true, "Elevated errors on checkout"],
  ["Iris Vault", "identity", 29, 66, "minor", false, "Intermittent SSO failures"],
  ["Birch Desk", "collaboration", 33, 95, "minor", true, "Help center unavailable"],
  ["Nimbus Logs", "data", 37, 48, "minor", false, "Ingestion delays"],
  ["Delta Send", "messaging", 41, 80, "minor", true, "SMS delivery delays"],
  ["Zephyr Labs", "ai", 46, 52, "minor", false, "Elevated latency on inference"],
  ["Umber Maps", "consumer", 52, 61, "minor", true, "Map tiles failing to load"],
  ["Juniper Flow", "devtools", 58, 44, "minor", false, "Deploys delayed"],
  ["Cedar Pay", "payments", 64, 70, "minor", true, "Dashboard login errors"],
  ["Vale Chat", "collaboration", 71, 39, "minor", false, "File uploads failing"],
  ["Basalt Shop", "commerce", 79, 55, "minor", true, "Admin panel slow"],
  ["Fjord Auth", "identity", 88, 41, "minor", false, "Password reset emails delayed"],
  ["Yarrow Labs", "ai", 97, 33, "minor", true, "Model API errors"],
  ["Dune Forms", "collaboration", 109, 30, "minor", false, "Embedded forms not rendering"],
  ["Arc Base", "devtools", 124, 26, "minor", false, "Status checks failing"],
  ["Quill Shop", "commerce", 141, 22, "minor", true, "Image uploads failing"],
  ["Lumen Notes", "consumer", 163, 18, "minor", false, "Mobile app login errors"],
];

const events: ReplayEvent[] = EVENTS.map(([service_name, category, offset_min, duration_min, impact, names_upstream, title], i) => {
  const service_id = service_name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  return { service_id, service_name, category, incident_id: `${service_id}:fx${String(i).padStart(4, "0")}`, title, impact, offset_min, duration_min, names_upstream };
});

const categories: Record<string, number> = {};
for (const e of events) categories[e.category] = (categories[e.category] ?? 0) + 1;

const replay: Replay = {
  slug: "meridian-edge-errors",
  title: "Meridian Edge errors spread across its customers",
  upstream: {
    service_id: "meridian-edge",
    service_name: "Meridian Edge",
    incident_id: "meridian-edge:fx0001",
    title: "Elevated 5xx errors across the edge network",
    impact: "critical",
    started_at: "2026-06-12T10:20:00Z",
    resolved_at: "2026-06-12T12:54:00Z",
    duration_min: 154,
    on_record: true,
  },
  window_min: 180,
  events,
  totals: { services: events.length, named: events.filter((e) => e.names_upstream).length, categories },
};

export const replaysFixture: ReplaysDoc = { replays: [replay] };
