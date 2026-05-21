/**
 * Home page — Server Component shell.
 *
 * Cold-start path before this conversion: page was `"use client"`, so the
 * browser had to download JS, hydrate, then issue 6 separate Cloud Run
 * round-trips for pulse / driver / heatmap / vol-landscape / events /
 * trump-monitor before any data appeared. On a cold visit that's a
 * waterfall of ~1-2s after hydration.
 *
 * Now: this RSC fans out the same 6 endpoints in parallel server-side
 * (in-region with Cloud Run via `preferredRegion = 'iad1'`), dehydrates
 * the resulting React Query cache, and ships it inside a HydrationBoundary
 * so the client island picks up the cache instantly. First paint shows
 * real numbers; client-side react-query then refetches on its existing
 * cadence.
 *
 * `revalidate = 30` makes the rendered HTML edge-cacheable on Vercel for
 * 30s with stale-while-revalidate, which matches the pulse refetch
 * cadence — cold visits in the same 30s window land on edge cache.
 */
import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import HomeClient from "@/components/home/home-client";
import { PULSE_TICKERS } from "@/lib/home-constants";
import {
  fetchSnapshotServer,
  fetchMarketDriverServer,
  fetchHeatmapServer,
  fetchVolLandscapeServer,
  fetchTrumpMonitorServer,
  fetchEventsServer,
} from "@/lib/api-server";

export const revalidate = 30;
export const preferredRegion = "iad1";

export default async function HomePage() {
  const queryClient = new QueryClient();

  const tickers = [...PULSE_TICKERS];
  const [pulse, driver, heatmap, volLandscape, trumpMonitor, events] = await Promise.all([
    fetchSnapshotServer(tickers),
    fetchMarketDriverServer(),
    fetchHeatmapServer("sectors"),
    fetchVolLandscapeServer(),
    fetchTrumpMonitorServer(),
    fetchEventsServer(),
  ]);

  // Seed the dehydrated cache only when the upstream call succeeded —
  // a null result means the client should perform its own fetch and
  // render its loading state, not lock in a bad payload.
  if (pulse) {
    queryClient.setQueryData(["pulse", tickers.join(",")], pulse);
  }
  if (driver) {
    queryClient.setQueryData(["market-driver"], driver);
  }
  if (heatmap) {
    queryClient.setQueryData(["heatmap", "sectors"], heatmap);
  }
  if (volLandscape) {
    queryClient.setQueryData(["vol-landscape-home"], volLandscape);
  }
  if (trumpMonitor) {
    queryClient.setQueryData(["trump-monitor-home"], trumpMonitor);
  }
  if (events) {
    queryClient.setQueryData(["events-home"], events);
  }

  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <HomeClient />
    </HydrationBoundary>
  );
}
