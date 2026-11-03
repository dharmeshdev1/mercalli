"use client";

import type { Meta } from "@/lib/api/types";
import { useDoc, type DocState } from "@/lib/api/use-doc";

export type ArtifactName = "graph.json" | "model.json" | "census-summary.json" | "replays.json";

/**
 * Load an offline artifact. meta.json says whether the model card and the census exist, so those
 * are only requested when published; graph and replays have no flag and are requested directly.
 * In development every artifact is requested, so a missing one falls back to its fixture.
 */
export function useArtifact<T>(name: ArtifactName): DocState<T> & { refresh: () => void } {
  const meta = useDoc<Meta>("meta.json");
  let path: string | null = name;
  let known = true;
  if (process.env.NODE_ENV !== "development" && (name === "model.json" || name === "census-summary.json")) {
    if (meta.status === "loading") path = null;
    else if (meta.data) {
      const published = name === "model.json" ? meta.data.models != null : meta.data.census != null;
      if (!published) {
        path = null;
        known = false;
      }
    }
  }
  const doc = useDoc<T>(path);
  if (!known) return { ...doc, status: "missing" };
  return doc;
}
