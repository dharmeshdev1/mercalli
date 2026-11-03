import type { Metadata } from "next";
import { GraphExplorer } from "@/components/graph/graph-explorer";

export const metadata: Metadata = {
  title: "Dependency graph",
  description: "Which services depend on which providers, found in the census, in incident reports and in shared failures.",
};

export default function GraphPage() {
  return (
    <div className="shell">
      <GraphExplorer />
    </div>
  );
}
