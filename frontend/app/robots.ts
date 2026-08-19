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
        // /prompt-loop is the self-improvement loop's own scoreboard: admin-gated,
        // deliberately absent from NAV_GROUPS (and therefore from the sitemap), and
        // operating detail rather than product. No reason for it to be crawled.
        disallow: ["/login", "/auth/", "/api/", "/prompt-loop"],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
