"use client";

import Link from "next/link";
import { ReliabilityChart } from "@/components/charts/reliability-chart";
import { FixtureNote, Pending, Section } from "@/components/ui/section";
import type { ModelCard } from "@/lib/api/types";
import { useArtifact } from "@/lib/api/use-artifact";
import { formatCount, formatDuration, formatShare } from "@/lib/format";

export function ModelTeaser() {
  const doc = useArtifact<ModelCard>("model.json");
  const m = doc.data;
  return (
    <Section
      id="how-long"
      compact={!m}
      title="How long will it last?"
      lead={
        <p>
          Each open incident gets a forecast of the time left until the vendor marks it resolved: a median and an 80%
          range, refreshed with every vendor update.
        </p>
      }
      aside={<Link href="/model/">Read the model card</Link>}
    >
      {doc.status === "loading" ? (
        <div className="h-56" aria-busy="true" />
      ) : !m ? (
        <Pending title={doc.status === "missing" ? "The model card has not been published yet." : "The model card could not be loaded."}>
          Accuracy on held-out incidents will be reported here once the first recovery model is trained and its card is
          published as model.json.
        </Pending>
      ) : (
        <div>
          <FixtureNote show={doc.fixture} />
          <div className="grid grid-cols-1 items-start gap-x-10 gap-y-8 md:grid-cols-[minmax(0,1fr)_auto]">
            <dl className="grid grid-cols-1 gap-y-6 sm:grid-cols-3 sm:gap-x-6">
              <div>
                <dt className="text-xs text-ink-soft">Median error</dt>
                <dd className="display mt-1 text-3xl font-light">{formatDuration(m.recovery.test.all_updates.median_abs_error_min)}</dd>
                <dd className="mt-1 text-xs text-ink-soft">Half of all forecasts land within this of the real end.</dd>
              </div>
              <div>
                <dt className="text-xs text-ink-soft">80% range coverage</dt>
                <dd className="display mt-1 text-3xl font-light">{formatShare(m.recovery.test.all_updates.coverage_80)}</dd>
                <dd className="mt-1 text-xs text-ink-soft">Share of real ends inside the 80% range. Calibrated means close to 80%.</dd>
              </div>
              <div>
                <dt className="text-xs text-ink-soft">Within a factor of two</dt>
                <dd className="display mt-1 text-3xl font-light">{formatShare(m.recovery.test.all_updates.within_2x)}</dd>
                <dd className="mt-1 text-xs text-ink-soft">
                  Forecasts between half and double the real time left, on {formatCount(m.recovery.test.all_updates.n)} held-out
                  updates.
                </dd>
              </div>
            </dl>
            <ReliabilityChart reliability={m.recovery.reliability} size={200} />
          </div>
        </div>
      )}
    </Section>
  );
}
