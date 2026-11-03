import type { Metadata } from "next";
import { Suspense } from "react";
import { IncidentView } from "@/components/incident/incident-view";

export const metadata: Metadata = {
  title: "Incident",
  description: "One incident: every vendor update, every forecast made while it was open, the blast radius and similar past incidents.",
};

export default function IncidentPage() {
  return (
    <div className="shell">
      <Suspense fallback={<div className="h-[60vh]" aria-busy="true" />}>
        <IncidentView />
      </Suspense>
    </div>
  );
}
