"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";

// The board sits at the bottom of the page and carries the layout animation code, so it loads
// only as the reader scrolls toward it, keeping it out of the first load.
const ServiceBoard = dynamic(() => import("./service-board").then((m) => m.ServiceBoard), {
  ssr: false,
  loading: () => <div className="section-rule h-[60vh]" aria-busy="true" />,
});

export function LazyBoard() {
  const ref = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNear(true);
          io.disconnect();
        }
      },
      { rootMargin: "1200px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return <div ref={ref}>{near ? <ServiceBoard /> : <div className="section-rule h-[60vh]" aria-busy="true" />}</div>;
}
