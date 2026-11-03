"use client";

import type { ReactNode } from "react";
import { useTheme, type Theme } from "@/lib/prefs";

const OPTIONS: { value: Theme; label: string; icon: ReactNode }[] = [
  {
    value: "system",
    label: "Match system",
    icon: (
      <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
        <rect x="1.75" y="2.75" width="12.5" height="8.5" rx="1" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M5.5 13.75h5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    value: "light",
    label: "Light",
    icon: (
      <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
        <circle cx="8" cy="8" r="2.9" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path
          d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    value: "dark",
    label: "Dark",
    icon: (
      <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
        <path d="M13.2 9.7A5.6 5.6 0 0 1 6.3 2.8a5.6 5.6 0 1 0 6.9 6.9z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      </svg>
    ),
  },
];

/** System, light or dark, remembered across visits. */
export function ThemeToggle({ withLabels = false }: { withLabels?: boolean }) {
  const [theme, setTheme] = useTheme();
  return (
    <div role="group" aria-label="Color theme" className="inline-flex rounded-sm border border-rule p-0.5">
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={theme === o.value}
          aria-label={withLabels ? undefined : o.label}
          title={o.label}
          onClick={() => setTheme(o.value)}
          className={`inline-flex min-h-8 items-center gap-1.5 rounded-xs px-2 text-xs transition-colors ${
            theme === o.value ? "bg-ink text-paper" : "text-ink-soft hover:bg-hover hover:text-ink"
          }`}
        >
          {o.icon}
          {withLabels ? <span>{o.label}</span> : null}
        </button>
      ))}
    </div>
  );
}
