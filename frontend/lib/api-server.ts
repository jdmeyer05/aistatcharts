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
} from "@/lib/api";

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

export function fetchOilBundleServer() {
  // Bundle is ~165KB and the cold path fans out 10 EIA fetches. Give it room
  // — the backend keeps a 30-min Supabase L2 cache + a process-local L1, so
  // the typical SSR path is single-digit ms once warm. Timeout matches the
  // realistic worst case (cold-instance + EIA upstream blip).
  return serverFetch<OilBundle>("/api/energy/oil", 15_000);
}

export function fetchNatGasBundleServer() {
  // Same L1/L2 cache layer as /oil; same 8-EIA cold fan-out shape.
  return serverFetch<NatGasBundle>("/api/energy/natgas", 15_000);
}
