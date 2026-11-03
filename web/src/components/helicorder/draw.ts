import type { Envelope } from "@/lib/helicorder";
import { REFERENCE_ROW, rowSeed, traceNoise } from "@/lib/helicorder";
import { HOUR } from "@/lib/format";

/** Canvas drawing for the helicorder. Geometry in CSS pixels; the caller scales for the device. */

export interface HeliLayout {
  width: number;
  height: number;
  /** Left edge of the traces (after the hour labels). */
  left: number;
  right: number;
  /** Top of the first row's band. */
  top: number;
  pitch: number;
  rows: number;
  traceWidth: number;
  /** Horizontal sampling step for the trace, in CSS pixels. */
  step: number;
  /** Pixels per pixel of the 19px reference row the amplitude is defined for. */
  amp: number;
}

export function heliLayout(width: number, rows: number): HeliLayout {
  const phone = rows <= 12;
  const left = phone ? 52 : 66;
  const right = phone ? 6 : 12;
  const top = 30;
  const pitch = phone ? 26 : 22;
  const traceWidth = Math.max(10, width - left - right);
  return {
    width,
    height: top + rows * pitch + 14,
    left,
    right,
    top,
    pitch,
    rows,
    traceWidth,
    step: 0.5,
    amp: pitch / REFERENCE_ROW,
  };
}

export interface HeliColors {
  inks: [string, string, string, string];
  grid: string;
  rule: string;
  faint: string;
}

export interface RowTrace {
  start: number;
  /** Baseline y in CSS pixels. */
  baseline: number;
  /** Which of the four inks this row uses. */
  ink: number;
  /** Displacement from the baseline at each sample (positive is up). */
  ys: Float32Array;
}

export function baselineOf(layout: HeliLayout, row: number): number {
  return layout.top + (row + 0.5) * layout.pitch;
}

export function xOf(layout: HeliLayout, rowStart: number, t: number): number {
  return layout.left + ((t - rowStart) / HOUR) * layout.traceWidth;
}

/** Row ink, chosen by the absolute hour so a row keeps its color as the drum scrolls. */
export function inkFor(rowStart: number): number {
  return ((Math.floor(rowStart / HOUR) % 4) + 4) % 4;
}

/**
 * Sample every row once. The envelope scales procedural noise seeded by the row's hour, so the
 * same hour always draws the same texture. Samples past the data's reach are left at zero and
 * drawn as a dotted baseline by `drawRows`.
 */
export function buildTraces(layout: HeliLayout, rowStarts: readonly number[], env: Envelope | null): RowTrace[] {
  const n = Math.ceil(layout.traceWidth / layout.step) + 1;
  return rowStarts.map((start, row) => {
    const seed = rowSeed(start);
    const ys = new Float32Array(n);
    for (let k = 0; k < n; k++) {
      const x = k * layout.step;
      const t = start + (x / layout.traceWidth) * HOUR;
      const a = env ? env.at(t) : 0;
      ys[k] = a === 0 ? 0 : a * layout.amp * traceNoise(seed, x);
    }
    return { start, baseline: baselineOf(layout, row), ink: inkFor(start), ys };
  });
}

export function drawGrid(ctx: CanvasRenderingContext2D, layout: HeliLayout, colors: HeliColors): void {
  const { left, traceWidth, top, rows, pitch } = layout;
  const bottom = top + rows * pitch;
  ctx.save();
  ctx.lineWidth = 1;
  for (let m = 0; m <= 60; m += 5) {
    const x = Math.round(left + (m / 60) * traceWidth) + 0.5;
    const major = m % 10 === 0;
    ctx.strokeStyle = m === 0 || m === 60 ? colors.rule : colors.grid;
    ctx.globalAlpha = major ? 1 : 0.55;
    ctx.beginPath();
    ctx.moveTo(x, top - (major ? 6 : 3));
    ctx.lineTo(x, bottom);
    ctx.stroke();
  }
  ctx.restore();
}

export interface DrawWindow {
  /** Draw nothing at or after this time (the pen's position). */
  now: number;
  /** Time the measured data reaches; later samples are drawn as a dotted baseline. */
  liveUntil: number;
  /** Per row share of the row to draw, for the entrance. 1 when omitted. */
  progress?: (row: number) => number;
}

/** Stroke every row up to the pen, or up to its entrance progress. */
export function drawRows(
  ctx: CanvasRenderingContext2D,
  layout: HeliLayout,
  traces: readonly RowTrace[],
  colors: HeliColors,
  w: DrawWindow,
): void {
  const { left, traceWidth, step } = layout;
  ctx.save();
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  traces.forEach((row, i) => {
    const share = w.progress ? Math.min(1, Math.max(0, w.progress(i))) : 1;
    if (share <= 0) return;
    const rowEnd = row.start + HOUR;
    const untilT = Math.min(rowEnd, w.now);
    if (untilT <= row.start) return;
    const lastK = Math.min(row.ys.length - 1, Math.floor((((untilT - row.start) / HOUR) * traceWidth * share) / step));
    const liveK = Math.floor(((Math.min(Math.max(w.liveUntil, row.start), rowEnd) - row.start) / HOUR) * traceWidth / step);
    const solidEnd = Math.min(lastK, liveK);

    ctx.strokeStyle = colors.inks[row.ink];
    ctx.lineWidth = 1;
    ctx.globalAlpha = 0.94;
    ctx.beginPath();
    for (let k = 0; k <= solidEnd; k++) {
      const x = left + k * step;
      const y = row.baseline - row.ys[k];
      if (k === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    if (lastK > solidEnd) {
      // No measurement here: the record says so with a dotted baseline instead of invented ink.
      ctx.globalAlpha = 0.6;
      ctx.setLineDash([1, 4]);
      ctx.beginPath();
      ctx.moveTo(left + Math.max(0, solidEnd) * step, row.baseline);
      ctx.lineTo(left + lastK * step, row.baseline);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  });
  ctx.restore();
}

/** The soft ink bleed under the newest stretch of trace. */
export function drawBleed(ctx: CanvasRenderingContext2D, layout: HeliLayout, row: RowTrace, colors: HeliColors, now: number): void {
  const { left, traceWidth, step } = layout;
  const lastK = Math.min(row.ys.length - 1, Math.floor((((now - row.start) / HOUR) * traceWidth) / step));
  const firstK = Math.max(0, lastK - Math.round(36 / step));
  if (lastK <= firstK) return;
  ctx.save();
  ctx.strokeStyle = colors.inks[row.ink];
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  for (let k = firstK; k < lastK; k++) {
    // Fade in toward the nib so the bleed has no hard start.
    ctx.globalAlpha = 0.16 * ((k - firstK) / (lastK - firstK));
    ctx.lineWidth = 3.2;
    ctx.beginPath();
    ctx.moveTo(left + k * step, row.baseline - row.ys[k]);
    ctx.lineTo(left + (k + 1) * step, row.baseline - row.ys[k + 1]);
    ctx.stroke();
  }
  ctx.restore();
}

/** Where the pen is: x and y of the trace at `now` on its row. */
export function penPoint(layout: HeliLayout, row: RowTrace, now: number): { x: number; y: number } {
  const k = Math.min(row.ys.length - 1, Math.max(0, Math.floor((((now - row.start) / HOUR) * layout.traceWidth) / layout.step)));
  return { x: layout.left + k * layout.step, y: row.baseline - row.ys[k] };
}
