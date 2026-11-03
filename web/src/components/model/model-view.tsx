"use client";

import { useState } from "react";
import { ReliabilityChart } from "@/components/charts/reliability-chart";
import { ShareBars } from "@/components/charts/share-bars";
import { IntensityScale } from "@/components/now/intensity-readout";
import { useOverview } from "@/components/overview-context";
import { PageHeader } from "@/components/ui/page-header";
import { FixtureNote, Pending, Section } from "@/components/ui/section";
import { Ago, Time } from "@/components/ui/time";
import type { BaselineMetrics, BlastEval, ModelCard, RecoveryMetrics } from "@/lib/api/types";
import { useArtifact } from "@/lib/api/use-artifact";
import { formatCount, formatDecimal, formatDuration, formatShare, humanize } from "@/lib/format";
import { intensityMeaning, NUMERALS } from "@/lib/intensity";
import { categoryLabel } from "@/lib/severity";

const BASELINE_LABEL: Record<string, string> = {
  phase_median: "Median for the phase",
  elapsed_and_phase: "Median for the phase and time elapsed",
  service_history: "The service's own history",
  base_rate_only: "The base rate alone",
  co_failure_lift: "How often the two failed together",
  named_in_reports: "How often the service named the provider before",
  census_dependency: "Census dependency",
  named_together_before: "Named together in past incidents",
};

const pct1 = (v: number) => formatShare(v, 1);

const FEATURE_LABEL: Record<string, string> = {
  prior_minus_elapsed: "Typical length for the service, minus time elapsed",
  elapsed_log: "Time elapsed",
  title: "Wording of the title",
  body: "Wording of the first update",
  latest: "Wording of the latest update",
  nn_log_median: "Typical length of similar past incidents",
  nn_long_share: "How often similar past incidents ran long",
  nn_similarity: "How close the most similar past incidents are",
  nn_same_service: "Similar past incidents at the same service",
  nn_minus_elapsed: "Length of similar past incidents, minus time elapsed",
  eta_log: "Time to resolution the vendor gives",
  next_update_log: "Time to the next update the vendor promises",
  says_fix: "Update mentions a fix, rollback or mitigation",
  says_upstream: "Update blames a third party or upstream provider",
  phase: "Phase (investigating, identified, monitoring)",
  since_update_log: "Time since the last update",
  prior_log_median: "Typical length for the service",
  n_updates: "Number of updates so far",
  category: "Category of the service",
  reached_monitoring: "Has reached monitoring",
  reached_identified: "Has reached identified",
  severity: "Severity of affected components",
  gap_log: "Gap between the last two updates",
  n_components: "Components affected",
  prior_long_share: "How often the service runs long",
  prior_log_n: "Incidents in the service's history",
  hour_sin: "Hour of day",
  hour_cos: "Hour of day",
  weekend: "Weekend",
};

export function ModelView() {
  const doc = useArtifact<ModelCard>("model.json");
  const m = doc.data;
  return (
    <>
      <PageHeader
        title="Model card"
        lead={
          <p>
            Two models sit behind the forecasts: one predicts how long an open incident will last, the other which services are likely
            to follow an incident at a provider they depend on. This card says how they were trained and how accurate they are.
          </p>
        }
        meta={
          m ? (
            <>
              Recovery model {m.recovery.production?.version ?? m.summary.recovery_version}, blast radius model {m.summary.blast_version}.
              Card generated <Ago iso={m.generated_at} />.
            </>
          ) : null
        }
      />
      <Section id="predicts" title="What is predicted">
        <div className="prose-measure space-y-3">
          <p>
            For each open incident, the time left until the vendor marks it resolved, as three percentiles: the 10th, the 50th (the
            median) and the 90th. The range from the 10th to the 90th is an 80% interval. Each forecast uses only what was known when it
            was made: the incident&apos;s phase, its updates so far, its wording, the service&apos;s own history and the time of day.
          </p>
          <p className="text-ink-soft">
            The intervals are widened by conformal calibration on a later period the model never trained on, so that they hold their
            stated coverage on new incidents.
          </p>
        </div>
      </Section>
      {doc.status === "loading" ? (
        <div className="h-96" aria-busy="true" />
      ) : !m ? (
        <Section id="card" title="Accuracy">
          <Pending title={doc.status === "missing" ? "The model card has not been published yet." : "The model card could not be loaded."}>
            Accuracy, calibration and feature importance will be reported here once the first models are trained and the card is
            published as model.json.
          </Pending>
        </Section>
      ) : (
        <CardBody m={m} fixture={doc.fixture} />
      )}
      <IntensitySection model={m} />
      <Section id="limitations" title="Limitations">
        <ul className="prose-measure list-disc space-y-2 pl-5 text-ink-soft marker:text-ink-faint">
          <li>The forecast is of when the vendor marks the incident resolved, which can lag the real recovery.</li>
          <li>Vendors sometimes post late and backdate the start, or post only after an incident is over. Those incidents cannot be forecast while open.</li>
          <li>Some incidents disappear from a page without a resolution time. Their end is when Mercalli noticed, and is marked as inferred.</li>
          <li>Services with little history borrow from their category, so their forecasts are less specific.</li>
          <li>The blast radius model sees only public signals: the census, past incident reports and past co-failures. A private dependency it cannot see will not show up.</li>
        </ul>
      </Section>
    </>
  );
}

function MetricsTable({ model, baselines, mode }: { model: RecoveryMetrics; baselines: Record<string, { all_updates: BaselineMetrics; first_update: BaselineMetrics }>; mode: "all_updates" | "first_update" }) {
  const rows: { name: string; m: BaselineMetrics & Partial<RecoveryMetrics>; model: boolean }[] = [
    { name: "Recovery model", m: model, model: true },
    ...Object.entries(baselines).map(([k, v]) => ({ name: BASELINE_LABEL[k] ?? humanize(k), m: v[mode], model: false })),
  ];
  return (
    <div className="overflow-x-auto">
      <table className="table min-w-[640px]">
        <caption className="sr-only">Test metrics for the model and simple baselines</caption>
        <thead>
          <tr>
            <th scope="col">Forecaster</th>
            <th scope="col" className="text-right">
              Median error
            </th>
            <th scope="col" className="text-right">
              Mean log error
            </th>
            <th scope="col" className="text-right">
              Within 2x
            </th>
            <th scope="col" className="text-right">
              80% coverage
            </th>
            <th scope="col" className="text-right">
              Forecasts
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <td className={r.model ? "font-medium text-ink" : "text-ink-soft"}>{r.name}</td>
              <td className="text-right">{formatDuration(r.m.median_abs_error_min)}</td>
              <td className="text-right">{formatDecimal(r.m.mae_log, 3)}</td>
              <td className="text-right">{formatShare(r.m.within_2x)}</td>
              <td className="text-right">{r.m.coverage_80 != null ? pct1(r.m.coverage_80) : <span className="text-ink-soft">no interval</span>}</td>
              <td className="text-right text-ink-soft">{formatCount(r.m.n)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function bestBaseline(baselines: ModelCard["recovery"]["baselines"], mode: "all_updates" | "first_update") {
  return Object.entries(baselines)
    .map(([key, v]) => ({ key, m: v[mode] }))
    .sort((a, b) => a.m.mae_log - b.m.mae_log)[0];
}

function overlaps(a?: [number, number], b?: [number, number]): boolean | null {
  if (!a || !b) return null;
  return a[0] <= b[1] && b[0] <= a[1];
}

function Interval({ ci }: { ci?: [number, number] }) {
  if (!ci) return null;
  return (
    <span className="block text-[0.72rem] text-ink-soft">
      {pct1(ci[0])} to {pct1(ci[1])}
    </span>
  );
}

function CardBody({ m, fixture }: { m: ModelCard; fixture: boolean }) {
  const [mode, setMode] = useState<"all_updates" | "first_update">("all_updates");
  const r = m.recovery;
  const test = r.test[mode];
  const best = bestBaseline(r.baselines, mode);
  const gain = best ? (best.m.mae_log - test.mae_log) / best.m.mae_log : 0;
  const ds = m.summary.dataset;
  const features = Object.entries(r.feature_importance)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12)
    .map(([k, v]) => ({ id: k, name: FEATURE_LABEL[k] ?? humanize(k), share: v }));
  const topFeature = Math.max(...features.map((f) => f.share), 0.0001);
  return (
    <>
      <Section
        id="data"
        title="Data and time split"
        lead={
          <p>
            Trained on incidents that began before <Time iso={r.split.calib_start} style="date" />, calibrated on those from then until{" "}
            <Time iso={r.split.test_start} style="date" />, and tested only on incidents after that.
          </p>
        }
      >
        <FixtureNote show={fixture} />
        {ds ? (
          <p className="prose-measure mb-5 text-ink-soft">
            The dataset holds {formatCount(ds.incidents)} incidents and {formatCount(ds.updates)} vendor updates from{" "}
            {formatCount(ds.services)} services, from <Time iso={ds.first} style="date" /> to <Time iso={ds.last} style="date" />.
          </p>
        ) : null}
        <div className="overflow-x-auto">
          <table className="table max-w-xl">
            <caption className="sr-only">Incidents and forecast snapshots in each period</caption>
            <thead>
              <tr>
                <th scope="col">Period</th>
                <th scope="col" className="text-right">
                  Incidents
                </th>
                <th scope="col" className="text-right">
                  Snapshots
                </th>
              </tr>
            </thead>
            <tbody>
              {(["train", "calib", "test"] as const).map((k) => (
                <tr key={k}>
                  <td>{k === "train" ? "Training" : k === "calib" ? "Calibration" : "Test"}</td>
                  <td className="text-right">{formatCount(r.counts.incidents[k])}</td>
                  <td className="text-right">{formatCount(r.counts.snapshots[k])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-ink-soft">A snapshot is one vendor update: a moment when a forecast would have been made.</p>
      </Section>

      <Section
        id="accuracy"
        title="Accuracy against simple rules"
        lead={
          <p>
            Each simple rule gets the same information as the model. Mean log error is the typical miss on a log scale, so a miss by a
            factor counts the same for short and long incidents; within 2x is the share of forecasts between half and double the real time
            left.
          </p>
        }
      >
        <div role="radiogroup" aria-label="Which forecasts" className="mb-5 flex flex-wrap gap-2">
          <button type="button" role="radio" aria-checked={mode === "all_updates"} className="chip" onClick={() => setMode("all_updates")}>
            Every vendor update
          </button>
          <button type="button" role="radio" aria-checked={mode === "first_update"} className="chip" onClick={() => setMode("first_update")}>
            First update only
          </button>
        </div>
        {best ? (
          <p className="prose-measure mb-5">
            On {formatCount(test.n)} held-out forecasts, the model&apos;s mean log error is {formatDecimal(test.mae_log, 3)}, against{" "}
            {formatDecimal(best.m.mae_log, 3)} for the best simple rule, {(BASELINE_LABEL[best.key] ?? humanize(best.key)).toLowerCase()}.
            {gain > 0
              ? ` That is ${formatShare(gain, gain < 0.1 ? 1 : 0)} lower, a ${gain < 0.2 ? "modest" : "clear"} gain.`
              : " The simple rule does as well or better."}{" "}
            Its 80% ranges contained {pct1(test.coverage_80)} of the real ends.
          </p>
        ) : null}
        <MetricsTable model={test} baselines={r.baselines} mode={mode} />
      </Section>

      <Section
        id="calibration"
        title="Calibration"
        lead={
          <p>
            {pct1(test.coverage_80)} of real ends fell inside the 80% range.
            {r.uncalibrated_coverage_80 != null
              ? ` Before calibration the raw ranges held ${pct1(r.uncalibrated_coverage_80)}; widening them on the later calibration period brought them to the stated coverage.`
              : ""}{" "}
            In a typical forecast, the high end of the range is {formatDecimal(test.median_width_ratio, 1)} times the low end.
          </p>
        }
      >
        <div className="grid grid-cols-1 items-start gap-8 md:grid-cols-[auto_minmax(0,1fr)]">
          <ReliabilityChart reliability={r.reliability} size={260} />
          <div className="prose-measure space-y-3 text-ink-soft">
            <p>
              Each dot is one quantile of the raw models, before the range is widened. Its height is the share of real outcomes that fell
              at or below it. A calibrated model puts every dot on the diagonal; dots below it mean forecasts that ran short.
            </p>
            <table className="table max-w-xs">
              <thead>
                <tr>
                  <th scope="col">Quantile</th>
                  <th scope="col" className="text-right">
                    Outcomes at or below
                  </th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(r.reliability).map(([q, v]) => (
                  <tr key={q}>
                    <td>{formatShare(Number(q))}</td>
                    <td className="text-right">{pct1(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <Breakdown title="By phase" rows={r.by_phase} label={(k) => humanize(k)} />
        <Breakdown title="By category" rows={r.by_category} label={categoryLabel} />
      </Section>

      <Section id="features" title="What the model relies on" lead={<p>How much the median forecast&apos;s error grows when one input is shuffled. Longer bars matter more.</p>}>
        <ShareBars label="Feature importance" rows={features} max={topFeature} format={(f) => formatDecimal(f.share, 3)} />
      </Section>

      <BlastSection blast={m.blast} />

      <Section id="live" title="Live accuracy" lead={<p>The same measures on forecasts made in production, once their incidents have ended.</p>}>
        {m.live ? (
          <dl className="grid grid-cols-1 gap-6 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-ink-soft">Resolved incidents scored</dt>
              <dd className="mt-1 text-2xl font-medium">{formatCount(m.live.n)}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-soft">80% coverage</dt>
              <dd className="mt-1 text-2xl font-medium">{pct1(m.live.coverage_80)}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-soft">Mean log error</dt>
              <dd className="mt-1 text-2xl font-medium">{formatDecimal(m.live.mae_log, 3)}</dd>
            </div>
          </dl>
        ) : (
          <p className="text-ink-soft">Not enough live forecasts have resolved yet to report.</p>
        )}
      </Section>
    </>
  );
}

function BlastSection({ blast }: { blast: ModelCard["blast"] }) {
  const t = blast.test;
  const rows: { key: string; name: string; v: BlastEval; model: boolean }[] = [
    { key: "model", name: "Blast radius model", v: t, model: true },
    ...(blast.test_major ? [{ key: "model_major", name: "Blast radius model, major and critical upstream incidents only", v: blast.test_major, model: true }] : []),
    ...Object.entries(blast.baselines).map(([k, v]) => ({ key: k, name: BASELINE_LABEL[k] ?? humanize(k), v, model: false })),
  ];
  const best = Object.entries(blast.baselines).sort((a, b) => b[1].recall_at_10 - a[1].recall_at_10)[0];
  const overlap = best ? overlaps(t.ci95?.recall_at_10, best[1].ci95?.recall_at_10) : null;
  const w = blast.window_minutes;
  return (
    <Section
      id="blast-model"
      title="Blast radius model"
      lead={
        <p>
          For an incident at a provider others depend on, a ranking of the services likely to follow. A hit means{" "}
          {blast.label ?? "the other service posts an incident of its own"}
          {w ? `, from ${w.before} minutes before to ${w.after} minutes after the provider's incident began` : ""}.
        </p>
      }
    >
      <p className="prose-measure mb-5">
        Tested on {formatCount(t.upstream_incidents_with_positive)} held-out upstream incidents with at least one follower, across{" "}
        {formatCount(t.pairs)} provider and service pairs, of which {formatCount(t.positives)} were hits.
        {best
          ? ` The model's top ten find ${pct1(t.recall_at_10)} of the services that followed, against ${pct1(best[1].recall_at_10)} for ${(BASELINE_LABEL[best[0]] ?? humanize(best[0])).toLowerCase()}${
              overlap == null ? "." : overlap ? "; the 95% intervals overlap, so that gain is not settled." : ", a gain outside both 95% intervals."
            }`
          : ""}
      </p>
      <div className="overflow-x-auto">
        <table className="table min-w-[680px]">
          <caption className="sr-only">Blast radius ranking on held-out incidents, with 95% intervals</caption>
          <thead>
            <tr>
              <th scope="col">Ranker</th>
              <th scope="col" className="text-right">
                Average precision
              </th>
              <th scope="col" className="text-right">
                Precision in top 10
              </th>
              <th scope="col" className="text-right">
                Recall in top 10
              </th>
              <th scope="col" className="text-right">
                At least one hit in top 10
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td className={row.model ? "font-medium text-ink" : "text-ink-soft"}>{row.name}</td>
                <td className="text-right">{formatDecimal(row.v.average_precision, row.v.average_precision < 0.001 ? 5 : 3)}</td>
                <td className="text-right">
                  {pct1(row.v.precision_at_10)}
                  <Interval ci={row.v.ci95?.precision_at_10} />
                </td>
                <td className="text-right">
                  {pct1(row.v.recall_at_10)}
                  <Interval ci={row.v.ci95?.recall_at_10} />
                </td>
                <td className="text-right">
                  {pct1(row.v.hit_at_10)}
                  <Interval ci={row.v.ci95?.hit_at_10} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-ink-soft">The smaller figures under each share are its 95% interval.</p>
    </Section>
  );
}

function Breakdown({ title, rows, label }: { title: string; rows: Record<string, RecoveryMetrics>; label: (k: string) => string }) {
  const entries = Object.entries(rows).sort((a, b) => b[1].n - a[1].n);
  if (!entries.length) return null;
  return (
    <div className="mt-10">
      <h3 className="mb-2 text-base font-medium">{title}</h3>
      <div className="overflow-x-auto">
        <table className="table min-w-[520px]">
          <thead>
            <tr>
              <th scope="col">{title.replace("By ", "").replace(/^./, (c) => c.toUpperCase())}</th>
              <th scope="col" className="text-right">
                Median error
              </th>
              <th scope="col" className="text-right">
                Within 2x
              </th>
              <th scope="col" className="text-right">
                80% coverage
              </th>
              <th scope="col" className="text-right">
                Forecasts
              </th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([k, v]) => (
              <tr key={k}>
                <td>{label(k)}</td>
                <td className="text-right">{formatDuration(v.median_abs_error_min)}</td>
                <td className="text-right">{formatShare(v.within_2x)}</td>
                <td className="text-right">{pct1(v.coverage_80)}</td>
                <td className="text-right text-ink-soft">{formatCount(v.n)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function IntensitySection({ model }: { model: ModelCard | null }) {
  const { data } = useOverview();
  const i = data?.intensity ?? null;
  const t = model?.intensity;
  return (
    <Section
      id="intensity"
      title="How intensity is measured"
      lead={
        <p>
          A single reading of how disrupted things are right now, on a ten step scale named after the Mercalli scale for how strongly an
          earthquake is felt.
        </p>
      }
    >
      <div className="prose-measure space-y-3">
        <p>
          The score adds up the open incidents that are minor or worse and less than a week old, weighted by impact, with extra weight for
          incidents at providers that {t?.hub_dependents != null ? `at least ${t.hub_dependents}` : "many"} other services depend on.
          Advisories, maintenance and incidents a vendor has left open for over a week do not count.
        </p>
        <p className="text-ink-soft">
          The level says how unusual the score is: each threshold is the score that a given share of past hours stayed below, computed
          from a year of history. Until that history is in place the thresholds are provisional, and the reading says so.
        </p>
      </div>
      {i ? (
        <div className="mt-8 max-w-xl">
          <p className="flex flex-wrap items-baseline gap-x-3">
            <span className="text-ink-soft">Right now</span>
            <span className="display text-3xl font-light">{i.numeral}</span>
            <span className="text-lg font-medium">{i.label}</span>
            <span className="text-ink-soft">score {Number.isInteger(i.score) ? i.score : formatDecimal(i.score, 1)}</span>
          </p>
          <IntensityScale level={i.level} />
          <p className="mt-3 text-ink-soft">{intensityMeaning(i)}</p>
        </div>
      ) : null}
      {t && t.thresholds.length ? (
        <div className="mt-8 overflow-x-auto">
          <table className="table max-w-xl">
            <caption className="mb-2 text-left text-xs text-ink-soft">Levels and the score each one starts at</caption>
            <thead>
              <tr>
                <th scope="col">Level</th>
                <th scope="col" className="text-right">
                  Score from
                </th>
                <th scope="col" className="text-right">
                  Busier than this share of hours
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{NUMERALS[0]}</td>
                <td className="text-right">0</td>
                <td className="text-right text-ink-soft">none</td>
              </tr>
              {t.thresholds.map((th, idx) => (
                <tr key={th}>
                  <td>{NUMERALS[idx + 1]}</td>
                  <td className="text-right">{th}</td>
                  <td className="text-right">{t.percentiles[idx] != null ? `${t.percentiles[idx]}%` : "unknown"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {Object.keys(t.weights).length ? (
            <p className="mt-3 text-xs text-ink-soft">
              Weights: {Object.entries(t.weights).map(([k, v]) => `${humanize(k).toLowerCase()} ${v}`).join(", ")}.
              {t.hours ? ` Thresholds come from ${formatCount(t.hours)} hours of history` : ""}
              {t.hours && t.median_score != null ? `; the median hour scored ${t.median_score}` : ""}
              {t.hours && t.max_score != null ? ` and the busiest ${t.max_score}` : ""}
              {t.hours ? "." : ""}
            </p>
          ) : null}
        </div>
      ) : (
        <p className="mt-6 text-xs text-ink-soft">The thresholds will be listed here once the model card is published.</p>
      )}
    </Section>
  );
}
