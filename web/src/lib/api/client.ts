/**
 * The typed client. Every document is fetched live from the API on the client; in development
 * only, a document that is not published yet (404) falls back to a typed fixture from
 * web/fixtures. The fallback sits behind a NODE_ENV check that the production build removes,
 * so fixtures are never bundled.
 */

/** The site's base path, "" when served from the root. */
export const BASE_PATH = process.env.MERCALLI_BASE_PATH ?? "";
/** Where the JSON API lives. */
export const API_BASE = process.env.MERCALLI_API_BASE ?? `${BASE_PATH}/api/v1`;

export const REPO_URL = "https://github.com/dharmeshdev1/mercalli";

export function apiUrl(path: string): string {
  return `${API_BASE}/${path.replace(/^\/+/, "")}`;
}

/** A file served next to the site, such as a feed or the incident dataset. */
export function siteUrl(path: string): string {
  return `${BASE_PATH}/${path.replace(/^\/+/, "")}`;
}

export class ApiError extends Error {
  readonly status: number;
  readonly path: string;

  constructor(status: number, path: string, message?: string) {
    super(message ?? `Could not load ${path} (HTTP ${status})`);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
  }
}

export interface Loaded<T> {
  data: T;
  /** True when the document came from a development fixture. */
  fixture: boolean;
}

const SAFE_PART = /^[A-Za-z0-9_][A-Za-z0-9._-]*$/;

/** "github:db4x8hh362t8" to "incidents/github/db4x8hh362t8.json", or null for anything unsafe. */
export function incidentPath(id: string): string | null {
  const i = id.indexOf(":");
  if (i <= 0) return null;
  const service = id.slice(0, i);
  const incident = id.slice(i + 1);
  if (!SAFE_PART.test(service) || !SAFE_PART.test(incident)) return null;
  return `incidents/${service}/${incident}.json`;
}

export function servicePath(id: string): string | null {
  return SAFE_PART.test(id) ? `services/${id}.json` : null;
}

async function developmentFixture(path: string): Promise<unknown> {
  if (process.env.NODE_ENV === "development") {
    const { fixtureFor } = await import("@fixtures/index");
    return fixtureFor(path);
  }
  return undefined;
}

export async function fetchDoc<T>(path: string, signal?: AbortSignal): Promise<Loaded<T>> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), { cache: "no-cache", signal });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new ApiError(0, path, `Could not reach the API for ${path}`);
  }
  if (res.ok) {
    try {
      return { data: (await res.json()) as T, fixture: false };
    } catch {
      throw new ApiError(res.status, path, `${path} is not valid JSON`);
    }
  }
  if (res.status === 404 && process.env.NODE_ENV === "development") {
    const fixture = await developmentFixture(path);
    if (fixture !== undefined) return { data: fixture as T, fixture: true };
  }
  throw new ApiError(res.status, path);
}

const cache = new Map<string, { at: number; promise: Promise<Loaded<unknown>> }>();
const CACHE_MS = 30_000;

/**
 * fetchDoc with a short-lived cache, so several components reading the same document on one page
 * share one request. Failures are not cached.
 */
export function fetchDocShared<T>(path: string): Promise<Loaded<T>> {
  const hit = cache.get(path);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.promise as Promise<Loaded<T>>;
  const promise = fetchDoc<T>(path).catch((err) => {
    cache.delete(path);
    throw err;
  });
  cache.set(path, { at: Date.now(), promise });
  return promise;
}

export function isMissing(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}

export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}
