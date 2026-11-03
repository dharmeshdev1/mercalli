import Link from "next/link";
import type { BlastEntry } from "@/lib/api/types";
import { formatShare } from "@/lib/format";

/** Services likely to post an incident of their own because of this one, most likely first. */
export function BlastRow({ blast, limit = 5 }: { blast: BlastEntry[] | null; limit?: number }) {
  if (!blast || blast.length === 0) return null;
  const shown = blast.slice(0, limit);
  return (
    <div className="mt-4 border-t border-grid pt-3">
      <p className="text-xs text-ink-soft">Likely to post an incident of their own</p>
      <ul className="mt-2 flex flex-wrap gap-2">
        {shown.map((b) => (
          <li key={b.service_id}>
            <Link
              href={`/s/${b.service_id}/`}
              className="quiet-link inline-flex min-h-8 items-center gap-2 rounded-xs border border-rule px-2.5 text-xs hover:bg-hover"
              title={b.evidence.length ? `Evidence: ${b.evidence.join(", ")}` : undefined}
            >
              <span className="font-medium text-ink">{b.service_name}</span>
              <span className="text-ink-soft">{formatShare(b.probability)}</span>
            </Link>
          </li>
        ))}
      </ul>
      {blast.length > shown.length ? <p className="mt-2 text-xs text-ink-soft">and {blast.length - shown.length} more on the incident page</p> : null}
    </div>
  );
}
