"use client";

import { useCallback, useSyncExternalStore } from "react";
import type { Zone } from "@/lib/format";
import { THEME_KEY, ZONE_KEY } from "@/lib/theme-script";

/**
 * Reader preferences kept in localStorage (theme, time zone). Every access is wrapped in
 * try/catch: storage can be missing or blocked, and the site must work without it.
 */

export type Theme = "system" | "light" | "dark";

const listeners = new Set<() => void>();
const cache = new Map<string, string | null>();

function read(key: string): string | null {
  if (cache.has(key)) return cache.get(key) ?? null;
  let value: string | null = null;
  try {
    value = window.localStorage.getItem(key);
  } catch {
    value = null;
  }
  cache.set(key, value);
  return value;
}

function write(key: string, value: string | null): void {
  cache.set(key, value);
  try {
    if (value == null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Storage is unavailable; the choice still applies for this visit.
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key === THEME_KEY || e.key === ZONE_KEY) {
      cache.delete(e.key);
      listener();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function readTheme(): Theme {
  const v = read(THEME_KEY);
  return v === "light" || v === "dark" ? v : "system";
}

function readZone(): Zone {
  return read(ZONE_KEY) === "local" ? "local" : "utc";
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const theme = useSyncExternalStore(subscribe, readTheme, () => "system" as Theme);
  const set = useCallback((t: Theme) => {
    applyTheme(t);
    write(THEME_KEY, t === "system" ? null : t);
  }, []);
  return [theme, set];
}

export function useZone(): [Zone, (z: Zone) => void] {
  const zone = useSyncExternalStore(subscribe, readZone, () => "utc" as Zone);
  const set = useCallback((z: Zone) => write(ZONE_KEY, z === "utc" ? null : z), []);
  return [zone, set];
}

const DARK_QUERY = "(prefers-color-scheme: dark)";

function subscribeScheme(listener: () => void): () => void {
  const mq = window.matchMedia(DARK_QUERY);
  mq.addEventListener("change", listener);
  const unsub = subscribe(listener);
  return () => {
    mq.removeEventListener("change", listener);
    unsub();
  };
}

function readResolved(): "light" | "dark" {
  const t = readTheme();
  if (t !== "system") return t;
  return window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
}

/** The theme actually showing, for drawing code that reads colors from CSS. */
export function useResolvedTheme(): "light" | "dark" {
  return useSyncExternalStore(subscribeScheme, readResolved, () => "light" as const);
}

const MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function subscribeMotion(listener: () => void): () => void {
  const mq = window.matchMedia(MOTION_QUERY);
  mq.addEventListener("change", listener);
  return () => mq.removeEventListener("change", listener);
}

/** True when the reader asked for reduced motion. */
export function useReducedMotionPref(): boolean {
  return useSyncExternalStore(
    subscribeMotion,
    () => window.matchMedia(MOTION_QUERY).matches,
    () => false,
  );
}

/** Whether a media query matches, kept current; false before hydration. */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (listener) => {
      const mq = window.matchMedia(query);
      mq.addEventListener("change", listener);
      return () => mq.removeEventListener("change", listener);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}
