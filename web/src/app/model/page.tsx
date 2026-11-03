import type { Metadata } from "next";
import { ModelView } from "@/components/model/model-view";

export const metadata: Metadata = {
  title: "Model card",
  description: "How the recovery forecaster and the blast radius model were trained and how accurate they are, and how intensity is measured.",
};

export default function ModelPage() {
  return (
    <div className="shell">
      <ModelView />
    </div>
  );
}
