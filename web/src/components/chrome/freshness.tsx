"use client";

import { useOverview } from "@/components/overview-context";
import { useNow } from "@/lib/clock";
import { formatAgo, MINUTE, parseTime } from "@/lib/format";

/** Older than this, the data is called stale rather than merely updated a while ago. */
export const STALE_AFTER = 20 * MINUTE;

export interface Freshness {
  state: "loading" | "fresh" | "stale" | "error" | "unavailable";
  ageMs: number | null;
  text: string;
}

export function useFreshness(): Freshness {
  const ov = useOverview();
  const now = useNow(15_000);
  const generated = parseTime(ov.data?.generated_at);
  if (ov.status === "loading" || !now) return { state: "loading", ageMs: null, text: "Loading the latest record" };
  if (!ov.data || generated == null) {
    return { state: "unavailable", ageMs: null, text: "Live data is unavailable" };
  }
  const age = Math.max(0, now - generated);
  if (ov.error) return { state: "error", ageMs: age, text: `Refresh failed, showing data from ${formatAgo(age)}` };
  if (age > STALE_AFTER) return { state: "stale", ageMs: age, text: `Data is ${formatAgo(age).replace(" ago", "")} old` };
  return { state: "fresh", ageMs: age, text: `Updated ${formatAgo(age)}` };
}

/** The ticking "updated N min ago" clock, honest about staleness and failed refreshes. */
export function FreshnessClock({ className = "" }: { className?: string }) {
  const f = useFreshness();
  const warn = f.state === "stale" || f.state === "error" || f.state === "unavailable";
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs ${warn ? "text-ink" : "text-ink-soft"} ${className}`} aria-live={warn ? "polite" : "off"}>
      <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
        {warn ? (
          <circle cx="8" cy="8" r="5.25" fill="none" stroke="var(--sev-partial-line)" strokeWidth="1.7" strokeDasharray="0.01 2.75" strokeLinecap="round" />
        ) : (
          <>
            <circle cx="8" cy="8" r="5.25" fill="none" stroke="currentColor" strokeWidth="1.3" />
            <path d="M8 5v3.2l2 1.3" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </>
        )}
      </svg>
      <span suppressHydrationWarning>{f.text}</span>
    </span>
  );
}
