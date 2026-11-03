import type { Metadata } from "next";
import { ServiceView } from "@/components/service/service-view";
import { buildTimeServices } from "@/lib/build-data";
import { categoryLabel } from "@/lib/severity";

export const dynamicParams = false;

export function generateStaticParams() {
  return buildTimeServices().map((s) => ({ id: s.id }));
}

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  const s = buildTimeServices().find((x) => x.id === id);
  if (!s) return { title: id };
  return {
    title: s.name,
    description: `${s.name} (${categoryLabel(s.category)}): live status, incidents over 90 days, forecasts and dependencies, read from its own status page.`,
  };
}

export default async function ServicePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const s = buildTimeServices().find((x) => x.id === id);
  return (
    <div className="shell">
      <ServiceView id={id} name={s?.name ?? id} category={s?.category ?? null} statusUrl={s?.status_url ?? null} />
    </div>
  );
}
