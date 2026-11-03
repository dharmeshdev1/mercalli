"use client";

import Link from "next/link";
import type { Intensity } from "@/lib/api/types";
import { intensityMeaning, intensityTone, NUMERALS } from "@/lib/intensity";

/** The intensity readout: numeral and label, the ten step scale with the current level raised, and its meaning. */
export function IntensityReadout({ intensity }: { intensity: Intensity | null | undefined }) {
  if (!intensity) {
    return (
      <div className="min-h-[164px]" aria-busy="true">
        <p className="text-ink-soft">Reading the current intensity</p>
      </div>
    );
  }
  const i = intensity;
  return (
    <div>
      <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-lg text-ink-soft">Intensity</span>
        <span className="display text-3xl font-light leading-none sm:text-4xl">{i.numeral}</span>
        <span className="text-xl font-medium">{i.label}</span>
      </p>
      <IntensityScale level={i.level} />
      <p className="mt-2.5 text-ink-soft">{intensityMeaning(i)}</p>
      <p className="mt-1.5">
        <Link href="/model/#intensity" className="text-xs">
          How intensity is measured
        </Link>
      </p>
    </div>
  );
}

/** Ten steps on the Mercalli color ramp; the current level stands taller and carries its numeral. */
export function IntensityScale({ level }: { level: number }) {
  return (
    <div className="mt-4" role="img" aria-label={`Level ${NUMERALS[level - 1] ?? level} of ten`}>
      <div className="grid grid-cols-10 items-end gap-[3px]">
        {NUMERALS.map((n, idx) => {
          const l = idx + 1;
          const tone = intensityTone(l);
          const current = l === level;
          return (
            <span
              key={n}
              className="block rounded-[1px]"
              style={{
                height: current ? 16 : 6,
                background: `var(--sev-${tone})`,
                opacity: current ? 1 : l < level ? 0.55 : 0.22,
                boxShadow: current ? `inset 0 0 0 1px var(--sev-${tone}-line)` : undefined,
              }}
            />
          );
        })}
      </div>
      <div className="mt-1.5 grid grid-cols-10 gap-[3px] text-[0.68rem] leading-none" aria-hidden="true">
        {NUMERALS.map((n, idx) => (
          <span key={n} className={idx + 1 === level ? "font-semibold text-ink" : "text-ink-soft"}>
            {idx === 0 || idx === 9 || idx + 1 === level ? n : ""}
          </span>
        ))}
      </div>
    </div>
  );
}
