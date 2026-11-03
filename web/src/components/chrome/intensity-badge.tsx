"use client";

import Link from "next/link";
import { useOverview } from "@/components/overview-context";
import { intensityMeaning, intensityTone } from "@/lib/intensity";

/** The current intensity in the top bar: a ramp swatch, the numeral and its label. */
export function IntensityBadge({ compact = false }: { compact?: boolean }) {
  const { data } = useOverview();
  const i = data?.intensity;
  if (!i) return <span className="inline-block h-5 w-16" aria-hidden="true" />;
  const tone = intensityTone(i.level);
  return (
    <Link
      href="/model/#intensity"
      className="quiet-link inline-flex items-center gap-2 text-xs"
      title={`Intensity ${i.numeral}, ${i.label.toLowerCase()}. ${intensityMeaning(i)}`}
      aria-label={`Intensity ${i.numeral}, ${i.label.toLowerCase()}. How intensity is measured`}
    >
      <span
        aria-hidden="true"
        className="inline-block size-2.5 rounded-xs"
        style={{ background: `var(--sev-${tone})`, boxShadow: `inset 0 0 0 1px var(--sev-${tone}-line)` }}
      />
      <span className="font-semibold text-ink">{i.numeral}</span>
      {compact ? null : <span className="text-ink-soft">{i.label}</span>}
    </Link>
  );
}
