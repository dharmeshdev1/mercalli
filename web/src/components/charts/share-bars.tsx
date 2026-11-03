import { formatCount, formatShare } from "@/lib/format";

export interface ShareRow {
  id: string;
  name: string;
  share: number;
  count?: number;
  /** Not a provider (no provider in this role, or providers outside the taxonomy): drawn quieter. */
  muted?: boolean;
}

/**
 * Ranked horizontal bars, one color, value at the tip. Used for provider concentration and
 * feature importance. Bars share one scale across the list so lengths compare honestly.
 */
export function ShareBars({
  rows,
  max,
  format = (r) => formatShare(r.share),
  highlight,
  label,
}: {
  rows: ShareRow[];
  /** The value that fills the track; defaults to the largest in the list. */
  max?: number;
  format?: (r: ShareRow) => string;
  highlight?: string | null;
  label: string;
}) {
  const top = max ?? Math.max(0.0001, ...rows.map((r) => r.share));
  return (
    <ul className="space-y-2.5" aria-label={label}>
      {rows.map((r) => {
        const w = Math.max(0.6, (r.share / top) * 100);
        const on = (highlight == null || highlight === r.id) && !r.muted;
        return (
          <li key={r.id} className="text-xs">
            <div className="flex items-baseline justify-between gap-3">
              <span className={`truncate ${on ? "text-ink" : "text-ink-soft"}`}>{r.name}</span>
              <span className="flex-none text-ink-soft">
                <span className="font-medium text-ink">{format(r)}</span>
                {r.count != null ? <span className="ml-2">{formatCount(r.count)}</span> : null}
              </span>
            </div>
            <div className="mt-1 h-1.5 w-full rounded-full bg-grid" aria-hidden="true">
              <div
                className="h-1.5 rounded-full transition-[width,background-color] duration-300"
                style={{ width: `${w}%`, background: on ? "var(--ink)" : "var(--ink-faint)" }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
