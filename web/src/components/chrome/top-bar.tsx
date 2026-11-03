"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useId, useState } from "react";
import { FreshnessClock } from "@/components/chrome/freshness";
import { IntensityBadge } from "@/components/chrome/intensity-badge";
import { Mark } from "@/components/chrome/mark";
import { isCurrent, NAV } from "@/components/chrome/nav";
import { ThemeToggle } from "@/components/chrome/theme-toggle";
import { ZoneToggle } from "@/components/chrome/zone-toggle";

function NavLink({ href, label, external, pathname, onNavigate }: { href: string; label: string; external?: boolean; pathname: string | null; onNavigate?: () => void }) {
  const current = !external && isCurrent(pathname, href);
  const cls = `relative inline-flex min-h-10 items-center text-[0.95rem] no-underline transition-colors ${
    current ? "text-ink" : "text-ink-soft hover:text-ink"
  }`;
  if (external) {
    return (
      <a href={href} className={cls} rel="noreferrer">
        {label}
      </a>
    );
  }
  return (
    <Link href={href} className={cls} aria-current={current ? "page" : undefined} onClick={onNavigate}>
      {label}
      {current ? <span aria-hidden="true" className="absolute inset-x-0 bottom-1.5 h-px bg-ink" /> : null}
    </Link>
  );
}

function MobileMenu({ pathname }: { pathname: string | null }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <div className="lg:hidden">
      <button
        type="button"
        className="button min-h-9 px-3"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
          {open ? (
            <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          ) : (
            <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          )}
        </svg>
        Menu
      </button>
      <div id={panelId} hidden={!open} className="absolute inset-x-0 top-full z-40 border-b border-rule bg-paper">
        <div className="shell pt-2 pb-6">
          <nav aria-label="Main">
            <ul className="grid grid-cols-2 gap-x-6 border-b border-grid pb-4 sm:grid-cols-4">
              {NAV.map((item) => (
                <li key={item.href}>
                  <NavLink {...item} pathname={pathname} onNavigate={() => setOpen(false)} />
                </li>
              ))}
            </ul>
          </nav>
          <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3">
            <FreshnessClock />
            <ThemeToggle withLabels />
            <ZoneToggle />
          </div>
        </div>
      </div>
    </div>
  );
}

export function TopBar() {
  const pathname = usePathname();
  return (
    <header className="relative z-30 border-b border-rule">
      <div className="shell flex h-15 items-center gap-6 lg:gap-8">
        <Link href="/" className="quiet-link inline-flex items-center gap-2 text-ink" aria-label="Mercalli, home">
          <Mark size={22} />
          <span className="display text-lg font-semibold" style={{ letterSpacing: "-0.02em" }}>
            Mercalli
          </span>
        </Link>
        <nav aria-label="Main" className="hidden lg:block">
          <ul className="flex items-center gap-5 xl:gap-6">
            {NAV.map((item) => (
              <li key={item.href}>
                <NavLink {...item} pathname={pathname} />
              </li>
            ))}
          </ul>
        </nav>
        <div className="ml-auto flex items-center gap-4 xl:gap-5">
          <span className="hidden sm:inline-flex">
            <IntensityBadge />
          </span>
          <span className="sm:hidden">
            <IntensityBadge compact />
          </span>
          <span className="hidden xl:inline-flex">
            <FreshnessClock />
          </span>
          <span className="hidden lg:inline-flex">
            <ThemeToggle />
          </span>
          <MobileMenu key={pathname} pathname={pathname} />
        </div>
      </div>
    </header>
  );
}
