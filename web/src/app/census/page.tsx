import type { Metadata } from "next";
import { CensusView } from "@/components/census/census-view";

export const metadata: Metadata = {
  title: "Census",
  description: "Which DNS, edge network, cloud, region, certificate authority and mail provider the most visited websites rely on.",
};

export default function CensusPage() {
  return (
    <div className="shell">
      <CensusView />
    </div>
  );
}
