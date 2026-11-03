// Writes the development fixtures as the files they stand in for, so a production build can be
// previewed with every artifact present: node scripts/dump-fixtures.ts <site-dir>
// The files land under <site-dir>/api/v1/ and <site-dir>/data/.
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { allFixtureFiles } from "../fixtures/index.ts";
import { incidentRowsFixture } from "../fixtures/incidents.ts";

const target = process.argv[2];
if (!target) {
  console.error("usage: node scripts/dump-fixtures.ts <site-dir>");
  process.exit(1);
}
for (const [rel, doc] of Object.entries(allFixtureFiles())) {
  const file = join(target, rel);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, JSON.stringify(doc));
  console.log(`wrote ${rel}`);
}
const rows = join(target, "data", "incidents.fixture.json");
mkdirSync(dirname(rows), { recursive: true });
writeFileSync(rows, JSON.stringify(incidentRowsFixture()));
console.log("wrote data/incidents.fixture.json (convert to parquet to preview Explore)");
