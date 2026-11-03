/**
 * Time and number formatting. Everything here is pure and locale-stable: copy is English, times
 * are 24 hour, and ranges are written with "to" rather than a dash.
 */

export type Zone = "utc" | "local";

export const MINUTE = 60_000;
export const HOUR = 60 * MINUTE;
export const DAY = 24 * HOUR;

/** Milliseconds since the epoch, or null for missing or unparseable input. */
export function parseTime(iso: string | null | undefined): number | null {
  if (!iso) return null;
  // Some artifacts write "2025-07-01 00:00:00+00:00"; ISO 8601 wants a T between date and time.
  const s = /^\d{4}-\d{2}-\d{2} \d/.test(iso) ? iso.replace(" ", "T") : iso;
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : null;
}

/** A duration in minutes, written the way people say it: "41 min", "5 h 12 min", "2 d 4 h". */
export function formatDuration(minutes: number | null | undefined): string {
  if (minutes == null || !Number.isFinite(minutes) || minutes < 0) return "unknown";
  if (minutes < 1) return "under a minute";
  const total = Math.round(minutes);
  if (total < 60) return `${total} min`;
  if (total < 24 * 60) {
    const h = Math.floor(total / 60);
    const m = total % 60;
    return m ? `${h} h ${m} min` : `${h} h`;
  }
  const d = Math.floor(total / (24 * 60));
  const h = Math.floor((total % (24 * 60)) / 60);
  if (d >= 10 || h === 0) return `${d} d`;
  return `${d} d ${h} h`;
}

/** How long ago something happened, relative to now: "just now", "6 min ago", "2 h ago". */
export function formatAgo(ms: number): string {
  if (!Number.isFinite(ms) || ms < MINUTE) return "just now";
  const min = Math.floor(ms / MINUTE);
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
}

type Parts = { weekday: string; day: string; month: string; year: string; hour: string; minute: string };

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(zone: Zone): Intl.DateTimeFormat {
  const key = zone;
  let f = formatters.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: zone === "utc" ? "UTC" : undefined,
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    formatters.set(key, f);
  }
  return f;
}

export function parts(ms: number, zone: Zone): Parts {
  const out: Parts = { weekday: "", day: "", month: "", year: "", hour: "", minute: "" };
  for (const p of formatter(zone).formatToParts(new Date(ms))) {
    if (p.type in out) out[p.type as keyof Parts] = p.value;
  }
  return out;
}

let localZoneName: string | null = null;

/** "UTC", or the reader's short zone name such as "GMT+5:30" or "PST". */
export function zoneLabel(zone: Zone, ms: number = Date.now()): string {
  if (zone === "utc") return "UTC";
  if (localZoneName) return localZoneName;
  try {
    const name = new Intl.DateTimeFormat("en-US", { timeZoneName: "short" })
      .formatToParts(new Date(ms))
      .find((p) => p.type === "timeZoneName")?.value;
    localZoneName = name || "local time";
  } catch {
    localZoneName = "local time";
  }
  return localZoneName;
}

/** "18:59 UTC" */
export function formatClock(ms: number, zone: Zone, withZone = true): string {
  const p = parts(ms, zone);
  const clock = `${p.hour}:${p.minute}`;
  return withZone ? `${clock} ${zoneLabel(zone, ms)}` : clock;
}

/** "Tue 3 Nov" */
export function formatDay(ms: number, zone: Zone): string {
  const p = parts(ms, zone);
  return `${p.weekday} ${p.day} ${p.month}`;
}

/** "3 Nov 2026" */
export function formatDate(ms: number, zone: Zone): string {
  const p = parts(ms, zone);
  return `${p.day} ${p.month} ${p.year}`;
}

/** "Tue 3 Nov, 18:59 UTC" */
export function formatDateTime(ms: number, zone: Zone, withZone = true): string {
  return `${formatDay(ms, zone)}, ${formatClock(ms, zone, withZone)}`;
}

function sameDay(a: number, b: number, zone: Zone): boolean {
  const pa = parts(a, zone);
  const pb = parts(b, zone);
  return pa.day === pb.day && pa.month === pb.month && pa.year === pb.year;
}

/**
 * A clock time, with the day only when it is not the same day as `reference`:
 * "18:59 UTC" or "Wed 4 Nov, 02:10 UTC".
 */
export function formatClockNear(ms: number, reference: number, zone: Zone): string {
  return sameDay(ms, reference, zone) ? formatClock(ms, zone) : formatDateTime(ms, zone);
}

/** "18:30 to 21:18 UTC", or with days when the range crosses midnight or is not today. */
export function formatTimeRange(a: number, b: number, zone: Zone, reference: number = a): string {
  if (sameDay(a, b, zone) && sameDay(a, reference, zone)) {
    return `${formatClock(a, zone, false)} to ${formatClock(b, zone)}`;
  }
  if (sameDay(a, b, zone)) {
    return `${formatDay(a, zone)}, ${formatClock(a, zone, false)} to ${formatClock(b, zone)}`;
  }
  return `${formatDateTime(a, zone, false)} to ${formatDateTime(b, zone)}`;
}

const integer = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

/** "1,284" */
export function formatCount(n: number): string {
  return integer.format(n);
}

/** A 0 to 1 share as a percentage: "31%", "4.2%", "under 0.1%". */
export function formatShare(share: number, digits?: number): string {
  if (!Number.isFinite(share)) return "unknown";
  const pct = share * 100;
  if (pct > 0 && pct < 0.1) return "under 0.1%";
  const d = digits ?? (pct < 10 && pct !== Math.round(pct) ? 1 : 0);
  return `${pct.toFixed(d)}%`;
}

/** A plain decimal with fixed digits and an ASCII minus sign. */
export function formatDecimal(n: number, digits = 2): string {
  if (!Number.isFinite(n)) return "unknown";
  return n.toFixed(digits);
}

/** "1 incident", "2 incidents" */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${formatCount(n)} ${n === 1 ? one : many}`;
}

/** "Amazon Web Services'" and "Cloudflare's". */
export function possessive(name: string): string {
  return /s$/i.test(name) ? `${name}'` : `${name}'s`;
}

/** Sentence case for vendor vocabulary such as "partial_outage" or "in_progress". */
export function humanize(value: string): string {
  const s = value.replace(/_/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}
