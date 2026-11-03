import type { GlyphSpec, Tone } from "@/lib/severity";
import { healthGlyph, healthLabel, impactGlyph, impactLabel } from "@/lib/severity";

function fill(tone: Tone): string {
  return tone === "faint" ? "var(--ink-faint)" : `var(--sev-${tone})`;
}

function line(tone: Tone): string {
  return tone === "faint" ? "var(--faint-line)" : `var(--sev-${tone}-line)`;
}

/**
 * The severity glyph. Fill level means severity, so the shape alone tells the reader how bad
 * something is: empty ring, center dot, half disc, full disc, and a disc inside an outer ring.
 */
export function Glyph({ spec, size = 14, className, label }: { spec: GlyphSpec; size?: number; className?: string; label?: string }) {
  const f = fill(spec.tone);
  const l = line(spec.tone);
  const common = { cx: 8, cy: 8 };
  let body;
  switch (spec.kind) {
    case "ring":
      body = <circle {...common} r={5.25} fill="none" stroke={l} strokeWidth={1.5} />;
      break;
    case "dot":
      body = (
        <>
          <circle {...common} r={5.25} fill="none" stroke={l} strokeWidth={1.5} />
          <circle {...common} r={2.3} fill={l} />
        </>
      );
      break;
    case "half":
      body = (
        <>
          <path d="M8 2.75a5.25 5.25 0 0 0 0 10.5z" fill={f} />
          <circle {...common} r={5.25} fill="none" stroke={l} strokeWidth={1.5} />
        </>
      );
      break;
    case "full":
      body = <circle {...common} r={5.6} fill={f} stroke={l} strokeWidth={1} />;
      break;
    case "epicenter":
      body = (
        <>
          <circle {...common} r={3.9} fill={f} />
          <circle {...common} r={6.6} fill="none" stroke={l} strokeWidth={1.25} />
        </>
      );
      break;
    case "dashed":
      body = <circle {...common} r={5.25} fill="none" stroke={l} strokeWidth={1.5} strokeDasharray="2.6 1.9" />;
      break;
    case "dotted":
      body = <circle {...common} r={5.25} fill="none" stroke={l} strokeWidth={1.7} strokeDasharray="0.01 2.75" strokeLinecap="round" />;
      break;
    case "small":
      body = <circle {...common} r={3.4} fill="none" stroke={l} strokeWidth={1.5} />;
      break;
  }
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      className={className}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={{ flex: "none" }}
    >
      {body}
    </svg>
  );
}

export function HealthGlyph({ health, size, className }: { health: string; size?: number; className?: string }) {
  return <Glyph spec={healthGlyph(health)} size={size} className={className} />;
}

export function ImpactGlyph({ impact, size, className }: { impact: string; size?: number; className?: string }) {
  return <Glyph spec={impactGlyph(impact)} size={size} className={className} />;
}

/** Glyph plus word, the pair used wherever severity is stated. */
export function HealthTag({ health, className }: { health: string; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${className ?? ""}`}>
      <HealthGlyph health={health} />
      <span>{healthLabel(health)}</span>
    </span>
  );
}

export function ImpactTag({ impact, className }: { impact: string; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${className ?? ""}`}>
      <ImpactGlyph impact={impact} />
      <span>{impactLabel(impact)}</span>
    </span>
  );
}
