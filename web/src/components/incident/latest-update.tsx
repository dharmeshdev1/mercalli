"use client";

import { useId, useState } from "react";
import { Time } from "@/components/ui/time";
import type { LatestUpdate as Latest } from "@/lib/api/types";
import { statusLabel } from "@/lib/severity";

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", "#39": "'" };

/**
 * Vendor text, lightly cleaned for display: HTML tags some vendors post (with line breaks kept),
 * common entities, markdown emphasis markers and runs of blank lines. The words are untouched.
 */
export function cleanBody(body: string): string {
  return body
    .replace(/<br\s*\/?>|<\/p>|<\/li>/gi, "\n")
    .replace(/<[^>]{1,200}>/g, "")
    .replace(/&(amp|lt|gt|quot|apos|nbsp|#39);/g, (_, e: string) => ENTITIES[e] ?? "")
    .replace(/\*\*|__/g, "")
    .replace(/\r/g, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** The latest vendor update, two lines by default and expandable. */
export function LatestUpdate({ latest }: { latest: Latest | null }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!latest || !latest.body.trim()) return null;
  const body = cleanBody(latest.body);
  const long = body.length > 150 || body.includes("\n");
  return (
    <div className="mt-3">
      <p className="text-xs text-ink-soft">
        {statusLabel(latest.status)} update at <Time iso={latest.at} />
      </p>
      <p id={id} className={`vendor-text mt-1 text-[0.9375rem] leading-relaxed text-ink ${open ? "whitespace-pre-line" : "line-clamp-2"}`}>
        {open ? body : body.replace(/\s+/g, " ")}
      </p>
      {long ? (
        <button
          type="button"
          className="mt-1 min-h-8 text-xs text-accent hover:underline"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Show less" : "Show the full update"}
        </button>
      ) : null}
    </div>
  );
}
