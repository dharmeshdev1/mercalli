"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchDoc, fetchDocShared, isAbort, isMissing } from "@/lib/api/client";

export type DocStatus = "loading" | "ready" | "missing" | "error";

export interface DocState<T> {
  status: DocStatus;
  data: T | null;
  /** The data before the latest successful refresh, for spotting what is new. */
  previous: T | null;
  /** The latest error. With data present, the data is stale and stays on screen. */
  error: Error | null;
  fixture: boolean;
  fetchedAt: number | null;
}

interface Internal<T> extends DocState<T> {
  path: string | null;
}

export interface UseDocOptions<T> {
  /** Refetch on this interval while the tab is visible. */
  pollMs?: number;
  transform?: (raw: unknown) => T;
}

function initial<T>(path: string | null): Internal<T> {
  return { path, status: "loading", data: null, previous: null, error: null, fixture: false, fetchedAt: null };
}

/**
 * Load one API document, optionally polling while the tab is visible. A failed refresh keeps
 * the last good data and reports the error, so pages can say the data is stale instead of
 * blanking. A 404 is "missing": the document is not published yet.
 */
export function useDoc<T>(path: string | null, options: UseDocOptions<T> = {}): DocState<T> & { refresh: () => void } {
  const { pollMs } = options;
  const [state, setState] = useState<Internal<T>>(() => initial(path));
  const [nonce, setNonce] = useState(0);
  const transform = useRef(options.transform);

  useEffect(() => {
    transform.current = options.transform;
  });

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    let lastFetch = 0;

    const load = async () => {
      controller?.abort();
      const own = new AbortController();
      controller = own;
      lastFetch = Date.now();
      try {
        // Polled documents always go to the network; one-off reads share a request per page.
        const { data, fixture } = pollMs ? await fetchDoc<unknown>(path, own.signal) : await fetchDocShared<unknown>(path);
        if (cancelled) return;
        const value = transform.current ? transform.current(data) : (data as T);
        setState((s) => ({
          path,
          status: "ready",
          data: value,
          previous: s.path === path ? s.data : null,
          error: null,
          fixture,
          fetchedAt: Date.now(),
        }));
      } catch (err) {
        if (cancelled || isAbort(err)) return;
        const error = err instanceof Error ? err : new Error(String(err));
        setState((s) => {
          const base = s.path === path ? s : initial<T>(path);
          if (base.data != null) return { ...base, error };
          return { ...base, status: isMissing(err) ? "missing" : "error", error };
        });
      }
    };

    void load();
    let timer: number | undefined;
    if (pollMs) {
      timer = window.setInterval(() => {
        if (document.visibilityState === "visible") void load();
      }, pollMs);
    }
    const onVisible = () => {
      if (pollMs && document.visibilityState === "visible" && Date.now() - lastFetch > pollMs) void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer) window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [path, pollMs, nonce]);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);
  const current = state.path === path ? state : initial<T>(path);
  return { ...current, refresh };
}
