/**
 * ERCOT Power Dashboard — Server Component shell.
 *
 * Mirrors the /oil + /natgas conversion (commit d1a1ecf): the live grid bundle
 * is fetched server-side, in-region with Cloud Run (`preferredRegion = 'iad1'`),
 * and dehydrated into a HydrationBoundary so the client island has data before
 * its useQuery would have fired. The backend keeps a 5-min Supabase L2 +
 * process-local L1 for /api/energy/ercot-bundle, and the startup pre-warm primes
 * it, so the typical SSR-side fetch is single-digit ms.
 *
 * `revalidate = 300` matches the 5-min server-side bundle TTL — this is live
 * data, so a short window keeps the SSR-cached shell from drifting. The client
 * useQuery (staleTime 2 min) refetches on mount for sub-cycle freshness.
 */
import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import ErcotPowerClient from "@/components/ercot/ercot-power-client";
import { fetchErcotBundleServer } from "@/lib/api-server";

export const revalidate = 300;
export const preferredRegion = "iad1";

export default async function ErcotPowerPage() {
  const queryClient = new QueryClient();

  const bundle = await fetchErcotBundleServer();

  // Only seed on a complete bundle. A null (upstream timeout) or a partial
  // bundle — fuel_mix / supply_demand missing because ERCOT's dashboard
  // blipped — would otherwise hydrate the client into its error state (the
  // parse returns null when those keys are absent) and stick there until the
  // 2-min staleTime elapses. Leaving the cache empty lets the client run its
  // own query and show the loading spinner instead.
  if (bundle && bundle.fuel_mix && bundle.supply_demand) {
    queryClient.setQueryData(["ercot-bundle"], bundle);
  }

  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <ErcotPowerClient />
    </HydrationBoundary>
  );
}
