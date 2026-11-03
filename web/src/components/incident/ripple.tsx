"use client";

import { useEffect, useState } from "react";
import { useReducedMotionPref } from "@/lib/prefs";

/**
 * An epicenter ripple: three rings expand once from the point where a new incident arrived.
 * With reduced motion, a still ring marks the arrival for ten seconds instead.
 */
export function Ripple({ color = "var(--accent)" }: { color?: string }) {
  const reduced = useReducedMotionPref();
  const [done, setDone] = useState(false);
  useEffect(() => {
    const t = window.setTimeout(() => setDone(true), reduced ? 10_000 : 1600);
    return () => window.clearTimeout(t);
  }, [reduced]);
  if (done) return null;
  if (reduced) {
    return (
      <span
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 left-1/2 size-9 -translate-x-1/2 -translate-y-1/2 rounded-full border-2"
        style={{ borderColor: color }}
      />
    );
  }
  return (
    <span aria-hidden="true" className="pointer-events-none absolute top-1/2 left-1/2">
      {[0, 160, 320].map((delay) => (
        <span
          key={delay}
          className="absolute top-0 left-0 block size-16 rounded-full border-[1.5px]"
          style={{ borderColor: color, animation: `ripple 900ms cubic-bezier(0.2, 0, 0, 1) ${delay}ms both` }}
        />
      ))}
    </span>
  );
}
