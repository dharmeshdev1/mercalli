// Puts the DuckDB-WASM engine next to the site so the Explore page loads everything from our
// own origin: the engine (one wasm module and its worker, copied from node_modules) and the
// Parquet and JSON extensions it loads on demand. The extensions are not part of the npm
// package, so they are fetched once at build time from DuckDB's extension repository and
// cached under public/duckdb (git ignored). Nothing is fetched from a third party at runtime.
import { copyFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const pkgDir = join(root, "node_modules", "@duckdb", "duckdb-wasm");
const dist = join(pkgDir, "dist");
const out = join(root, "public", "duckdb");

// The DuckDB release each pinned duckdb-wasm package embeds. Extensions must match it exactly.
const ENGINE = { "1.32.0": "v1.4.3" };
const EXTENSIONS = ["parquet", "json"];
const PLATFORM = "wasm_eh";

const { version } = JSON.parse(readFileSync(join(pkgDir, "package.json"), "utf8"));
const engine = ENGINE[version];
if (!engine) {
  throw new Error(`No DuckDB release recorded for @duckdb/duckdb-wasm ${version}. Add it to ENGINE in scripts/copy-duckdb.mjs.`);
}

mkdirSync(out, { recursive: true });
for (const name of ["duckdb-eh.wasm", "duckdb-browser-eh.worker.js"]) {
  const from = join(dist, name);
  const to = join(out, name);
  if (!existsSync(from)) throw new Error(`missing ${from}: run pnpm install`);
  if (existsSync(to) && statSync(to).size === statSync(from).size) continue;
  copyFileSync(from, to);
  console.log(`copied ${name}`);
}

const extDir = join(out, "extensions", engine, PLATFORM);
mkdirSync(extDir, { recursive: true });
for (const ext of EXTENSIONS) {
  const file = join(extDir, `${ext}.duckdb_extension.wasm`);
  if (existsSync(file) && statSync(file).size > 0) continue;
  const url = `https://extensions.duckdb.org/${engine}/${PLATFORM}/${ext}.duckdb_extension.wasm`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`could not download the ${ext} extension from ${url} (HTTP ${res.status})`);
  const bytes = new Uint8Array(await res.arrayBuffer());
  // A wasm module starts with \0asm; anything else is an error page.
  if (bytes[0] !== 0x00 || bytes[1] !== 0x61 || bytes[2] !== 0x73 || bytes[3] !== 0x6d) {
    throw new Error(`${url} did not return a wasm module`);
  }
  writeFileSync(file, bytes);
  console.log(`fetched the ${ext} extension for DuckDB ${engine}`);
}
