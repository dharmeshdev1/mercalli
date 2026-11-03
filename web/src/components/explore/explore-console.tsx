"use client";

import type { AsyncDuckDB, AsyncDuckDBConnection } from "@duckdb/duckdb-wasm";
import { useCallback, useId, useRef, useState, type KeyboardEvent } from "react";
import { PageHeader } from "@/components/ui/page-header";
import { BASE_PATH } from "@/lib/api/client";
import type { Meta } from "@/lib/api/types";
import { useDoc } from "@/lib/api/use-doc";
import { Time } from "@/components/ui/time";
import { formatCount } from "@/lib/format";

/**
 * SQL over the incident history, run in the browser by DuckDB-WASM. The engine and the dataset
 * are fetched from this site only when the reader runs a first query; nothing is sent anywhere.
 */

const EXAMPLES: { label: string; sql: string }[] = [
  {
    label: "Longest incidents by provider this year",
    sql: `-- The longest incident each provider posted this year (UTC)
SELECT service_id AS provider,
       count(*) AS incidents,
       round(max(duration_min) / 60, 1) AS longest_hours
FROM incidents
WHERE year(start::TIMESTAMP) = (SELECT max(year(start::TIMESTAMP)) FROM incidents)
  AND duration_min IS NOT NULL
GROUP BY provider
ORDER BY longest_hours DESC
LIMIT 20`,
  },
  {
    label: "Incidents per weekday",
    sql: `-- When incidents begin, by day of the week (UTC)
SELECT dayname(start::TIMESTAMP) AS weekday,
       count(*) AS incidents,
       round(100 * count(*) / sum(count(*)) OVER (), 1) AS percent
FROM incidents
GROUP BY weekday, isodow(start::TIMESTAMP)
ORDER BY isodow(start::TIMESTAMP)`,
  },
  {
    label: "Share posted after they ended",
    sql: `-- Incidents a vendor only posted once they were over
SELECT category,
       count(*) AS incidents,
       round(100 * avg(CASE WHEN retroactive THEN 1 ELSE 0 END), 1) AS percent_posted_after
FROM incidents
GROUP BY category
ORDER BY percent_posted_after DESC`,
  },
  {
    label: "Median length by category and impact",
    sql: `-- Typical length of an incident, by category and impact
SELECT category, impact,
       count(*) AS incidents,
       round(median(duration_min)) AS median_minutes
FROM incidents
WHERE duration_min IS NOT NULL
  AND impact IN ('minor', 'major', 'critical')
GROUP BY category, impact
ORDER BY category, median_minutes DESC`,
  },
  {
    label: "Providers named most by others",
    sql: `-- Which providers other services name in their own incident reports
SELECT provider,
       count(*) AS incidents,
       count(DISTINCT service_id) AS services
FROM (SELECT unnest(mentions) AS provider, service_id FROM incidents)
GROUP BY provider
ORDER BY incidents DESC
LIMIT 20`,
  },
];

const SHOW_ROWS = 500;

class MissingDataset extends Error {}

interface Engine {
  db: AsyncDuckDB;
  conn: AsyncDuckDBConnection;
  source: "parquet" | "fixture";
  columns: { name: string; type: string }[];
}

let engine: Promise<Engine> | null = null;

/** Read a response body, reporting whole percents when the size is known. */
async function readWithProgress(res: Response, onPercent: (p: number) => void): Promise<Uint8Array> {
  const total = Number(res.headers.get("content-length")) || 0;
  const reader = res.body?.getReader();
  if (!reader || !total) return new Uint8Array(await res.arrayBuffer());
  const out = new Uint8Array(total);
  let got = 0;
  let last = -1;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (got + value.length > out.length) return new Uint8Array(await new Blob([out.subarray(0, got), value]).arrayBuffer());
    out.set(value, got);
    got += value.length;
    const p = Math.floor((100 * got) / total);
    if (p !== last) {
      last = p;
      onPercent(p);
    }
  }
  return got === total ? out : out.subarray(0, got);
}

async function startEngine(onStatus: (s: string) => void): Promise<Engine> {
  const duckdb = await import("@duckdb/duckdb-wasm");
  const base = `${window.location.origin}${BASE_PATH}`;
  onStatus("Loading the query engine");
  const worker = new Worker(`${base}/duckdb/duckdb-browser-eh.worker.js`);
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker);
  await db.instantiate(`${base}/duckdb/duckdb-eh.wasm`, null, (p) => {
    if (p.bytesTotal > 0) onStatus(`Loading the query engine, ${Math.round((100 * p.bytesLoaded) / p.bytesTotal)}%`);
  });
  const conn = await db.connect();
  // Extensions come from this site, never from a third party: no automatic installs, and the
  // repository points at the copies served next to the engine.
  await conn.query("SET autoinstall_known_extensions = false");
  await conn.query("SET autoload_known_extensions = false");
  await conn.query(`SET custom_extension_repository = '${base}/duckdb/extensions'`);
  onStatus("Reading the incident dataset");
  const res = await fetch(`${base}/data/incidents.parquet`, { cache: "no-cache" });
  let source: Engine["source"] = "parquet";
  if (res.ok) {
    await conn.query("LOAD parquet");
    await db.registerFileBuffer("incidents.parquet", await readWithProgress(res, (p) => onStatus(`Reading the incident dataset, ${p}%`)));
    await conn.query("CREATE VIEW incidents AS SELECT * FROM read_parquet('incidents.parquet')");
  } else if (process.env.NODE_ENV === "development") {
    const { incidentRowsFixture } = await import("@fixtures/incidents");
    await conn.query("LOAD json");
    await db.registerFileText("incidents.json", JSON.stringify(incidentRowsFixture()));
    await conn.query("CREATE TABLE incidents AS SELECT * FROM read_json_auto('incidents.json')");
    source = "fixture";
  } else {
    throw new MissingDataset("The incident dataset has not been published yet.");
  }
  const described = await conn.query("DESCRIBE incidents");
  const columns = described.toArray().map((r) => {
    const o = r.toJSON() as { column_name: string; column_type: string };
    return { name: o.column_name, type: o.column_type };
  });
  return { db, conn, source, columns };
}

interface Result {
  columns: string[];
  numeric: boolean[];
  rows: string[][];
  ms: number;
}

function cell(value: unknown, type: string): string {
  if (value == null) return "";
  if (typeof value === "bigint") return value.toString();
  if (/timestamp|date/i.test(type) && typeof value === "number") return new Date(value).toISOString().replace(".000Z", "Z");
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(Math.round(value * 1000) / 1000);
  if (typeof value === "object" && value && Symbol.iterator in value) return Array.from(value as Iterable<unknown>).map((v) => cell(v, "")).join(", ");
  if (value instanceof Date) return value.toISOString();
  return String(value);
}

function toCsv(r: Result): string {
  const q = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  return [r.columns.map(q).join(","), ...r.rows.map((row) => row.map(q).join(","))].join("\n");
}

export function ExploreConsole() {
  const meta = useDoc<Meta>("meta.json");
  const ds = meta.data?.models?.dataset ?? null;
  const [sql, setSql] = useState(EXAMPLES[0].sql);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [info, setInfo] = useState<Pick<Engine, "source" | "columns"> | null>(null);
  const editorId = useId();
  const last = useRef<Result | null>(null);

  const run = useCallback(async (text: string) => {
    setBusy(true);
    setError(null);
    try {
      if (!engine) {
        engine = startEngine((s) => setStatus(s));
        engine.catch(() => {
          engine = null;
        });
      }
      const e = await engine;
      setInfo({ source: e.source, columns: e.columns });
      setStatus("Running");
      const t0 = performance.now();
      const table = await e.conn.query(text);
      const ms = performance.now() - t0;
      const fields = table.schema.fields.map((f) => ({ name: f.name, type: String(f.type) }));
      const rows = table.toArray().map((r) => {
        const o = r.toJSON() as Record<string, unknown>;
        return fields.map((f) => cell(o[f.name], f.type));
      });
      const numeric = fields.map((f) => /^(Int|Uint|Float|Decimal)/i.test(f.type));
      const res = { columns: fields.map((f) => f.name), numeric, rows, ms };
      last.current = res;
      setResult(res);
      setStatus(null);
    } catch (err) {
      if (err instanceof MissingDataset) {
        setMissing(true);
        setStatus(null);
      } else {
        setError(err instanceof Error ? err.message : String(err));
        setStatus(null);
      }
    } finally {
      setBusy(false);
    }
  }, []);

  const download = () => {
    const r = last.current;
    if (!r) return;
    const blob = new Blob([toCsv(r)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "mercalli-query.csv";
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      void run(sql);
    }
  };

  return (
    <>
      <PageHeader
        title="Explore"
        lead={
          <p>
            Ask the incident history anything, in SQL. Queries run in your browser with DuckDB: the engine and the dataset are downloaded
            from this site when you run the first one, and nothing you type is sent anywhere.
          </p>
        }
        meta={
          <>
            One table, incidents, with a row per incident: its service, category, impact, start, end, length in minutes, updates and the
            providers its reports name.
            {ds ? (
              <>
                {" "}
                {formatCount(ds.incidents)} incidents from {formatCount(ds.services)} services, <Time iso={ds.first} style="date" /> to{" "}
                <Time iso={ds.last} style="date" />.
              </>
            ) : null}
          </>
        }
      />
      <div className="pb-20">
        <div role="group" aria-label="Example queries" className="flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex.label}
              type="button"
              className="chip"
              aria-pressed={sql === ex.sql}
              onClick={() => {
                setSql(ex.sql);
                void run(ex.sql);
              }}
            >
              {ex.label}
            </button>
          ))}
        </div>

        <label htmlFor={editorId} className="mt-6 block text-xs text-ink-soft">
          Query
        </label>
        <textarea
          id={editorId}
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          onKeyDown={onKey}
          spellCheck={false}
          wrap="off"
          rows={Math.min(14, Math.max(6, sql.split("\n").length + 1))}
          className="field mt-1.5 block w-full resize-y font-mono text-[0.8rem] leading-relaxed"
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="button" className="button bg-ink text-paper hover:bg-ink" style={{ borderColor: "var(--ink)" }} onClick={() => void run(sql)} disabled={busy}>
            {busy ? "Running" : "Run query"}
          </button>
          <span className="text-xs text-ink-soft">or press Ctrl or Cmd with Enter</span>
          {result ? (
            <button type="button" className="button ml-auto" onClick={download}>
              Download CSV
            </button>
          ) : null}
        </div>

        <div className="mt-4 min-h-6 text-xs" aria-live="polite">
          {status ? <p className="text-ink-soft">{status}</p> : null}
          {error ? (
            <p className="ledger px-3 py-2 font-mono text-[0.75rem] whitespace-pre-wrap text-ink" role="alert">
              {error}
            </p>
          ) : null}
          {result && !status && !error ? (
            <p className="text-ink-soft">
              {plural(result.rows.length, "row")} in {Math.max(1, Math.round(result.ms))} ms
              {result.rows.length > SHOW_ROWS ? `, showing the first ${formatCount(SHOW_ROWS)}` : ""}.
              {process.env.NODE_ENV === "development" && info?.source === "fixture" ? " Development fixture data." : ""}
            </p>
          ) : null}
        </div>

        {missing ? (
          <div className="ledger mt-4 px-5 py-5">
            <p className="font-medium">The incident dataset has not been published yet.</p>
            <p className="prose-measure mt-2 text-ink-soft">
              It will be published as data/incidents.parquet next to this site. Once it is, the examples above run against it.
            </p>
          </div>
        ) : null}

        {result ? (
          <div className="mt-4 max-h-[60vh] overflow-auto rounded-sm border border-rule bg-paper-raised">
            <table className="table">
              <thead className="sticky top-0 bg-paper-raised">
                <tr>
                  {result.columns.map((c, j) => (
                    <th key={c} scope="col" className={`px-3 ${result.numeric[j] ? "text-right" : ""}`}>
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.slice(0, SHOW_ROWS).map((row, i) => (
                  <tr key={i}>
                    {row.map((v, j) => (
                      <td key={j} className={`px-3 whitespace-nowrap ${result.numeric[j] ? "text-right" : ""}`}>
                        {v}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {info ? (
          <details className="mt-6 text-xs">
            <summary className="inline-flex min-h-8 cursor-pointer items-center text-ink-soft hover:text-ink">Columns of incidents</summary>
            <ul className="mt-2 flex flex-wrap gap-2">
              {info.columns.map((c) => (
                <li key={c.name} className="rounded-xs border border-rule px-2 py-1 font-mono text-[0.7rem]">
                  {c.name} <span className="text-ink-soft">{c.type.toLowerCase()}</span>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </>
  );
}

function plural(n: number, one: string): string {
  return `${formatCount(n)} ${n === 1 ? one : `${one}s`}`;
}
