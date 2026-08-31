import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static generation budget per page, raised from the 60s default.
  //
  // WHAT ACTUALLY HAPPENS, because the obvious explanation is wrong. `/` and
  // `/natgas` have both failed a local build with "took more than 60 seconds",
  // always while the production API was cold, so the natural reading was that a
  // slow upstream was hanging the prerender. It is not: every server-side fetch
  // goes through `serverFetch`, which caps itself with an AbortController and
  // returns null rather than throwing, and a build pointed at a server that
  // NEVER RESPONDS completes all 60 pages in 20.3s. The timeouts work, and a
  // page whose data is missing renders its skeleton and lets the client refetch.
  //
  // So the budget is being spent somewhere other than waiting on the API —
  // fifteen parallel workers rendering React trees on a machine that is also
  // running something else, most likely. That is not worth engineering around,
  // but it should not fail a deploy either: 60s is simply a thin allowance for
  // the largest page in the app. Three minutes costs nothing on a healthy build
  // and turns a hard failure into a slow one.
  staticPageGenerationTimeout: 180,
  poweredByHeader: false,
  productionBrowserSourceMaps: false,
  compress: true,
  experimental: {
    // Only list packages that are actually imported somewhere in app/. Listing
    // unused packages is a no-op but obscures intent. @react-three/*, zod,
    // zustand, and @tanstack/react-table are installed but not imported yet.
    optimizePackageImports: [
      "@tanstack/react-query",
      "lightweight-charts",
      "react-markdown",
    ],
  },
};

export default nextConfig;
