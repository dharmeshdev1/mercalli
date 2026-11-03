/// <reference lib="webworker" />
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from "d3-force";

/**
 * Runs the force layout off the main thread. Receives node radii and edges, posts progress, then
 * posts the final positions as one transferable Float32Array (x0, y0, x1, y1, ...).
 */

interface InNode extends SimulationNodeDatum {
  index: number;
  r: number;
}

interface Message {
  radii: number[];
  edges: [number, number, number][];
  ticks: number;
}

const ctx = self as unknown as DedicatedWorkerGlobalScope;

ctx.onmessage = (e: MessageEvent<Message>) => {
  const { radii, edges, ticks } = e.data;
  const nodes: InNode[] = radii.map((r, index) => ({ index, r }));
  const links = edges.map(([source, target, weight]) => ({ source, target, weight }));
  const sim = forceSimulation(nodes)
    .force(
      "link",
      forceLink<InNode, { source: number; target: number; weight: number }>(links)
        .id((d) => d.index)
        .distance((l) => 26 + ((l.target as unknown as InNode).r ?? 0) * 1.6)
        .strength((l) => 0.12 + 0.25 * l.weight),
    )
    .force("charge", forceManyBody<InNode>().strength((d) => -10 - d.r * 2.2).theta(0.95).distanceMax(520))
    .force("collide", forceCollide<InNode>((d) => d.r + 1.6).iterations(1))
    .force("x", forceX(0).strength(0.035))
    .force("y", forceY(0).strength(0.035))
    .alphaDecay(1 - Math.pow(0.001, 1 / ticks))
    .stop();
  for (let i = 0; i < ticks; i++) {
    sim.tick();
    if (i % 20 === 0) ctx.postMessage({ type: "progress", value: i / ticks });
  }
  const out = new Float32Array(nodes.length * 2);
  nodes.forEach((n, i) => {
    out[i * 2] = n.x ?? 0;
    out[i * 2 + 1] = n.y ?? 0;
  });
  ctx.postMessage({ type: "done", positions: out }, [out.buffer]);
};
