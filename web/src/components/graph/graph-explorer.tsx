"use client";

import { quadtree, type Quadtree } from "d3-quadtree";
import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type WheelEvent } from "react";
import { useOverview } from "@/components/overview-context";
import { HealthGlyph } from "@/components/ui/glyph";
import { PageHeader } from "@/components/ui/page-header";
import { FixtureNote, Pending } from "@/components/ui/section";
import { Ago } from "@/components/ui/time";
import type { EdgeKind, GraphDoc, GraphEdge, GraphNode } from "@/lib/api/types";
import { useArtifact } from "@/lib/api/use-artifact";
import { formatCount, plural } from "@/lib/format";
import { useReducedMotionPref, useResolvedTheme } from "@/lib/prefs";
import { categoryLabel, healthLabel } from "@/lib/severity";
import { useElementWidth } from "@/lib/use-width";

type Style = "census" | "named" | "cofailure";

const KIND_LABEL: Record<EdgeKind, string> = {
  dns: "DNS",
  edge: "Edge network",
  hosting: "Hosting",
  named: "Named in incident reports",
  cofailure: "Fails together",
};

function styleOf(e: GraphEdge): Style {
  if (e.kinds.includes("named")) return "named";
  if (e.kinds.includes("cofailure") && !e.kinds.some((k) => k === "dns" || k === "edge" || k === "hosting")) return "cofailure";
  return "census";
}

function radius(dependents: number): number {
  return Math.min(24, 2.4 + Math.sqrt(dependents) * 0.85);
}

export function GraphExplorer() {
  const doc = useArtifact<GraphDoc>("graph.json");
  const g = doc.data;
  return (
    <>
      <PageHeader
        title="Dependency graph"
        lead={
          <p>
            Who depends on whom. Nodes are services and the providers they rely on, sized by how many depend on them; edges
            come from the census, from incident reports that name a provider, and from failures that happen together.
          </p>
        }
        meta={
          g ? (
            <>
              {plural(g.nodes.length, "node")} and {plural(g.edges.length, "edge")}, built <Ago iso={g.generated_at} />
            </>
          ) : null
        }
      />
      {doc.status === "loading" ? (
        <div className="h-[60vh]" aria-busy="true" />
      ) : !g ? (
        <Pending title={doc.status === "missing" ? "The dependency graph has not been published yet." : "The dependency graph could not be loaded."}>
          It is built offline from the census and from incident history and published as graph.json. Until then, each
          service page lists the dependencies already known for it.
        </Pending>
      ) : (
        <div className="pb-16">
          <FixtureNote show={doc.fixture} />
          <GraphView graph={g} />
        </div>
      )}
    </>
  );
}

interface Colors {
  ink: string;
  soft: string;
  faint: string;
  rule: string;
  paper: string;
  accent: string;
  health: Record<string, string>;
}

function readColors(): Colors {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim();
  return {
    ink: v("--ink"),
    soft: v("--ink-soft"),
    faint: v("--ink-faint"),
    rule: v("--rule"),
    paper: v("--paper-raised"),
    accent: v("--accent"),
    health: {
      operational: v("--sev-operational"),
      maintenance: v("--sev-maintenance"),
      degraded: v("--sev-degraded"),
      partial_outage: v("--sev-partial"),
      major_outage: v("--sev-major"),
      unknown: v("--ink-faint"),
    },
  };
}

interface View {
  k: number;
  x: number;
  y: number;
}

function GraphView({ graph: raw }: { graph: GraphDoc }) {
  const { data: overview } = useOverview();
  // The graph does not carry health; watched services take theirs from the live overview.
  const graph = useMemo<GraphDoc>(() => {
    if (!overview || raw.nodes.every((n) => n.health)) return raw;
    const health = new Map(overview.services.map((s) => [s.id, s.health]));
    return { ...raw, nodes: raw.nodes.map((n) => (n.health || !health.has(n.id) ? n : { ...n, health: health.get(n.id) })) };
  }, [raw, overview]);
  const theme = useResolvedTheme();
  const reduced = useReducedMotionPref();
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const width = useElementWidth(wrapRef);
  const height = width && width < 640 ? 460 : 640;
  const [positions, setPositions] = useState<Float32Array | null>(null);
  const [progress, setProgress] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  const [query, setQuery] = useState("");
  const [notFound, setNotFound] = useState(false);
  const view = useRef<View>({ k: 1, x: 0, y: 0 });
  const frame = useRef(0);
  const drawRef = useRef<() => void>(() => {});
  const listId = useId();

  const index = useMemo(() => new Map(graph.nodes.map((n, i) => [n.id, i])), [graph.nodes]);
  const radii = useMemo(() => graph.nodes.map((n) => radius(n.dependents)), [graph.nodes]);
  const edges = useMemo(
    () =>
      graph.edges
        .map((e) => ({ e, s: index.get(e.source), t: index.get(e.target), style: styleOf(e) }))
        .filter((x): x is { e: GraphEdge; s: number; t: number; style: Style } => x.s != null && x.t != null),
    [graph.edges, index],
  );
  const adjacency = useMemo(() => {
    const out: number[][] = graph.nodes.map(() => []);
    const into: number[][] = graph.nodes.map(() => []);
    edges.forEach((x, k) => {
      out[x.s].push(k);
      into[x.t].push(k);
    });
    return { out, into };
  }, [edges, graph.nodes]);
  const ranked = useMemo(() => graph.nodes.map((n, i) => ({ n, i })).sort((a, b) => b.n.dependents - a.n.dependents), [graph.nodes]);
  const labelled = useMemo(() => new Set(ranked.slice(0, 28).map((r) => r.i)), [ranked]);
  const neighbors = useMemo(() => {
    if (selected == null) return null;
    const set = new Set<number>([selected]);
    for (const k of adjacency.out[selected]) set.add(edges[k].t);
    for (const k of adjacency.into[selected]) set.add(edges[k].s);
    return set;
  }, [selected, adjacency, edges]);

  // Layout in a worker; the main thread only draws.
  useEffect(() => {
    const worker = new Worker(new URL("./layout.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (e: MessageEvent<{ type: string; value?: number; positions?: Float32Array }>) => {
      if (e.data.type === "progress") setProgress(e.data.value ?? 0);
      if (e.data.type === "done" && e.data.positions) {
        setPositions(e.data.positions);
        worker.terminate();
      }
    };
    worker.postMessage({ radii, edges: edges.map((x) => [x.s, x.t, x.e.weight]), ticks: 240 });
    return () => worker.terminate();
  }, [radii, edges]);

  const tree = useMemo<Quadtree<number> | null>(() => {
    if (!positions) return null;
    return quadtree<number>()
      .x((i) => positions[i * 2])
      .y((i) => positions[i * 2 + 1])
      .addAll(graph.nodes.map((_, i) => i));
  }, [positions, graph.nodes]);

  // Fit the bulk of the graph: a few isolated nodes drift far out and would shrink everything else.
  const fit = useCallback(() => {
    if (!positions || !width) return;
    const xs: number[] = [];
    const ys: number[] = [];
    for (let i = 0; i < positions.length; i += 2) {
      xs.push(positions[i]);
      ys.push(positions[i + 1]);
    }
    xs.sort((a, b) => a - b);
    ys.sort((a, b) => a - b);
    const at = (v: number[], q: number) => v[Math.min(v.length - 1, Math.max(0, Math.round((v.length - 1) * q)))];
    const minX = at(xs, 0.02);
    const maxX = at(xs, 0.98);
    const minY = at(ys, 0.02);
    const maxY = at(ys, 0.98);
    const k = Math.min(width / (maxX - minX + 80), height / (maxY - minY + 80));
    view.current = { k, x: -((minX + maxX) / 2) * k, y: -((minY + maxY) / 2) * k };
  }, [positions, width, height]);

  const schedule = useCallback(() => {
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => drawRef.current());
  }, []);

  // The drawing routine, rebuilt when anything it reads changes.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !width || !positions) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const colors = readColors();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const P = positions;
    drawRef.current = () => {
      const { k, x, y } = view.current;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.translate(width / 2 + x, height / 2 + y);
      ctx.scale(k, k);
      const dim = neighbors != null;
      const styles: { style: Style; color: string; width: number; alpha: number; dash: number[] }[] = [
        { style: "census", color: colors.faint, width: 0.7, alpha: dim ? 0.06 : 0.22, dash: [] },
        { style: "cofailure", color: colors.soft, width: 0.9, alpha: dim ? 0.06 : 0.4, dash: [3, 3] },
        { style: "named", color: colors.accent, width: 0.8, alpha: dim ? 0.06 : 0.2, dash: [] },
      ];
      for (const st of styles) {
        ctx.beginPath();
        for (const ed of edges) {
          if (ed.style !== st.style) continue;
          ctx.moveTo(P[ed.s * 2], P[ed.s * 2 + 1]);
          ctx.lineTo(P[ed.t * 2], P[ed.t * 2 + 1]);
        }
        ctx.globalAlpha = st.alpha;
        ctx.strokeStyle = st.color;
        ctx.lineWidth = st.width / Math.sqrt(k);
        ctx.setLineDash(st.dash.map((d) => d / k));
        ctx.stroke();
      }
      ctx.setLineDash([]);
      if (selected != null) {
        // A hub with a thousand edges would otherwise turn solid: thin the ink as the count grows.
        const count = adjacency.out[selected].length + adjacency.into[selected].length;
        const alpha = Math.min(0.9, Math.max(0.12, 27 / Math.max(1, count)));
        for (const list of [adjacency.out[selected], adjacency.into[selected]]) {
          for (const kk of list) {
            const ed = edges[kk];
            const st = styles.find((s) => s.style === ed.style) ?? styles[0];
            ctx.beginPath();
            ctx.moveTo(P[ed.s * 2], P[ed.s * 2 + 1]);
            ctx.lineTo(P[ed.t * 2], P[ed.t * 2 + 1]);
            ctx.globalAlpha = alpha;
            ctx.strokeStyle = ed.style === "census" ? colors.soft : st.color;
            ctx.lineWidth = 1.4 / Math.sqrt(k);
            ctx.setLineDash(st.dash.map((d) => d / k));
            ctx.stroke();
          }
        }
        ctx.setLineDash([]);
      }
      // Nodes, batched by fill.
      const groups = new Map<string, number[]>();
      graph.nodes.forEach((n, i) => {
        const fill = n.kind === "provider" ? colors.soft : colors.health[n.health ?? "operational"] ?? colors.faint;
        const list = groups.get(fill) ?? [];
        list.push(i);
        groups.set(fill, list);
      });
      for (const [fill, list] of groups) {
        for (const pass of [0, 1]) {
          ctx.beginPath();
          for (const i of list) {
            const on = !dim || neighbors.has(i);
            if ((pass === 0) === on) continue;
            ctx.moveTo(P[i * 2] + radii[i], P[i * 2 + 1]);
            ctx.arc(P[i * 2], P[i * 2 + 1], radii[i], 0, Math.PI * 2);
          }
          ctx.globalAlpha = pass === 0 ? 0.18 : 1;
          ctx.fillStyle = fill;
          ctx.fill();
          if (pass === 1) {
            ctx.lineWidth = 1 / k;
            ctx.strokeStyle = colors.paper;
            ctx.stroke();
          }
        }
      }
      ctx.globalAlpha = 1;
      if (selected != null) {
        ctx.beginPath();
        ctx.arc(P[selected * 2], P[selected * 2 + 1], radii[selected] + 3.5 / k, 0, Math.PI * 2);
        ctx.strokeStyle = colors.ink;
        ctx.lineWidth = 1.6 / k;
        ctx.stroke();
      }
      // Labels: the selection first, then the biggest hubs, then more as you zoom in. A label
      // that would overlap one already placed is skipped rather than drawn on top of it.
      ctx.font = `500 ${11 / k}px ${getComputedStyle(document.body).fontFamily}`;
      ctx.textBaseline = "middle";
      ctx.lineJoin = "round";
      const candidates: number[] = [];
      if (selected != null) candidates.push(selected);
      for (const { i } of ranked) {
        if (i === selected) continue;
        const show = neighbors ? neighbors.has(i) && (labelled.has(i) || k > 3.2 || neighbors.size < 40) : labelled.has(i) || (k > 2.6 && radii[i] > 4);
        if (show) candidates.push(i);
      }
      const placed: [number, number, number, number][] = [];
      const pad = 3 / k;
      for (const i of candidates) {
        const name = graph.nodes[i].name;
        const tx = P[i * 2] + radii[i] + 4 / k;
        const ty = P[i * 2 + 1];
        const w = ctx.measureText(name).width;
        const box: [number, number, number, number] = [tx - pad, ty - 7 / k, tx + w + pad, ty + 7 / k];
        if (i !== selected && placed.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) continue;
        placed.push(box);
        ctx.strokeStyle = colors.paper;
        ctx.lineWidth = 3 / k;
        ctx.strokeText(name, tx, ty);
        ctx.fillStyle = i === selected ? colors.ink : colors.soft;
        ctx.fillText(name, tx, ty);
      }
    };
    schedule();
  }, [width, height, positions, edges, graph.nodes, radii, selected, neighbors, adjacency, labelled, ranked, theme, schedule]);

  // Fit once the layout arrives or the size changes.
  useEffect(() => {
    if (!positions || !width) return;
    fit();
    schedule();
  }, [positions, width, fit, schedule]);

  const animateTo = useCallback(
    (target: View) => {
      if (reduced) {
        view.current = target;
        schedule();
        return;
      }
      const from = { ...view.current };
      const t0 = performance.now();
      const step = () => {
        const p = Math.min(1, (performance.now() - t0) / 520);
        const e = 1 - Math.pow(1 - p, 3);
        view.current = { k: from.k + (target.k - from.k) * e, x: from.x + (target.x - from.x) * e, y: from.y + (target.y - from.y) * e };
        drawRef.current();
        if (p < 1) frame.current = requestAnimationFrame(step);
      };
      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(step);
    },
    [reduced, schedule],
  );

  const focusNode = useCallback(
    (i: number) => {
      setSelected(i);
      if (!positions) return;
      const k = Math.max(view.current.k, 2.2);
      animateTo({ k, x: -positions[i * 2] * k, y: -positions[i * 2 + 1] * k });
    },
    [positions, animateTo],
  );

  const toWorld = (sx: number, sy: number) => {
    const { k, x, y } = view.current;
    return { wx: (sx - width / 2 - x) / k, wy: (sy - height / 2 - y) / k };
  };

  const pick = (sx: number, sy: number): number | null => {
    if (!tree || !positions) return null;
    const { wx, wy } = toWorld(sx, sy);
    const found = tree.find(wx, wy, 26 / view.current.k);
    if (found == null) return null;
    const dx = positions[found * 2] - wx;
    const dy = positions[found * 2 + 1] - wy;
    return Math.hypot(dx, dy) <= radii[found] + 6 / view.current.k ? found : null;
  };

  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean; pinch?: { d: number; k: number } } | null>(null);

  const onPointerDown = (e: PointerEvent<HTMLCanvasElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    const r = e.currentTarget.getBoundingClientRect();
    pointers.current.set(e.pointerId, { x: e.clientX - r.left, y: e.clientY - r.top });
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      drag.current = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, vx: view.current.x, vy: view.current.y, moved: true, pinch: { d: Math.hypot(a.x - b.x, a.y - b.y), k: view.current.k } };
    } else {
      drag.current = { x: e.clientX - r.left, y: e.clientY - r.top, vx: view.current.x, vy: view.current.y, moved: false };
    }
  };

  const onPointerMove = (e: PointerEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const sx = e.clientX - r.left;
    const sy = e.clientY - r.top;
    if (pointers.current.has(e.pointerId)) pointers.current.set(e.pointerId, { x: sx, y: sy });
    const d = drag.current;
    if (d && d.pinch && pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const k = Math.min(10, Math.max(0.15, d.pinch.k * (dist / d.pinch.d)));
      view.current = { ...view.current, k };
      schedule();
      return;
    }
    if (d) {
      const dx = sx - d.x;
      const dy = sy - d.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
      if (d.moved) {
        view.current = { ...view.current, x: d.vx + dx, y: d.vy + dy };
        schedule();
        return;
      }
    }
    const i = pick(sx, sy);
    setHover((h) => (i == null ? null : h && h.i === i ? h : { i, x: sx, y: sy }));
  };

  const onPointerUp = (e: PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    pointers.current.delete(e.pointerId);
    if (d && !d.moved) {
      const r = e.currentTarget.getBoundingClientRect();
      const i = pick(e.clientX - r.left, e.clientY - r.top);
      setSelected(i);
    }
    if (pointers.current.size === 0) drag.current = null;
  };

  const zoomBy = (factor: number, sx = width / 2, sy = height / 2) => {
    const { k, x, y } = view.current;
    const nk = Math.min(10, Math.max(0.15, k * factor));
    const wx = (sx - width / 2 - x) / k;
    const wy = (sy - height / 2 - y) / k;
    view.current = { k: nk, x: sx - width / 2 - wx * nk, y: sy - height / 2 - wy * nk };
    schedule();
  };

  const onWheel = (e: WheelEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    zoomBy(Math.exp(-e.deltaY * 0.0016), e.clientX - r.left, e.clientY - r.top);
  };

  // Wheel zoom must be able to prevent page scroll, which React's passive listener cannot.
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const stop = (e: globalThis.WheelEvent) => e.preventDefault();
    c.addEventListener("wheel", stop, { passive: false });
    return () => c.removeEventListener("wheel", stop);
  }, [positions]);

  const onKeyDown = (e: KeyboardEvent<HTMLCanvasElement>) => {
    const step = 60;
    if (e.key === "+" || e.key === "=") zoomBy(1.25);
    else if (e.key === "-") zoomBy(0.8);
    else if (e.key === "ArrowLeft") view.current = { ...view.current, x: view.current.x + step };
    else if (e.key === "ArrowRight") view.current = { ...view.current, x: view.current.x - step };
    else if (e.key === "ArrowUp") view.current = { ...view.current, y: view.current.y + step };
    else if (e.key === "ArrowDown") view.current = { ...view.current, y: view.current.y - step };
    else if (e.key === "Escape") setSelected(null);
    else return;
    e.preventDefault();
    schedule();
  };

  const search = (q: string) => {
    const s = q.trim().toLowerCase();
    if (!s) return;
    const exact = graph.nodes.findIndex((n) => n.name.toLowerCase() === s || n.id.toLowerCase() === s);
    const i = exact >= 0 ? exact : graph.nodes.findIndex((n) => n.name.toLowerCase().startsWith(s));
    const j = i >= 0 ? i : graph.nodes.findIndex((n) => n.name.toLowerCase().includes(s));
    setNotFound(j < 0);
    if (j >= 0) focusNode(j);
  };

  const sel = selected != null ? graph.nodes[selected] : null;
  const hoverNode = hover ? graph.nodes[hover.i] : null;

  return (
    <div className="grid grid-cols-1 gap-x-6 gap-y-6 lg:grid-cols-12">
      <div className="lg:col-span-8">
        <form
          className="mb-3 flex flex-wrap items-center gap-2"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            search(query);
          }}
        >
          <label htmlFor={`${listId}-q`} className="sr-only">
            Find a service or provider
          </label>
          <input
            id={`${listId}-q`}
            className="field w-full sm:w-72"
            list={listId}
            placeholder="Find a service or provider"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setNotFound(false);
              if (graph.nodes.some((n) => n.name === e.target.value)) search(e.target.value);
            }}
          />
          <datalist id={listId}>
            {ranked.map(({ n }) => (
              <option key={n.id} value={n.name} />
            ))}
          </datalist>
          <button type="submit" className="button">
            Find
          </button>
          <span className="ml-auto flex gap-1">
            <button type="button" className="button px-3" onClick={() => zoomBy(1.3)} aria-label="Zoom in">
              +
            </button>
            <button type="button" className="button px-3" onClick={() => zoomBy(0.77)} aria-label="Zoom out">
              &minus;
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setSelected(null);
                fit();
                schedule();
              }}
            >
              Fit
            </button>
          </span>
          {notFound ? (
            <p className="w-full text-xs text-ink" role="status">
              Nothing in the graph matches that name.
            </p>
          ) : null}
        </form>
        <div ref={wrapRef} className="relative overflow-hidden rounded-sm border border-rule bg-paper-raised" style={{ height }}>
          <canvas
            ref={canvasRef}
            className="block touch-none outline-offset-[-3px]"
            tabIndex={0}
            role="img"
            aria-label={`Dependency graph of ${formatCount(graph.nodes.length)} nodes and ${formatCount(graph.edges.length)} edges. Drag to pan, scroll or use plus and minus to zoom. Use the search to focus on one node.`}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={(e) => {
              pointers.current.delete(e.pointerId);
              drag.current = null;
            }}
            onPointerLeave={() => setHover(null)}
            onWheel={onWheel}
            onKeyDown={onKeyDown}
            style={{ cursor: hover ? "pointer" : "grab" }}
          />
          {!positions ? (
            <div className="absolute inset-0 grid place-items-center">
              <div className="w-56 text-center text-xs text-ink-soft">
                <p>Laying out {formatCount(graph.nodes.length)} nodes</p>
                <span className="mt-2 block h-px w-full bg-rule">
                  <span className="block h-px origin-left bg-ink transition-transform" style={{ transform: `scaleX(${progress})` }} />
                </span>
              </div>
            </div>
          ) : null}
          {hoverNode && hover ? (
            <div className="ledger pointer-events-none absolute z-10 px-2.5 py-1.5 text-xs" style={{ left: Math.min(hover.x + 14, width - 220), top: hover.y + 14 }}>
              <span className="font-medium text-ink">{hoverNode.name}</span>
              <span className="ml-2 text-ink-soft">{plural(hoverNode.dependents, "dependent")}</span>
            </div>
          ) : null}
        </div>
        <GraphLegend />
      </div>
      <aside className="lg:col-span-4" aria-live="polite">
        {sel && selected != null ? (
          <NodePanel node={sel} out={adjacency.out[selected].map((k) => edges[k])} into={adjacency.into[selected].map((k) => edges[k])} nodes={graph.nodes} onSelect={focusNode} onClose={() => setSelected(null)} />
        ) : (
          <div>
            <h2 className="text-base font-medium">Most depended on</h2>
            <p className="mt-1 text-xs text-ink-soft">Select one to see its edges and the evidence for each.</p>
            <ol className="mt-3 text-xs">
              {ranked.slice(0, 14).map(({ n, i }) => (
                <li key={n.id} className="border-t border-grid first:border-t-0">
                  <button type="button" className="flex min-h-9 w-full items-center justify-between gap-3 text-left hover:bg-hover" onClick={() => focusNode(i)}>
                    <span className="flex items-center gap-2">
                      {n.health ? <HealthGlyph health={n.health} size={12} /> : <span className="inline-block size-3 rounded-full bg-ink-soft" aria-hidden="true" />}
                      <span className="text-ink">{n.name}</span>
                    </span>
                    <span className="text-ink-soft">{formatCount(n.dependents)}</span>
                  </button>
                </li>
              ))}
            </ol>
          </div>
        )}
      </aside>
    </div>
  );
}

function GraphLegend() {
  return (
    <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-xs text-ink-soft" aria-label="Legend">
      <li className="inline-flex items-center gap-2">
        <svg width="22" height="8" aria-hidden="true">
          <line x1="0" x2="22" y1="4" y2="4" stroke="var(--ink-faint)" strokeWidth="1.5" />
        </svg>
        Census: DNS, edge or hosting
      </li>
      <li className="inline-flex items-center gap-2">
        <svg width="22" height="8" aria-hidden="true">
          <line x1="0" x2="22" y1="4" y2="4" stroke="var(--accent)" strokeWidth="1.5" />
        </svg>
        Named in incident reports
      </li>
      <li className="inline-flex items-center gap-2">
        <svg width="22" height="8" aria-hidden="true">
          <line x1="0" x2="22" y1="4" y2="4" stroke="var(--ink-soft)" strokeWidth="1.5" strokeDasharray="3 3" />
        </svg>
        Fails together
      </li>
      <li className="inline-flex items-center gap-2">
        <span className="inline-block size-3 rounded-full bg-ink-soft" aria-hidden="true" />
        Provider
      </li>
      <li className="inline-flex items-center gap-2">
        <span className="inline-block size-3 rounded-full" style={{ background: "var(--sev-operational)" }} aria-hidden="true" />
        Service, colored by health
      </li>
    </ul>
  );
}

function NodePanel({
  node,
  out,
  into,
  nodes,
  onSelect,
  onClose,
}: {
  node: GraphNode;
  out: { e: GraphEdge; t: number }[];
  into: { e: GraphEdge; s: number }[];
  nodes: GraphNode[];
  onSelect: (i: number) => void;
  onClose: () => void;
}) {
  return (
    <div>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">{node.name}</h2>
          <p className="mt-1 text-xs text-ink-soft">
            {node.kind === "provider" ? "Provider" : "Service"}, {categoryLabel(node.category)}
            {node.health ? `, ${healthLabel(node.health).toLowerCase()}` : ""}
          </p>
        </div>
        <button type="button" className="button min-h-8 px-2.5" onClick={onClose}>
          Close
        </button>
      </div>
      {node.kind === "service" ? (
        <p className="mt-2 text-xs">
          <Link href={`/s/${node.id}/`}>Open the service page</Link>
        </p>
      ) : null}
      <EdgeList title="Depended on by" items={into.map(({ e, s }) => ({ e, other: s }))} nodes={nodes} onSelect={onSelect} />
      <EdgeList title="Depends on" items={out.map(({ e, t }) => ({ e, other: t }))} nodes={nodes} onSelect={onSelect} />
    </div>
  );
}

function EdgeList({ title, items, nodes, onSelect }: { title: string; items: { e: GraphEdge; other: number }[]; nodes: GraphNode[]; onSelect: (i: number) => void }) {
  const [all, setAll] = useState(false);
  const sorted = items.slice().sort((a, b) => b.e.weight - a.e.weight);
  const shown = all ? sorted : sorted.slice(0, 12);
  return (
    <div className="mt-6">
      <h3 className="text-xs font-medium">
        {title} <span className="text-ink-soft">{formatCount(items.length)}</span>
      </h3>
      {items.length === 0 ? (
        <p className="mt-1 text-xs text-ink-soft">None.</p>
      ) : (
        <ul className="mt-2 max-h-[22rem] overflow-y-auto text-xs">
          {shown.map(({ e, other }) => (
            <li key={`${e.source}>${e.target}`} className="border-t border-grid py-2 first:border-t-0">
              <div className="flex items-baseline justify-between gap-3">
                <button type="button" className="text-left font-medium text-ink hover:underline" onClick={() => onSelect(other)}>
                  {nodes[other].name}
                </button>
                <span className="text-ink-soft">{e.kinds.map((k) => KIND_LABEL[k] ?? k).join(", ")}</span>
              </div>
              {e.evidence.length ? <p className="mt-0.5 font-mono text-[0.68rem] break-words text-ink-soft">{e.evidence.join("  ")}</p> : null}
            </li>
          ))}
        </ul>
      )}
      {items.length > 12 ? (
        <button type="button" className="mt-1 min-h-8 text-xs text-accent hover:underline" onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${formatCount(items.length)}`}
        </button>
      ) : null}
    </div>
  );
}
