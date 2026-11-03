# Mercalli web: design notes

The site is the record of a seismograph observatory for the internet. One instrument, the
helicorder, carries the boldness; everything around it is set like a printed station bulletin:
paper, ink, hairline rules, numbers in sentences.

## Principles

1. **Structure carries information.** Row order means time, rings mean time from an epicenter,
   color means severity. Nothing is drawn that does not encode something.
2. **Problems carry color, health does not.** Operational is a quiet outline. Severity color only
   appears on glyphs and marks, never on text, and every glyph is paired with a word.
3. **Numbers live in sentences.** "27 active incidents across 14 services", not a tile that says
   "27". Every number comes from the API; when a document is missing the page says so plainly.
4. **Calm by default, one entrance.** The helicorder inks in once on load. Everything else moves
   only when data changes or when the reader asks (scroll the replay, change a filter).
5. **Honest about time.** Every page knows how old its data is, says it, and keeps the last good
   render on screen when a refresh fails.

## Tokens

Colors are CSS custom properties on `:root` (light "paper"), redefined for dark ("night recorder")
under `prefers-color-scheme` unless `data-theme="light"`, and under `data-theme="dark"`. Values are
exactly the brief's.

| Role | Tokens |
| --- | --- |
| Surfaces | `--paper`, `--paper-raised`, `--grid`, `--rule` |
| Text | `--ink` (body), `--ink-soft` (secondary text), `--ink-faint` (non-text marks only) |
| Helicorder inks | `--trace-1` to `--trace-4`, cycling per row |
| Severity (MMI ramp) | `--sev-operational`, `--sev-maintenance`, `--sev-degraded`, `--sev-partial`, `--sev-major`, `--sev-critical` |
| Interaction | `--accent` (links, focus) |

Contrast, measured: `--ink-faint` is 3.3:1 on light paper, so it is used for rules, ticks and
glyph outlines, never for small text; secondary text uses `--ink-soft` (6.9:1 light, 8.6:1 dark).
Yellow, orange and the operational grey fail as text on light paper, so severity words are set in
ink beside a colored glyph. Light-theme glyphs get a stroke mixed toward ink so they clear 3:1.

Severity glyph system (shape plus color, readable in grayscale): fill level means severity.
Operational is an empty ring, degraded or minor a ring with a center dot, partial outage a
half-filled disc, major a full disc, critical a full disc inside an outer ring (an epicenter),
maintenance a dashed ring, unknown a dotted ring, an advisory with no impact a small hollow ring.

Intensity levels I to X map onto the same ramp: I to II operational, III maintenance blue (MMI
weak shaking is blue), IV to V degraded, VI to VII partial, VIII to IX major, X critical.

## Type

Geologica variable (wght plus the SHRP sharpness axis) for everything, Martian Mono only for code,
API paths and the helicorder's axes. Both self-hosted from fontsource files through `next/font`
with size-adjusted fallbacks; latin-ext is a second face behind a unicode-range so it only loads
when a vendor title needs it.

Scale 1.25 from a 16px body: 12.8, 16, 20, 25, 31.25, 39, 48.8, 61. Display sizes use weight 300
to 400 with SHRP 40 to 60 for an engraved-instrument feel; headings 600; body 400 at 1.55 line
height, prose capped at 68ch. `tabular-nums` everywhere numbers or times appear. Sentence case
throughout; no all-caps labels, no eyebrows, no middle-dot metadata, no arrows on links.

## Layout

12 columns, 24px gutters, content max 1320px, side margins 16px (phones), 32px (tablets), 60px
(desktop). Sections are separated by a full-width hairline and 96px of space. Inside a section,
the heading and a one-line statement of what the numbers mean sit in columns 1 to 4 on desktop and
the content in 5 to 12, like marginal heads in a printed report; the helicorder and the replay
break out to all 12 columns. Below 768px everything is one column with 16px gutters and no
horizontal scroll at 360px.

Cards are ruled ledger entries: `--paper-raised`, a 1px `--rule` border, 3px corners, no shadow,
the severity glyph hung in a left margin column.

## States

- **Loading:** quiet placeholders sized like what will replace them, so nothing jumps.
- **Not published yet:** a dashed frame and one plain sentence saying which document will fill
  it. Dashes mean "nothing measured here", the same language as the helicorder's dotted baseline.
- **Stale:** the pen keeps drawing for 20 minutes past the last record, then the ink stops and a
  dotted baseline runs to the present; the top bar says how old the data is.
- **Failed refresh:** the last good render stays on screen with a line saying the refresh failed.

## The helicorder

One row per hour, newest at the bottom, inks cycling per row by absolute hour. Amplitude, in
pixels for a 19px row and scaled to the row height, follows the README helicorder:
`0.55 + 2.2 * sqrt(sustained) + 7.8 * burst`, where `sustained` is the weighted count above the
20th percentile of the window drawn (so a permanently busy internet reads as a quiet line) and
`burst` is the decayed weight of new incidents (`seismograph.arrivals`, seven minute time
constant, saturating at 3). The fine wiggle is value noise seeded by the hour, so it is stable
across reloads. At most ten flags mark major and critical starts, critical first, and a flag whose
label would collide with another on the same row is left out. Every bucket is readable by hover or
keyboard, and the whole record is available as a table.

## Motion inventory

| Moment | Motion | Reduced motion |
| --- | --- | --- |
| Home load | Helicorder rows ink in top to bottom, about 1.2s, each row swept left to right by the pen. The page's only orchestrated entrance. | Final state drawn at once |
| Live | Pen nib advances on the current row (canvas, about 1px every 3s), soft ink bleed breathing behind it. Paused while the tab is hidden. | Nib advances, no breathing |
| New incident on a poll | One epicenter ripple: three rings expand once from the service name, 900ms. | Static ring outline for 10s |
| Replay | Pinned and scrubbed by scroll (GSAP ScrollTrigger): a wavefront circle sweeps outward with the clock, dots arrive with a single ring pulse, narration steps advance. | Final map plus narration as a list |
| Board filters | Motion layout animation, 320ms. | Instant |
| Disclosure | Height and opacity, 200ms. | Instant |
| Hover and focus | Color and outline, 120ms. | Same |

Easing: `cubic-bezier(0.2, 0, 0, 1)` for interface motion; linear for anything that represents
time passing (pen, wavefront).

## Revisions after checking against the brief

A first pass produced things any status page would have. Each was replaced:

- **A row of KPI tiles** (open incidents, services affected, intensity, last update) read as a
  generic dashboard. Replaced by the hero sentence, the intensity scale readout and counts written
  into section statements.
- **Status pills** (colored capsules with white text) failed contrast for yellow and orange and
  looked like every SaaS status page. Replaced by the fill-level glyph plus a plain word in ink.
- **Green dots for healthy services** made the board shout. Operational is now an outline ring in
  the quiet operational grey; only problems carry color.
- **Charts in shadowed white cards** added chrome that encodes nothing. Charts sit on the paper;
  a plotting grid appears only inside plot areas, where it marks real axis intervals.
- **Colored left borders on incident cards** are a dashboard cliche and color-only. The glyph sits
  in a margin column, paired with the impact word.
- **A uniform three-column card grid for every section** flattened the hierarchy. Sections now use
  marginal heads with content on the right, and only the two instruments span the full width.
- **Hiding the long-running incidents** would undercount what vendors still list as open. They
  are kept, collapsed, in their own quieter ledger with a sentence on why they are separate.
