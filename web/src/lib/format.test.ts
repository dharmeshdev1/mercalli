import { describe, expect, it } from "vitest";
import { formatAgo, formatClock, formatDuration, formatShare, formatTimeRange, HOUR, MINUTE, parseTime, possessive } from "./format";

describe("formatDuration", () => {
  it("says minutes, hours and days the way people do", () => {
    expect(formatDuration(0.4)).toBe("under a minute");
    expect(formatDuration(41)).toBe("41 min");
    expect(formatDuration(60)).toBe("1 h");
    expect(formatDuration(312)).toBe("5 h 12 min");
    expect(formatDuration(3000)).toBe("2 d 2 h");
    expect(formatDuration(2880)).toBe("2 d");
    expect(formatDuration(18632.6)).toBe("12 d");
  });

  it("refuses to print nonsense", () => {
    expect(formatDuration(null)).toBe("unknown");
    expect(formatDuration(-88.7)).toBe("unknown");
    expect(formatDuration(Number.NaN)).toBe("unknown");
  });
});

describe("formatAgo", () => {
  it("rounds down to the unit that matters", () => {
    expect(formatAgo(20_000)).toBe("just now");
    expect(formatAgo(6 * MINUTE + 59_000)).toBe("6 min ago");
    expect(formatAgo(2 * HOUR + 5 * MINUTE)).toBe("2 h ago");
    expect(formatAgo(72 * HOUR)).toBe("3 d ago");
  });
});

describe("clock formatting", () => {
  const t = parseTime("2026-11-02T18:59:00Z") ?? 0;

  it("formats a UTC clock with its zone", () => {
    expect(formatClock(t, "utc")).toBe("18:59 UTC");
    expect(formatClock(t, "utc", false)).toBe("18:59");
  });

  it("writes ranges with words, never a dash", () => {
    const a = parseTime("2026-11-02T18:30:00Z") ?? 0;
    const b = parseTime("2026-11-02T21:18:00Z") ?? 0;
    expect(formatTimeRange(a, b, "utc")).toBe("18:30 to 21:18 UTC");
    const c = parseTime("2026-11-03T02:10:00Z") ?? 0;
    expect(formatTimeRange(a, c, "utc")).toBe("Mon 2 Nov, 18:30 to Tue 3 Nov, 02:10 UTC");
    expect(formatTimeRange(a, c, "utc")).not.toMatch(/[\u2013\u2014]/);
  });

  it("reads the space-separated form some artifacts use", () => {
    expect(parseTime("2025-07-01 00:00:00+00:00")).toBe(Date.parse("2025-07-01T00:00:00Z"));
  });

  it("returns null for unparseable times", () => {
    expect(parseTime("not a time")).toBeNull();
    expect(parseTime(null)).toBeNull();
  });
});

describe("possessive", () => {
  it("handles names that end in s", () => {
    expect(possessive("Amazon Web Services")).toBe("Amazon Web Services'");
    expect(possessive("Cloudflare")).toBe("Cloudflare's");
  });
});

describe("formatShare", () => {
  it("keeps one decimal only for small shares", () => {
    expect(formatShare(0.31)).toBe("31%");
    expect(formatShare(0.042)).toBe("4.2%");
    expect(formatShare(0.0004)).toBe("under 0.1%");
  });
});
