/**
 * Home page — Server Component shell, in two streamed halves.
 *
 * Cold-start path before the RSC conversion: the page was `"use client"`, so
 * the browser had to download JS, hydrate, then issue separate Cloud Run
 * round-trips for every board before any data appeared — a waterfall of ~1-2s
 * after hydration on a cold visit.
 *
 * The conversion fixed the waterfall but introduced a subtler version of the
 * same problem: ELEVEN endpoints were awaited in one `Promise.all`, each with
 * its own 8-20s timeout, and nothing rendered until the slowest returned. The
 * price at the top of the page was gated on the S&P valuation scrape.
 *
 * So it is now two boundaries:
 *
 *   FAST — the pulse strip and the ES briefing, the only two blocks a session
 *   actually turns on, plus the gate's track record: a cheap read whose
 *   REFUSING state ("13 of 30 sessions") is the thing worth painting straight
 *   away. Both heavy ones are pre-warmed on the API instance. Awaited inline,
 *   so they paint first.
 *
 *   SWING — the other ten, behind `<Suspense>`. They stream in underneath.
 *   One slow upstream now delays one half of a page instead of all of it.
 *
 * Each half dehydrates its own QueryClient; React Query merges nested
 * HydrationBoundaries into the one client-side cache, so the split is invisible
 * to the cards.
 *
 * `revalidate = 30` makes the rendered HTML edge-cacheable on Vercel for 30s
 * with stale-while-revalidate, matching the pulse refetch cadence — cold visits
 * inside the same 30s window land on edge cache.
 */
import { Suspense } from "react";
import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import { HomeFast, HomeSwing } from "@/components/home/home-client";
import { PULSE_TICKERS } from "@/lib/home-constants";
import {
  fetchSnapshotServer,
  fetchMarketDriverServer,
  fetchHeatmapServer,
  fetchVolLandscapeServer,
  fetchEventsServer,
  fetchCtaFlowsServer,
  fetchMacroPressureServer,
  fetchSectorRrgServer,
  fetchSpValuationServer,
  fetchEsBriefServer,
  fetchEsGateTrackRecordServer,
  fetchFedProbabilitiesServer,
  fetchTsmomBookServer,
} from "@/lib/api-server";

export const revalidate = 30;
export const preferredRegion = "iad1";

/** The tail of the RRG the card asks for. Declared once, here, because it is a
 *  cache KEY as well as a query parameter — the card reads
 *  ["sector-rrg", RRG_TAIL_WEEKS] and this file has to seed the same key. It
 *  did not, for a while: the server fetched and seeded 4 while the card read 8,
 *  so the single slowest board on the page was the one board whose prefetch was
 *  never used. */
const RRG_TAIL_WEEKS = 8;

async function FastSection() {
  const queryClient = new QueryClient();
  const tickers = [...PULSE_TICKERS];

  const [pulse, esBrief, gateRecord] = await Promise.all([
    fetchSnapshotServer(tickers),
    fetchEsBriefServer(),
    fetchEsGateTrackRecordServer(),
  ]);

  if (pulse) {
    queryClient.setQueryData(["pulse", tickers.join(",")], pulse);
  }
  // Seeding an unavailable briefing would pin the card to its error state for
  // the whole staleTime rather than letting the client retry.
  if (esBrief?.available) {
    queryClient.setQueryData(["es-brief"], esBrief);
  }
  // Seeded whether or not it is `available` — unlike every other block here,
  // the UNAVAILABLE payload is the thing worth rendering. It carries "logging
  // since X, N of 30 sessions", and that countdown belongs in the first paint.
  if (gateRecord) {
    queryClient.setQueryData(["es-track-record-gate"], gateRecord);
  }

  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <HomeFast />
    </HydrationBoundary>
  );
}

async function SwingSection() {
  const queryClient = new QueryClient();

  // TWEET WATCH IS DELIBERATELY NOT PREFETCHED.
  //
  // `/api/trump/monitor` measures ~13s (it is a scrape plus a model call behind
  // a 2-minute cache, so it misses far more often than it hits) while its
  // server-side timeout is 10s. It therefore times out essentially every time —
  // and because this is one `Promise.all`, every other board on the page sat
  // waiting 10 seconds for a fetch that was never going to land. The rendered
  // page timed at 10.06s, which is that timeout and almost nothing else.
  //
  // Raising the timeout would be worse: it would make ten boards wait 13s for
  // the least important card on the page. Dropping it from the prefetch lets
  // the client fetch it on mount exactly as it always has — the card renders
  // its own loading state for a moment — and the other ten paint immediately.
  const [
    driver, heatmap, volLandscape, events, ctaFlows,
    macroPressure, sectorRrg, spValuation, fedProbabilities, tsmomBook,
  ] = await Promise.all([
    fetchMarketDriverServer(),
    fetchHeatmapServer("sectors"),
    fetchVolLandscapeServer(),
    fetchEventsServer(),
    fetchCtaFlowsServer(),
    fetchMacroPressureServer(),
    fetchSectorRrgServer(RRG_TAIL_WEEKS),
    fetchSpValuationServer(),
    fetchFedProbabilitiesServer(4),
    fetchTsmomBookServer(),
  ]);

  // Seed the dehydrated cache only when the upstream call succeeded — a null
  // result means the client should perform its own fetch and render its loading
  // state, not lock in a bad payload.
  if (driver) {
    queryClient.setQueryData(["market-driver"], driver);
  }
  if (heatmap) {
    queryClient.setQueryData(["heatmap", "sectors"], heatmap);
  }
  if (volLandscape) {
    queryClient.setQueryData(["vol-landscape-home"], volLandscape);
  }
  if (events) {
    queryClient.setQueryData(["events-home"], events);
  }
  // Seed only a usable board — an `available: false` payload (unmapped
  // contract, thin history) would otherwise lock the card into its error
  // state for the whole staleTime instead of letting the client retry.
  if (ctaFlows?.available) {
    queryClient.setQueryData(["cta-flows", "13874A"], ctaFlows);
  }
  if (macroPressure?.available) {
    queryClient.setQueryData(["macro-pressure"], macroPressure);
  }
  if (sectorRrg?.available) {
    queryClient.setQueryData(["sector-rrg", RRG_TAIL_WEEKS], sectorRrg);
  }
  if (spValuation?.available) {
    queryClient.setQueryData(["sp-valuation"], spValuation);
  }
  if (fedProbabilities?.available) {
    queryClient.setQueryData(["fed-probabilities", 4], fedProbabilities);
  }
  if (tsmomBook?.available) {
    queryClient.setQueryData(["tsmom-book"], tsmomBook);
  }

  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <HomeSwing />
    </HydrationBoundary>
  );
}

/** Reserved height while the swing half streams, so the fast half does not jump
 *  when the boards arrive under it. */
function SwingSkeleton() {
  return (
    <div className="space-y-4" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="card card-compact">
          <div className="h-3 w-40 bg-surface-alt rounded animate-pulse" />
          <div className="mt-3 h-24 bg-surface-alt/60 rounded animate-pulse" />
        </div>
      ))}
      <p className="text-[0.6rem] text-text-muted text-center">
        Loading the swing-horizon boards…
      </p>
    </div>
  );
}

export default function HomePage() {
  return (
    <div className="space-y-5">
      <FastSection />
      <Suspense fallback={<SwingSkeleton />}>
        <SwingSection />
      </Suspense>
    </div>
  );
}
