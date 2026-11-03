import type { CensusSummary, GraphDoc, ModelCard, ReplaysDoc } from "../src/lib/api/types.ts";
import { censusFixture } from "./census-summary.ts";
import { graphFixture } from "./graph.ts";
import { modelFixture } from "./model.ts";
import { replaysFixture } from "./replays.ts";

/**
 * Typed fixtures for the artifacts the pipeline has not published yet. The web app reaches this
 * module only through a development-only dynamic import (see src/lib/api/client.ts), so none of
 * it is part of a production build.
 */
export function fixtureFor(path: string): GraphDoc | ModelCard | CensusSummary | ReplaysDoc | undefined {
  switch (path) {
    case "graph.json":
      return graphFixture();
    case "model.json":
      return modelFixture;
    case "census-summary.json":
      return censusFixture;
    case "replays.json":
      return replaysFixture;
    default:
      return undefined;
  }
}

/** Every fixture as the file it stands in for, relative to the API root (or site root for data/). */
export function allFixtureFiles(): Record<string, unknown> {
  return {
    "api/v1/graph.json": graphFixture(),
    "api/v1/model.json": modelFixture,
    "api/v1/census-summary.json": censusFixture,
    "api/v1/replays.json": replaysFixture,
  };
}
