"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { FixtureNote, Pending } from "@/components/ui/section";
import type { Replay, ReplaysDoc } from "@/lib/api/types";
import { useArtifact } from "@/lib/api/use-artifact";
import { formatClock, formatDate, formatDuration, parseTime, plural, possessive, MINUTE } from "@/lib/format";
import { clockStart, narrate, onRecord, sectors, stepAt, type NarrationStep } from "@/lib/replay";
import { useReducedMotionPref, useZone } from "@/lib/prefs";
import { categoryLabel } from "@/lib/severity";
import { useElementWidth } from "@/lib/use-width";
import { ReplayMap, type ReplayMapHandle } from "./replay-map";

/** Scrolling distance of the pinned replay, in viewport heights. */
const SCROLL_SCREENS = 2.6;

export function ReplaySection() {
  const doc = useArtifact<ReplaysDoc>("replays.json");
  const replay = doc.data?.replays?.[0] ?? null;

  if (doc.status === "loading") return <section className="section-rule min-h-48" aria-busy="true" />;
  if (!replay) {
    return (
      <section className="section-rule pt-10 pb-12 md:pt-12 md:pb-14" aria-labelledby="replay-title">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          <div className="lg:col-span-4">
            <h2 id="replay-title" className="text-xl font-semibold md:text-2xl">
              Replay
            </h2>
            <p className="mt-3 text-ink-soft">How one provider&apos;s incident spread to the services that depend on it, minute by minute.</p>
          </div>
          <div className="lg:col-span-8">
            <Pending title={doc.status === "missing" || doc.data ? "No cascade has been reconstructed yet." : "The replays could not be loaded."}>
              Replays are rebuilt from status page history and published as replays.json. The first one will appear here.
            </Pending>
          </div>
        </div>
      </section>
    );
  }
  return <ReplayStage replay={replay} fixture={doc.fixture} />;
}

function ReplayStage({ replay, fixture }: { replay: Replay; fixture: boolean }) {
  const [zone] = useZone();
  const reduced = useReducedMotionPref();
  const sectionRef = useRef<HTMLElement>(null);
  const pinRef = useRef<HTMLDivElement>(null);
  const mapWrapRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<ReplayMapHandle>(null);
  const clockRef = useRef<HTMLSpanElement>(null);
  const offsetRef = useRef<HTMLSpanElement>(null);
  const countRef = useRef<HTMLSpanElement>(null);
  const barRef = useRef<HTMLSpanElement>(null);
  const stepRefs = useRef<(HTMLLIElement | null)[]>([]);
  const c0 = clockStart(replay);
  const minuteRef = useRef(c0);
  const [mobileStep, setMobileStep] = useState(0);
  const mapWidth = useElementWidth(mapWrapRef);
  const compact = mapWidth > 0 && mapWidth < 520;
  const scale = mapWidth ? Math.max(1, ((compact ? 264 : 346) * 2) / mapWidth) : 1;
  const sectorList = useMemo(() => sectors(replay.events), [replay.events]);

  const startMs = parseTime(replay.upstream.started_at) ?? 0;
  const steps = useMemo<NarrationStep[]>(() => narrate(replay, (ms) => formatClock(ms, zone)), [replay, zone]);
  const offsets = useMemo(() => replay.events.map((e) => e.offset_min).sort((a, b) => a - b), [replay.events]);

  // Paint a minute everywhere: map, clock, counter, progress and the narration.
  const paint = useMemo(() => {
    return (minute: number) => {
      minuteRef.current = minute;
      mapRef.current?.setClock(minute);
      if (clockRef.current) clockRef.current.textContent = formatClock(startMs + minute * MINUTE, zone);
      if (offsetRef.current) {
        offsetRef.current.textContent =
          Math.abs(minute) < 1 ? "start" : `${minute < 0 ? "-" : "+"}${formatDuration(Math.abs(minute))}`;
      }
      if (countRef.current) {
        let n = 0;
        while (n < offsets.length && offsets[n] <= minute) n++;
        countRef.current.textContent = n === 1 ? "1 service has posted an incident" : `${n} services have posted incidents`;
      }
      if (barRef.current) barRef.current.style.transform = `scaleX(${Math.min(1, Math.max(0, (minute - c0) / (replay.window_min - c0)))})`;
      const current = stepAt(steps, minute);
      stepRefs.current.forEach((el, i) => {
        if (!el) return;
        el.dataset.state = i < current ? "past" : i === current ? "current" : "future";
      });
      setMobileStep((s) => (s === current ? s : current));
    };
  }, [startMs, zone, offsets, replay.window_min, steps, c0]);

  // Repaint after any render (theme, zone or size changes) at the current clock.
  useEffect(() => {
    paint(reduced ? replay.window_min : minuteRef.current);
  });

  useEffect(() => {
    if (reduced) return;
    const section = sectionRef.current;
    const pin = pinRef.current;
    if (!section || !pin) return;
    let revert: (() => void) | null = null;
    let disposed = false;
    let ro: ResizeObserver | null = null;

    const setup = async () => {
      const [{ gsap }, { ScrollTrigger }] = await Promise.all([import("gsap"), import("gsap/ScrollTrigger")]);
      if (disposed) return;
      gsap.registerPlugin(ScrollTrigger);
      const proxy = { minute: minuteRef.current };
      const from = clockStart(replay);
      const ctx = gsap.context(() => {
        gsap
          .timeline({
            scrollTrigger: {
              trigger: pin,
              start: "top top",
              end: () => `+=${Math.round(window.innerHeight * SCROLL_SCREENS)}`,
              pin: true,
              scrub: 0.5,
              invalidateOnRefresh: true,
            },
          })
          .fromTo(proxy, { minute: from }, { minute: replay.window_min, ease: "none", onUpdate: () => paint(proxy.minute) });
      }, section);
      // Content above the replay loads late; keep the trigger positions honest.
      let pending = 0;
      ro = new ResizeObserver(() => {
        window.clearTimeout(pending);
        pending = window.setTimeout(() => ScrollTrigger.refresh(), 150);
      });
      ro.observe(document.body);
      revert = () => {
        window.clearTimeout(pending);
        ctx.revert();
      };
    };

    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          io.disconnect();
          void setup();
        }
      },
      { rootMargin: "900px 0px" },
    );
    io.observe(section);
    return () => {
      disposed = true;
      io.disconnect();
      ro?.disconnect();
      revert?.();
    };
  }, [reduced, paint, replay]);

  const up = replay.upstream;
  const recorded = onRecord(replay);
  const cats = Object.keys(replay.totals.categories).length;
  const epicenter = `${up.service_name}, ${formatDate(startMs, zone)}`;

  return (
    <section ref={sectionRef} id="replay" aria-labelledby="replay-title" className="section-rule">
      <div ref={pinRef} className={`flex flex-col bg-paper ${reduced ? "pt-10 pb-20 md:pt-12 md:pb-24" : "min-h-[100svh] justify-center py-6 md:py-8"}`}>
        <div className="grid grid-cols-1 gap-x-6 gap-y-4 lg:grid-cols-12">
          <div className="lg:col-span-7">
            <FixtureNote show={fixture} />
            <h2 id="replay-title" className="text-xl font-semibold md:text-2xl">
              Replay: {replay.title}
            </h2>
            <p className="prose-measure mt-1 text-ink-soft">
              {recorded
                ? `${formatDate(startMs, zone)}. `
                : `${up.title.replace(/\.$/, "")}. Mercalli has no record of ${possessive(up.service_name)} own incident, so the map is built from the incidents other services posted. `}
              {reduced ? "The whole cascade, as it ended." : "Scroll to run the clock forward."}
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-x-6 gap-y-1 lg:col-span-5 lg:justify-end" aria-hidden="true">
            <p className="leading-none">
              <span ref={clockRef} className="display block text-2xl font-light whitespace-nowrap md:text-3xl" />
              <span ref={offsetRef} className="mt-1.5 block text-xs text-ink-soft" />
            </p>
            <p className="pb-0.5 text-xs text-ink-soft">
              <span ref={countRef} />
            </p>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-1 items-center gap-x-6 gap-y-4 lg:grid-cols-12">
          <div className="lg:col-span-7">
            <div ref={mapWrapRef} className="mx-auto w-full" style={{ maxWidth: reduced ? 640 : "min(640px, calc(100svh - 230px))" }}>
              <ReplayMap ref={mapRef} replay={replay} scale={scale} compact={compact} />
            </div>
            {compact ? (
              <p className="mt-2 text-xs text-ink-soft">
                Clockwise from the top: {sectorList.map((s) => `${categoryLabel(s.category)} ${s.count}`).join(", ")}.
              </p>
            ) : null}
          </div>
          <div className="lg:col-span-5">
            <ol className="hidden space-y-3 lg:block" aria-hidden="true">
              {steps.map((s, i) => (
                <li
                  key={`${s.atMin}-${i}`}
                  ref={(el) => void (stepRefs.current[i] = el)}
                  data-state={reduced ? "current" : i === 0 ? "current" : "future"}
                  className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 transition-[opacity,color] duration-300 data-[state=current]:text-ink data-[state=future]:opacity-0 data-[state=past]:text-ink-soft"
                >
                  <span className="font-medium">{s.lead}</span>
                  <span>{s.text}</span>
                </li>
              ))}
            </ol>
            {reduced ? (
              <ol className="space-y-2 lg:hidden" aria-hidden="true">
                {steps.map((st, i) => (
                  <li key={i}>
                    <span className="font-medium">{st.lead}.</span> {st.text}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="min-h-[4.5rem] lg:hidden" aria-hidden="true">
                <span className="font-medium">{steps[mobileStep]?.lead}.</span> {steps[mobileStep]?.text}
              </p>
            )}
            <ReplayLegend upstream={up.service_name} epicenter={epicenter} rings={recorded ? "minutes after it began" : `minutes after ${formatClock(startMs, zone)}`} />
          </div>
        </div>

        {reduced ? null : (
          <div className="mt-5" aria-hidden="true">
            <span className="block h-px w-full bg-rule">
              <span ref={barRef} className="block h-px w-full origin-left bg-ink" style={{ transform: "scaleX(0)" }} />
            </span>
          </div>
        )}
      </div>

      <div className="sr-only">
        <p>
          Replay of the cascade around {epicenter}: within {formatDuration(replay.window_min)}, {plural(replay.totals.services, "service")}{" "}
          in {plural(cats, "category", "categories")} posted incidents, and {replay.totals.named} of them named {up.service_name}.
          {recorded ? "" : ` Mercalli has no record of ${possessive(up.service_name)} own incident.`}
        </p>
        <ol>
          {steps.map((s, i) => (
            <li key={i}>
              {s.lead}. {s.text}
            </li>
          ))}
        </ol>
        <table>
          <caption>Services that posted incidents, in order</caption>
          <thead>
            <tr>
              <th scope="col">Minutes after</th>
              <th scope="col">Service</th>
              <th scope="col">Category</th>
              <th scope="col">Impact</th>
              <th scope="col">Names {up.service_name}</th>
            </tr>
          </thead>
          <tbody>
            {replay.events
              .slice()
              .sort((a, b) => a.offset_min - b.offset_min)
              .map((e) => (
                <tr key={e.incident_id}>
                  <td>{Math.round(e.offset_min)}</td>
                  <td>{e.service_name}</td>
                  <td>{categoryLabel(e.category)}</td>
                  <td>{e.impact}</td>
                  <td>{e.names_upstream ? "Yes" : "No"}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ReplayLegend({ upstream, epicenter, rings }: { upstream: string; epicenter: string; rings: string }) {
  return (
    <ul className="mt-6 flex flex-wrap gap-x-5 gap-y-2 border-t border-grid pt-4 text-xs text-ink-soft" aria-label="Legend">
      <li className="inline-flex items-center gap-2">
        <svg width="18" height="18" viewBox="-9 -9 18 18" aria-hidden="true">
          <circle r="3.6" fill="var(--sev-critical)" />
          <circle r="7" fill="none" stroke="var(--sev-critical)" strokeWidth="1.3" />
        </svg>
        {epicenter}, at the center
      </li>
      <li className="inline-flex items-center gap-2">
        <svg width="16" height="16" viewBox="-8 -8 16 16" aria-hidden="true">
          <circle r="4.5" fill="var(--sev-degraded)" stroke="var(--sev-degraded-line)" strokeWidth="1" />
        </svg>
        Minor
      </li>
      <li className="inline-flex items-center gap-2">
        <svg width="16" height="16" viewBox="-8 -8 16 16" aria-hidden="true">
          <circle r="4.5" fill="var(--sev-major)" />
        </svg>
        Major
      </li>
      <li className="inline-flex items-center gap-2">
        <svg width="16" height="16" viewBox="-8 -8 16 16" aria-hidden="true">
          <circle r="4.5" fill="var(--sev-critical)" />
        </svg>
        Critical
      </li>
      <li className="inline-flex items-center gap-2">
        <svg width="18" height="18" viewBox="-9 -9 18 18" aria-hidden="true">
          <circle r="7" fill="none" stroke="var(--ink)" strokeWidth="1.2" />
          <circle r="4" fill="var(--ink-faint)" />
        </svg>
        Names {upstream} in its own report
      </li>
      <li className="inline-flex items-center gap-2">
        <svg width="18" height="18" viewBox="-9 -9 18 18" aria-hidden="true">
          <circle r="7.5" fill="none" stroke="var(--rule)" strokeWidth="1.2" />
        </svg>
        Rings: {rings}
      </li>
    </ul>
  );
}
