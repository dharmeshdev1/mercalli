import type { ReactNode } from "react";

/** The head of an inner page: title, one sentence on what it shows, and a line of provenance. */
export function PageHeader({ title, lead, meta, children }: { title: ReactNode; lead?: ReactNode; meta?: ReactNode; children?: ReactNode }) {
  return (
    <header className="pt-10 pb-10 md:pt-14 md:pb-12">
      <h1 className="display text-3xl font-normal md:text-4xl">{title}</h1>
      {lead ? <div className="prose-measure mt-4 text-lg text-ink-soft">{lead}</div> : null}
      {meta ? <div className="mt-3 text-xs text-ink-soft">{meta}</div> : null}
      {children}
    </header>
  );
}
