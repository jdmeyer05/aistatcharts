/**
 * Oil Fundamentals — Server Component shell.
 *
 * Mirrors the /home conversion pattern (see app/page.tsx): the bundle is
 * fetched server-side, in-region with Cloud Run (`preferredRegion = 'iad1'`),
 * and dehydrated into a HydrationBoundary so the client island picks up the
 * cache instantly. The backend keeps a 30-min Supabase L2 + process-local L1
 * for /api/energy/oil, and the startup pre-warm task primes both on every
 * Cloud Run revision, so the typical SSR-side fetch is single-digit ms.
 *
 * `revalidate = 1800` matches the server-side bundle TTL — cold visits within
 * the same 30-min window land on Vercel's edge cache.
 */
import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import OilClient from "@/components/oil/oil-client";
import { fetchOilBundleServer } from "@/lib/api-server";

export const revalidate = 1800;
export const preferredRegion = "iad1";

export default async function OilPage() {
  const queryClient = new QueryClient();

  const bundle = await fetchOilBundleServer();

  // Only seed the cache on a successful server fetch — a null result means
  // the upstream timed out and the client should run its own query + show
  // the existing loading state rather than render an empty page.
  if (bundle) {
    queryClient.setQueryData(["oil-bundle"], bundle);
  }

  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <OilClient />
    </HydrationBoundary>
  );
}
