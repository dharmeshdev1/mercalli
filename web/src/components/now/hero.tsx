"use client";

import { useOverview } from "@/components/overview-context";
import { Helicorder } from "@/components/helicorder/helicorder";
import { formatCount } from "@/lib/format";
import { IntensityReadout } from "./intensity-readout";

export function Hero() {
  const ov = useOverview();
  const data = ov.data;
  const n = data?.counts.services;
  return (
    <section aria-labelledby="hero-title" className="pt-6 pb-16 sm:pt-8 md:pt-9 md:pb-20">
      <div className="grid grid-cols-1 items-end gap-x-6 gap-y-6 sm:gap-y-8 lg:grid-cols-12">
        <div className="lg:col-span-7">
          <h1 id="hero-title" className="display text-4xl font-light sm:text-5xl">
            Mercalli
          </h1>
          <p className="mt-3 max-w-[34ch] text-lg text-ink-soft md:text-xl">
            Live outage intelligence for{" "}
            <span className="inline-block min-w-[3ch] font-medium text-ink">{n != null ? formatCount(n) : " "}</span>{" "}
            services, from their own status pages.
          </p>
        </div>
        <div className="lg:col-span-5 lg:pb-1">
          <IntensityReadout intensity={data?.intensity} />
        </div>
      </div>
      <div className="mt-6 sm:mt-8">
        {ov.status === "error" && !data ? (
          <p className="mb-3 text-ink" role="alert">
            The live record could not be loaded. The API did not answer; this page will try again in a minute.
          </p>
        ) : null}
        <Helicorder overview={data} />
      </div>
    </section>
  );
}
