"use client";

import { useNow } from "@/lib/clock";
import {
  formatAgo,
  formatClock,
  formatClockNear,
  formatDate,
  formatDateTime,
  formatDuration,
  MINUTE,
  parseTime,
} from "@/lib/format";
import { useZone } from "@/lib/prefs";

type Style = "clock" | "near" | "datetime" | "date";

/**
 * A time in the reader's chosen zone (UTC by default). Renders nothing until hydrated, so the
 * static HTML never carries a time formatted for the wrong zone.
 */
export function Time({ iso, style = "near", className }: { iso: string | null | undefined; style?: Style; className?: string }) {
  const [zone] = useZone();
  const now = useNow(MINUTE);
  const ms = parseTime(iso);
  if (ms == null) return null;
  let text = "";
  if (now) {
    if (style === "clock") text = formatClock(ms, zone);
    else if (style === "datetime") text = formatDateTime(ms, zone);
    else if (style === "date") text = formatDate(ms, zone);
    else text = formatClockNear(ms, now, zone);
  }
  return (
    <time dateTime={iso ?? undefined} className={className} suppressHydrationWarning>
      {text}
    </time>
  );
}

/** Time since `iso`, ticking each minute: "20 h 12 min". */
export function Elapsed({ iso, className }: { iso: string; className?: string }) {
  const now = useNow(MINUTE);
  const start = parseTime(iso);
  if (start == null || !now) return <span className={className}>&nbsp;</span>;
  return <span className={className}>{formatDuration((now - start) / MINUTE)}</span>;
}

/** "6 min ago", ticking. */
export function Ago({ iso, className }: { iso: string | null | undefined; className?: string }) {
  const now = useNow(15_000);
  const t = parseTime(iso);
  if (t == null || !now) return <span className={className}>&nbsp;</span>;
  return (
    <time dateTime={iso ?? undefined} className={className}>
      {formatAgo(now - t)}
    </time>
  );
}
