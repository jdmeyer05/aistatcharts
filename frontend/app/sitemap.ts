import type { MetadataRoute } from "next";
import { ALL_PAGES } from "@/lib/nav";

const SITE_URL = "https://www.aistatcharts.com";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  // Every live page registered in nav.ts. Drill-down paths with query
  // params (per-ticker views etc.) are intentionally not listed — they
  // depend on user input and aren't canonical destinations.
  const pages = ALL_PAGES.filter((p) => p.status === "live").map((p) => ({
    url: `${SITE_URL}${p.href}`,
    lastModified: now,
    changeFrequency: "daily" as const,
    priority: p.href === "/" ? 1.0 : 0.7,
  }));

  return pages;
}
