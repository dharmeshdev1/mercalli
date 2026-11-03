import type { ReactNode } from "react";

/**
 * A page section with a marginal head: the heading and a one-line statement of what the numbers
 * mean sit in the first four columns on desktop, the content in the remaining eight.
 */
export function Section({
  id,
  title,
  lead,
  aside,
  children,
  wide = false,
  compact = false,
  className = "",
}: {
  id?: string;
  title: ReactNode;
  lead?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  wide?: boolean;
  /** Less space below, for a section that only says its data is not published yet. */
  compact?: boolean;
  className?: string;
}) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section id={id} aria-labelledby={headingId} className={`section-rule pt-10 md:pt-12 ${compact ? "pb-12 md:pb-14" : "pb-20 md:pb-24"} ${className}`}>
      <div className="grid grid-cols-1 gap-x-6 gap-y-6 lg:grid-cols-12">
        <div className={wide ? "lg:col-span-12" : "lg:col-span-4 lg:pr-6"}>
          <h2 id={headingId} className="text-xl font-semibold md:text-2xl">
            {title}
          </h2>
          {lead ? <div className="prose-measure mt-3 text-ink-soft">{lead}</div> : null}
          {aside ? <div className="mt-4 text-xs text-ink-soft">{aside}</div> : null}
        </div>
        <div className={wide ? "lg:col-span-12" : "min-w-0 lg:col-span-8"}>{children}</div>
      </div>
    </section>
  );
}

/** A calm statement for a document that is not published yet or failed to load. */
export function Pending({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-sm border border-dashed border-ink-faint/70 px-5 py-4 md:px-6">
      <p className="font-medium">{title}</p>
      {children ? <div className="prose-measure mt-2 text-ink-soft">{children}</div> : null}
    </div>
  );
}

/** A small marker shown in development when a section is fed by a fixture. */
export function FixtureNote({ show }: { show: boolean }) {
  if (process.env.NODE_ENV !== "development" || !show) return null;
  return (
    <p className="mb-3 inline-flex items-center gap-2 rounded-xs border border-dashed border-ink-faint px-2 py-1 text-xs text-ink-soft">
      Development fixture: this artifact is not published yet
    </p>
  );
}
