"use client";

import dynamic from "next/dynamic";

// Single dynamic import of the custom Plotly build. Sharing one import point
// across every chart-using page gives Webpack a stable chunk boundary so the
// Plotly bundle is emitted once and reused.
export const Plot = dynamic(() => import("./plot-factory"), {
  ssr: false,
  loading: () => null,
});
