"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { arrivals, normalizeOverview } from "@/lib/api/normalize";
import type { Overview } from "@/lib/api/types";
import { useDoc, type DocState } from "@/lib/api/use-doc";

export interface OverviewState extends DocState<Overview> {
  /** Ids of active incidents that appeared on the latest poll. */
  arrivals: Set<string>;
  refresh: () => void;
}

const OverviewContext = createContext<OverviewState | null>(null);

/** Polls overview.json every 60 seconds while the tab is visible, for the whole app. */
export function OverviewProvider({ children }: { children: ReactNode }) {
  const doc = useDoc<Overview>("overview.json", { pollMs: 60_000, transform: normalizeOverview });
  const fresh = useMemo(() => arrivals(doc.previous, doc.data), [doc.previous, doc.data]);
  const value = useMemo<OverviewState>(() => ({ ...doc, arrivals: fresh }), [doc, fresh]);
  return <OverviewContext.Provider value={value}>{children}</OverviewContext.Provider>;
}

export function useOverview(): OverviewState {
  const ctx = useContext(OverviewContext);
  if (!ctx) throw new Error("useOverview must be used inside OverviewProvider");
  return ctx;
}
