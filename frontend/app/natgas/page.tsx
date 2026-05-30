/**
 * Natural Gas Fundamentals — Server Component shell.
 *
 * Same conversion as /oil (see app/oil/page.tsx) — the bundle is fetched
 * server-side in-region with Cloud Run (`preferredRegion = 'iad1'`) and
 * dehydrated into a HydrationBoundary so the client island gets the data
 * before its useQuery would have fired. /api/energy/natgas is part of the
 * startup pre-warm task, so the typical SSR fetch hits a warm L1.
 */
import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import NatGasClient from "@/components/natgas/natgas-client";
import { fetchNatGasBundleServer } from "@/lib/api-server";

export const revalidate = 1800;
export const preferredRegion = "iad1";

export default async function NatGasPage() {
  const queryClient = new QueryClient();

  const bundle = await fetchNatGasBundleServer();

  if (bundle) {
    queryClient.setQueryData(["natgas-bundle"], bundle);
  }

  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <NatGasClient />
    </HydrationBoundary>
  );
}
