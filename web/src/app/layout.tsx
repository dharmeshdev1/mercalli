import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { OverviewProvider } from "@/components/overview-context";
import { Footer } from "@/components/chrome/footer";
import { TopBar } from "@/components/chrome/top-bar";
import { THEME_SCRIPT } from "@/lib/theme-script";
import { geologica, geologicaExt, martian } from "./fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Mercalli: live outage intelligence",
    template: "%s | Mercalli",
  },
  description:
    "Live outage intelligence from public status pages: what is broken now, how long it will likely last, and what else it may hit.",
  applicationName: "Mercalli",
  alternates: {
    types: { "application/atom+xml": `${process.env.MERCALLI_BASE_PATH ?? ""}/feeds/all.xml` },
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f3f5f4" },
    { media: "(prefers-color-scheme: dark)", color: "#0f151b" },
  ],
  colorScheme: "light dark",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${geologica.variable} ${geologicaExt.variable} ${martian.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <OverviewProvider>
          <TopBar />
          <main id="main" tabIndex={-1} className="outline-none">
            {children}
          </main>
          <Footer />
        </OverviewProvider>
      </body>
    </html>
  );
}
