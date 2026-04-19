import type { MetadataRoute } from "next";

const SITE_URL = "https://www.aistatcharts.com";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        // Keep crawlers off auth + private API surfaces. They'd 307 to /login
        // anyway but this prevents wasted crawl budget and noisy 307s in GSC.
        disallow: ["/login", "/auth/", "/api/"],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
