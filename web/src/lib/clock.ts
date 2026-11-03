"use client";

import { useSyncExternalStore } from "react";

/**
 * One shared ticker for every clock on the page. Components subscribe with a granularity and
 * re-render only when the rounded time changes. Before hydration the time is 0, which callers
 * treat as "not known yet" so server HTML never contains a stale clock.
 */

let now = 0;
let timer: number | undefined;
const listeners = new Set<() => void>();

function tick() {
  now = Date.now();
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  if (listeners.size === 0) {
    now = Date.now();
    timer = window.setInterval(tick, 1000);
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer) {
      window.clearInterval(timer);
      timer = undefined;
    }
  };
}

function snapshot(granularity: number): number {
  // The first read happens before the ticker starts; pin it so repeated reads agree.
  if (!now) now = Date.now();
  return Math.floor(now / granularity) * granularity;
}

/** Milliseconds since the epoch, rounded down to `granularity`; 0 before hydration. */
export function useNow(granularity = 1000): number {
  return useSyncExternalStore(
    subscribe,
    () => snapshot(granularity),
    () => 0,
  );
}
