import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
