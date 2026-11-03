import type { Metadata } from "next";
import { ExploreConsole } from "@/components/explore/explore-console";

export const metadata: Metadata = {
  title: "Explore",
  description: "Query the full incident history with SQL, in your browser, with DuckDB.",
};

export default function ExplorePage() {
  return (
    <div className="shell">
      <ExploreConsole />
    </div>
  );
}
