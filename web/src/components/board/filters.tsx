"use client";

import { useId } from "react";
import { categoryLabel } from "@/lib/severity";
import { formatCount } from "@/lib/format";

export interface FilterState {
  query: string;
  category: string | null;
}

export function matches(s: { id: string; name: string; category: string }, f: FilterState): boolean {
  if (f.category && s.category !== f.category) return false;
  const q = f.query.trim().toLowerCase();
  if (!q) return true;
  return s.name.toLowerCase().includes(q) || s.id.toLowerCase().includes(q);
}

/** Category chips and a search field, in one row above everything they filter. */
export function BoardFilters({
  items,
  state,
  onChange,
  shown,
}: {
  items: { category: string }[];
  state: FilterState;
  onChange: (next: FilterState) => void;
  shown: number;
}) {
  const searchId = useId();
  const counts = new Map<string, number>();
  for (const s of items) counts.set(s.category, (counts.get(s.category) ?? 0) + 1);
  const cats = [...counts.entries()].sort((a, b) => b[1] - a[1] || categoryLabel(a[0]).localeCompare(categoryLabel(b[0])));
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor={searchId} className="sr-only">
          Search services
        </label>
        <input
          id={searchId}
          type="search"
          className="field w-full sm:w-72"
          placeholder="Search services"
          value={state.query}
          onChange={(e) => onChange({ ...state, query: e.target.value })}
          autoComplete="off"
          spellCheck={false}
        />
        <p className="text-xs text-ink-soft" aria-live="polite">
          {shown === items.length ? `${formatCount(items.length)} services` : `${formatCount(shown)} of ${formatCount(items.length)} services`}
        </p>
      </div>
      <div
        role="group"
        aria-label="Filter by category"
        className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:mx-0 sm:flex-wrap sm:overflow-visible sm:px-0 sm:pb-0 [&>button]:flex-none"
      >
        <button type="button" className="chip" aria-pressed={state.category == null} onClick={() => onChange({ ...state, category: null })}>
          All <span>{formatCount(items.length)}</span>
        </button>
        {cats.map(([cat, n]) => (
          <button
            key={cat}
            type="button"
            className="chip"
            aria-pressed={state.category === cat}
            onClick={() => onChange({ ...state, category: state.category === cat ? null : cat })}
          >
            {categoryLabel(cat)} <span>{n}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
