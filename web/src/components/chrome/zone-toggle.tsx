"use client";

import { zoneLabel, type Zone } from "@/lib/format";
import { useZone } from "@/lib/prefs";
import { useNow } from "@/lib/clock";

/** Times in UTC or the reader's own zone. One choice for the whole site. */
export function ZoneToggle({ className = "" }: { className?: string }) {
  const [zone, setZone] = useZone();
  const now = useNow(60_000);
  const options: { value: Zone; label: string }[] = [
    { value: "utc", label: "UTC" },
    { value: "local", label: now ? `Local (${zoneLabel("local", now)})` : "Local" },
  ];
  return (
    <div role="group" aria-label="Time zone" className={`inline-flex rounded-sm border border-rule p-0.5 ${className}`}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={zone === o.value}
          onClick={() => setZone(o.value)}
          className={`min-h-8 rounded-xs px-2.5 text-xs transition-colors ${
            zone === o.value ? "bg-ink text-paper" : "text-ink-soft hover:bg-hover hover:text-ink"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
