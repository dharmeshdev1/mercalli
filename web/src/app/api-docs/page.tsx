import type { Metadata } from "next";
import { ApiDocs } from "@/components/api-docs/api-docs";

export const metadata: Metadata = {
  title: "API",
  description: "Mercalli's JSON API: documents, fields, and live examples. No key needed.",
};

export default function ApiDocsPage() {
  return (
    <div className="shell">
      <ApiDocs />
    </div>
  );
}
