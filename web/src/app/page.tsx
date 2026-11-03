import { LazyBoard } from "@/components/board/lazy-board";
import { CensusTeaser } from "@/components/now/census-teaser";
import { Hero } from "@/components/now/hero";
import { ModelTeaser } from "@/components/now/model-teaser";
import { OpenNow } from "@/components/now/open-now";
import { ReplaySection } from "@/components/replay/replay-section";

export default function NowPage() {
  return (
    <div className="shell">
      <Hero />
      <OpenNow />
      <ReplaySection />
      <ModelTeaser />
      <CensusTeaser />
      <LazyBoard />
    </div>
  );
}
