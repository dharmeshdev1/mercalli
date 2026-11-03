// First load JS per route of the static export: every script a route's HTML loads before it
// becomes interactive, measured raw and gzipped. Run after `pnpm build`.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { gzipSync } from "node:zlib";

const out = join(import.meta.dirname, "..", "out");
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/mercalli";

function htmlFiles(dir) {
  const files = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name === "_next" || name === "api" || name === "duckdb" || name === "feeds") continue;
      files.push(...htmlFiles(p));
    } else if (name === "index.html") files.push(p);
  }
  return files;
}

const sizes = new Map();
function measure(src) {
  if (!sizes.has(src)) {
    const file = join(out, src.slice(basePath.length));
    const raw = readFileSync(file);
    sizes.set(src, { raw: raw.length, gz: gzipSync(raw).length });
  }
  return sizes.get(src);
}

const rows = [];
const seen = new Set();
for (const file of htmlFiles(out).sort()) {
  let route = "/" + relative(out, file).replace(/index\.html$/, "");
  if (route.startsWith("/s/") && route !== "/s/") {
    if (seen.has("/s/[id]/")) continue;
    seen.add("/s/[id]/");
    route = "/s/[id]/";
  }
  const html = readFileSync(file, "utf8");
  const srcs = [...html.matchAll(/<script[^>]+src="([^"]+\.js)"/g)].map((m) => m[1]);
  let raw = 0;
  let gz = 0;
  for (const src of new Set(srcs)) {
    const s = measure(src);
    raw += s.raw;
    gz += s.gz;
  }
  rows.push({ route, scripts: new Set(srcs).size, raw, gz, html: gzipSync(html).length });
}

const kb = (n) => (n / 1024).toFixed(1).padStart(7);
console.log("route".padEnd(20), "scripts", "   raw KB", "  gzip KB", " html gz KB");
for (const r of rows) console.log(r.route.padEnd(20), String(r.scripts).padStart(7), kb(r.raw), "  ", kb(r.gz), "  ", kb(r.html));
