import Link from "next/link";
import { Mark } from "@/components/chrome/mark";
import { REPO_URL, siteUrl } from "@/lib/api/client";

export function Footer() {
  return (
    <footer className="mt-8 border-t border-rule">
      <div className="shell grid grid-cols-1 gap-x-6 gap-y-10 py-12 sm:grid-cols-2 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <p className="inline-flex items-center gap-2 font-semibold">
            <Mark size={20} />
            <span className="display">Mercalli</span>
          </p>
          <p className="prose-measure mt-3 text-xs text-ink-soft">
            Outage intelligence from public status pages: what is broken now, how long it is likely to last, and what
            else it may hit. Open source under the Apache 2.0 license.
          </p>
        </div>
        <div className="lg:col-span-3">
          <p className="text-xs font-medium">Data</p>
          <ul className="mt-3 space-y-2 text-xs">
            <li>
              <Link href="/api-docs/">API documentation</Link>
            </li>
            <li>
              <a href={siteUrl("feeds/all.xml")}>Atom feed of all incidents</a>
            </li>
            <li>
              <Link href="/explore/">Query the incident history</Link>
            </li>
          </ul>
        </div>
        <div className="lg:col-span-4">
          <p className="text-xs font-medium">Project</p>
          <ul className="mt-3 space-y-2 text-xs">
            <li>
              <a href={REPO_URL}>Source on GitHub</a>
            </li>
            <li>
              <a href={`${REPO_URL}/tree/main/docs`}>Self-hosting documentation</a>
            </li>
            <li>
              <a href={`${REPO_URL}/blob/main/LICENSE`}>License: Apache 2.0</a>
            </li>
          </ul>
        </div>
      </div>
    </footer>
  );
}
