import type { Metadata } from "next";
import { ServicesIndex } from "@/components/services/services-index";

export const metadata: Metadata = {
  title: "Services",
  description: "Every status page Mercalli reads, with its health now and its incidents over the last 90 days.",
};

export default function ServicesPage() {
  return (
    <div className="shell">
      <ServicesIndex />
    </div>
  );
}
