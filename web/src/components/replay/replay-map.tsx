"use client";

import { useImperativeHandle, useMemo, useRef, type Ref } from "react";
import type { Replay } from "@/lib/api/types";
import { formatDuration } from "@/lib/format";
import { placeEvents, radiusShare, ringsFor, sectors } from "@/lib/replay";
import { categoryLabel, impactGlyph, impactLabel } from "@/lib/severity";

/** Outer ring radius in SVG units; the view box leaves room for category labels. */
const R = 250;
const PAD = 96;
const PULSE_MIN = 7;
/** A small disc kept free around the epicenter, as a share of the outer ring. */
export const INNER = 0.09;

export interface ReplayMapHandle {
  /** Move the replay clock, in minutes after the upstream incident began. */
  setClock(minute: number): void;
}

function ringLabel(m: number): string {
  return m < 60 ? `${m} min` : formatDuration(m);
}

/**
 * The isoseismal map. The upstream provider sits at the epicenter; rings mark minutes after it
 * began; each downstream service is a dot at the radius of its own incident's start, in its
 * category's sector, colored by its own impact. Driven imperatively so scrolling never renders.
 */
export function ReplayMap({ replay, scale, compact = false, ref }: { replay: Replay; scale: number; compact?: boolean; ref: Ref<ReplayMapHandle> }) {
  const secs = useMemo(() => sectors(replay.events), [replay.events]);
  const placed = useMemo(() => placeEvents(replay.events, secs, replay.window_min, INNER), [replay.events, secs, replay.window_min]);
  // Big cascades get smaller dots so they stay apart.
  const DOT = replay.events.length > 90 ? 4.2 : 5.5;
  const rings = ringsFor(replay.window_min);
  const dots = useRef<(SVGGElement | null)[]>([]);
  const pulses = useRef<(SVGCircleElement | null)[]>([]);
  const front = useRef<SVGCircleElement>(null);
  const area = useRef<SVGCircleElement>(null);
  const ringEls = useRef<(SVGGElement | null)[]>([]);

  const apply = (minute: number) => {
    const r = radiusShare(minute, replay.window_min, INNER) * R;
    front.current?.setAttribute("r", String(Math.max(0.01, r)));
    area.current?.setAttribute("r", String(Math.max(0.01, r)));
    rings.forEach((m, i) => {
      const el = ringEls.current[i];
      if (el) el.style.opacity = minute >= m ? "1" : "0.38";
    });
    placed.forEach((p, i) => {
      const g = dots.current[i];
      const pulse = pulses.current[i];
      const dt = minute - p.event.offset_min;
      if (g) {
        const on = dt >= 0;
        const grow = on ? Math.min(1, dt / 1.5) : 0;
        g.style.opacity = on ? "1" : "0";
        g.style.transform = `scale(${0.4 + 0.6 * grow})`;
      }
      if (pulse) {
        // One ring pulse as the incident begins, a function of the clock so it scrubs backwards too.
        const k = dt >= 0 && dt <= 8 ? dt / 8 : -1;
        pulse.style.opacity = k >= 0 ? String(0.75 * (1 - k)) : "0";
        pulse.setAttribute("r", String(PULSE_MIN + 18 * Math.max(0, k)));
      }
    });
  };

  useImperativeHandle(ref, () => ({ setClock: apply }));

  const fs = 11.5 * scale;
  // On narrow screens the category names move out of the map into a line of text below it.
  const vb = R + (compact ? 14 : PAD);
  return (
    <svg
      viewBox={`${-vb} ${-vb} ${vb * 2} ${vb * 2}`}
      className="block h-auto w-full overflow-visible"
      aria-hidden="true"
    >
      <circle ref={area} cx="0" cy="0" r="0.01" fill="color-mix(in oklab, var(--accent) 7%, transparent)" />
      {secs.map((s) => (
        <line
          key={`div-${s.category}`}
          x1={Math.cos(s.a0 - 0.03) * 18}
          y1={Math.sin(s.a0 - 0.03) * 18}
          x2={Math.cos(s.a0 - 0.03) * (R + 8)}
          y2={Math.sin(s.a0 - 0.03) * (R + 8)}
          stroke="var(--grid)"
          strokeWidth="1"
        />
      ))}
      {rings.map((m, i) => {
        const r = radiusShare(m, replay.window_min, INNER) * R;
        return (
          <g key={m} ref={(el) => void (ringEls.current[i] = el)} style={{ transition: "opacity 200ms" }}>
            <circle cx="0" cy="0" r={r} fill="none" stroke="var(--rule)" strokeWidth="1" />
            <text
              x={0}
              y={-r + 4 * scale}
              dominantBaseline="hanging"
              textAnchor="middle"
              fontSize={fs * 0.92}
              fill="var(--ink-soft)"
              stroke="var(--paper)"
              strokeWidth={4 * scale}
              paintOrder="stroke"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {ringLabel(m)}
            </text>
          </g>
        );
      })}
      <circle ref={front} cx="0" cy="0" r="0.01" fill="none" stroke="var(--accent)" strokeWidth={1.5 * Math.max(1, scale * 0.8)} />
      {compact
        ? null
        : secs.map((s) => {
        const lr = R + 16 * scale;
        const x = Math.cos(s.mid) * lr;
        const y = Math.sin(s.mid) * lr;
        const anchor = Math.abs(Math.cos(s.mid)) < 0.25 ? "middle" : Math.cos(s.mid) > 0 ? "start" : "end";
        return (
          <text
            key={`label-${s.category}`}
            x={x}
            y={y}
            dy={Math.sin(s.mid) > 0.5 ? fs * 0.9 : Math.sin(s.mid) < -0.5 ? -fs * 0.2 : fs * 0.35}
            textAnchor={anchor}
            fontSize={fs}
            fill="var(--ink-soft)"
          >
            {categoryLabel(s.category)} {s.count}
          </text>
        );
      })}
      {placed.map((p, i) => {
        const x = Math.cos(p.angle) * p.r * R;
        const y = Math.sin(p.angle) * p.r * R;
        const tone = impactGlyph(p.event.impact).tone;
        const fill = tone === "faint" ? "var(--ink-faint)" : `var(--sev-${tone})`;
        return (
          <g key={p.event.incident_id} transform={`translate(${x} ${y})`}>
            <circle ref={(el) => void (pulses.current[i] = el)} r={PULSE_MIN} fill="none" stroke={fill} strokeWidth="1.5" style={{ opacity: 0 }} />
            <g ref={(el) => void (dots.current[i] = el)} style={{ opacity: 0, transformBox: "fill-box", transformOrigin: "center" }}>
              {p.event.names_upstream ? <circle r={DOT + 3.2} fill="none" stroke="var(--ink)" strokeWidth="1.2" /> : null}
              <circle r={DOT} fill={fill} stroke="var(--paper)" strokeWidth="2" />
              <title>
                {`${p.event.service_name}, ${impactLabel(p.event.impact).toLowerCase()}, +${formatDuration(p.event.offset_min)}: ${p.event.title}`}
              </title>
            </g>
          </g>
        );
      })}
      <circle r="9" fill="var(--sev-critical)" />
      <circle r="15" fill="none" stroke="var(--sev-critical)" strokeWidth="1.5" />
    </svg>
  );
}
