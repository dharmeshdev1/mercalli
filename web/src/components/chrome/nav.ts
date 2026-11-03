import { REPO_URL } from "@/lib/api/client";

export interface NavItem {
  href: string;
  label: string;
  external?: boolean;
}

export const NAV: NavItem[] = [
  { href: "/", label: "Now" },
  { href: "/services/", label: "Services" },
  { href: "/graph/", label: "Graph" },
  { href: "/census/", label: "Census" },
  { href: "/model/", label: "Model" },
  { href: "/explore/", label: "Explore" },
  { href: "/api-docs/", label: "API" },
  { href: REPO_URL, label: "GitHub", external: true },
];

export function isCurrent(pathname: string | null, href: string): boolean {
  if (!pathname) return false;
  const norm = (p: string) => (p.endsWith("/") ? p : `${p}/`);
  if (href === "/") return norm(pathname) === "/";
  if (href === "/services/") return norm(pathname).startsWith("/services/") || norm(pathname).startsWith("/s/");
  return norm(pathname).startsWith(href);
}
