/**
 * Server-side fetch helpers for use in React Server Components.
 *
 * Mirrors the public, unauthenticated subset of `lib/api.ts` but:
 *  - never touches `window` or the Supabase browser client
 *  - never throws (returns null on failure) so a single slow upstream
 *    can't 500 the page — the client island will fall back to its own
 *    fetch and render a skeleton instead
 *  - pinned to the same shapes as the client helpers so query-cache
 *    hydration via HydrationBoundary lines up byte-for-byte
 *
 * Used from `app/page.tsx` to prefetch the home dashboard in parallel
 * during SSR/ISR regeneration. Region for this page is pinned to iad1
 * (us-east-1) so the SSR-side fetch is in-region with the FastAPI
 * Cloud Run service.
 */
import type {
  Snapshot,
  HeatmapItem,
  CalendarEvent,
  MarketDriverResponse,
  VolLandscapeScan,
  TrumpMonitorResponse,
  OilBundle,
  NatGasBundle,
  CtaFlowBoard,
  MacroPressureBoard,
  SectorRrg,
  SpValuation,
  EsBrief,
  EsGateTrackRecord,
  FedProbabilities,
  TsmomBook,
} from "@/lib/api";
import { normalizeOilBundle } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function serverFetch<T>(path: string, timeoutMs = 8_000): Promise<T | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      next: { revalidate: 30 },
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export function fetchSnapshotServer(tickers: string[]) {
  return serverFetch<Snapshot>(`/api/market/snapshot?tickers=${tickers.join(",")}`);
}

export function fetchHeatmapServer(group = "sectors") {
  return serverFetch<{ group: string; items: HeatmapItem[] }>(
    `/api/market/heatmap?group=${group}`
  );
}

export function fetchEventsServer() {
  return serverFetch<{ events: CalendarEvent[] }>("/api/market/events");
}

export function fetchMarketDriverServer() {
  return serverFetch<MarketDriverResponse>("/api/market/market-driver", 12_000);
}

export function fetchVolLandscapeServer() {
  return serverFetch<VolLandscapeScan>("/api/options/vol-landscape", 12_000);
}

export function fetchTrumpMonitorServer() {
  return serverFetch<TrumpMonitorResponse>("/api/trump/monitor", 10_000);
}

export async function fetchOilBundleServer(): Promise<OilBundle | null> {
  // Bundle is ~165KB and the cold path fans out 16 EIA fetches. Give it room
  // — the backend keeps a 30-min Supabase L2 cache + a process-local L1, so
  // the typical SSR path is single-digit ms once warm. Timeout matches the
  // realistic worst case (cold-instance + EIA upstream blip).
  // Normalize so a stale-shape backend response can't poison the dehydrated
  // cache (the client otherwise crashes on `data.spr.length`).
  const raw = await serverFetch<Partial<OilBundle>>("/api/energy/oil", 15_000);
  return raw ? normalizeOilBundle(raw) : null;
}

export function fetchNatGasBundleServer() {
  // Same L1/L2 cache layer as /oil; same 8-EIA cold fan-out shape.
  return serverFetch<NatGasBundle>("/api/energy/natgas", 15_000);
}

export function fetchMacroPressureServer() {
  // ~4s cold across 14 FRED/yfinance series, but the API pre-warms it at
  // startup and holds it 45 min, so the SSR path is normally a dict hit.
  return serverFetch<MacroPressureBoard>("/api/market/macro-pressure", 15_000);
}

export function fetchSpValuationServer() {
  return serverFetch<SpValuation>("/api/market/sp-valuation", 15_000);
}

export function fetchEsBriefServer() {
  // Session clock, levels, CTA and macro in one bundled call, cached 3 min
  // server-side. The longer timeout covers a cold intraday-bar fetch; every
  // block inside is independently optional, so a partial payload still renders.
  return serverFetch<EsBrief>("/api/market/es-brief", 20_000);
}

export function fetchSectorRrgServer(tailWeeks = 8) {
  // ~4s cold across 12 yfinance series; cached 45 min server-side.
  //
  // DEFAULT IS 8, NOT 4 — a bug fix rather than a preference. The RRG card asks
  // for an 8-week tail and caches under ["sector-rrg", 8]. This defaulted to 4
  // and `app/page.tsx` seeded ["sector-rrg", 4], so the one board on this page
  // whose cold fetch is measured in seconds was the one board the server-side
  // prefetch never served: the card waterfalled a client fetch on every visit
  // while the prefetched payload sat under a key nothing read. The startup
  // pre-warm was warming 4 as well.
  return serverFetch<SectorRrg>(`/api/sectors/rrg?tail_weeks=${tailWeeks}`, 15_000);
}

export function fetchFedProbabilitiesServer(nMeetings = 4) {
  // Daily ZQ settlements behind a 30-min server cache. It was the only home
  // card left out of the prefetch, so it alone paid a client round-trip before
  // showing a number.
  return serverFetch<FedProbabilities>(
    `/api/market/fed-probabilities?n_meetings=${nMeetings}`, 15_000);
}

export function fetchEsGateTrackRecordServer() {
  // Cheap while it is still refusing — it reads the snapshot log and returns a
  // count. Prefetched so the "N of 30 sessions" line is in the first paint
  // rather than appearing a round-trip later: a countdown that shows up late is
  // a countdown the reader scrolls past.
  return serverFetch<EsGateTrackRecord>("/api/market/es-track-record-gate", 15_000);
}

export function fetchTsmomBookServer() {
  // 32 yfinance fetches cold, behind a 12h cache and a startup pre-warm, so in
  // practice this is a cache read. The rule rebalances monthly; nothing in it
  // moves faster than daily.
  return serverFetch<TsmomBook>("/api/market/tsmom-book", 20_000);
}

export function fetchCtaFlowsServer(code = "13874A") {
  // Bundled paths + pivots + terminal flows in one call. Price history is
  // cached 4h server-side and the path walk is ~0.1s, so this is cheap once
  // warm; the longer timeout covers a cold yfinance fetch.
  return serverFetch<CtaFlowBoard>(`/api/cftc/cta-flows?code=${code}`, 15_000);
}

export function fetchErcotBundleServer() {
  // Live grid bundle — 4 ERCOT dashboard fetches behind the same L1/L2 cache
  // (5-min TTL). The startup pre-warm primes it, so the SSR path is usually a
  // single-digit-ms L1 read. Untyped passthrough to match the client's
  // fetchErcotBundle (Record<string, any>); the page guards before seeding.
  return serverFetch<Record<string, unknown>>("/api/energy/ercot-bundle", 15_000);
}
