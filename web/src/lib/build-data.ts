import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import type { ServiceEntry, ServicesDoc } from "@/lib/api/types";

/**
 * Build-time read of services.json, used only to pre-render one page per service. The pages
 * fetch their data live; only the name, category and status page link are taken from here.
 */
let cached: ServiceEntry[] | null = null;

export function buildTimeServices(): ServiceEntry[] {
  if (cached) return cached;
  const file = process.env.MERCALLI_SERVICES_JSON ?? path.join(process.cwd(), "public", "api", "v1", "services.json");
  if (!existsSync(file)) {
    if (process.env.NODE_ENV === "production") {
      throw new Error(
        `services.json was not found at ${file}. Publish the API into web/public/api/v1 before building, or point MERCALLI_SERVICES_JSON at a copy.`,
      );
    }
    cached = [];
    return cached;
  }
  cached = (JSON.parse(readFileSync(file, "utf8")) as ServicesDoc).services;
  return cached;
}
