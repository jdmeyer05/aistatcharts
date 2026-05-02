"use client";

/**
 * Home — real-time market dashboard (client island).
 *
 * Layout (desktop, stacks on mobile):
 *   1. Market Pulse Strip       (30s refetch)
 *   2. What's Driving Markets   (5 min refetch, backend caches 15 min)
 *   3. Sector Relative  |  Vol Landscape Snapshot     (60s / 5min)
 *   4. News             |  Trump / Tweet Watch         (derived / 2min)
 *   5. Macro Calendar — next 14 days                   (10 min refetch)
 *
 * The page shell is a Server Component (`app/page.tsx`) which prefetches
 * all six endpoints in parallel and ships dehydrated query state via
 * HydrationBoundary. This component picks up the cache instantly on
 * hydration — no fetch waterfall on first paint — then refetches on its
 * normal cadence in the background.
 */

import Link from "next/link";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchSnapshot,
  fetchMarketDriver,
  fetchHeatmap,
  fetchEvents,
  fetchVolLandscape,
  fetchTrumpMonitor,
  type MarketDriverResponse,
  type TrumpPost,
} from "@/lib/api";

const PULSE_TICKERS = ["SPY", "QQQ", "^VIX", "TLT", "GLD", "USO", "BTC-USD", "DX-Y.NYB"];
export const HOME_PULSE_TICKERS = PULSE_TICKERS;

const PULSE_LABELS: Record<string, string> = {
  SPY: "S&P",
  QQQ: "Nasdaq",
  "^VIX": "VIX",
  TLT: "20Y",
  GLD: "Gold",
  USO: "Crude",
  "BTC-USD": "BTC",
  "DX-Y.NYB": "DXY",
};

function fmtAgo(iso: string): string {
  try {
    const ms = Date.now() - new Date(iso).getTime();
    const min = Math.floor(ms / 60000);
    if (min < 1) return "just now";
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    return `${Math.floor(hr / 24)}d ago`;
  } catch {
    return "";
  }
}

function pctClass(n: number | undefined | null): string {
  if (n == null || n === 0) return "text-text-muted";
  return n > 0 ? "text-gain" : "text-loss";
}

/* ─── Market Pulse Strip ──────────────────────────────────────── */

function MarketPulse() {
  const q = useQuery({
    queryKey: ["pulse", PULSE_TICKERS.join(",")],
    queryFn: () => fetchSnapshot(PULSE_TICKERS),
    refetchInterval: 30_000,
    staleTime: 20_000,
  });
  const data: Record<string, { price: number; change: number; prev_close?: number }> = q.data ?? {};

  return (
    <div className="card card-compact">
      <div className="flex flex-wrap gap-x-5 gap-y-2 items-center">
        {PULSE_TICKERS.map((tk) => {
          const s = data[tk] || { price: 0, change: 0 };
          const label = PULSE_LABELS[tk] ?? tk;
          return (
            <div key={tk} className="flex items-baseline gap-1.5 min-w-0">
              <span className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">{label}</span>
              <span className="text-sm font-semibold tabular-nums">
                {s.price ? s.price.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}
              </span>
              <span className={`text-xs tabular-nums ${pctClass(s.change)}`}>
                {s.change == null ? "" : `${s.change > 0 ? "+" : ""}${s.change.toFixed(2)}%`}
              </span>
            </div>
          );
        })}
        <div className="ml-auto text-[0.6rem] text-text-muted">
          {q.isFetching ? "updating…" : q.dataUpdatedAt ? `as of ${new Date(q.dataUpdatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : ""}
        </div>
      </div>
    </div>
  );
}

/* ─── What's Driving Markets ──────────────────────────────────── */

function DriverPill({ label, source }: { label: string; source: string }) {
  const sourceColors: Record<string, string> = {
    news: "bg-accent/15 text-accent",
    quotes: "bg-gain/15 text-gain",
    vol: "bg-spot/15 text-spot",
    cftc: "bg-loss/15 text-loss",
    polymarket: "bg-violet-500/15 text-violet-400",
    release: "bg-amber-500/15 text-amber-400",
  };
  const cls = sourceColors[source] ?? "bg-surface-alt text-text-muted";
  return (
    <span className={`text-[0.6rem] font-semibold px-2 py-0.5 rounded ${cls}`} title={source}>
      {label}
    </span>
  );
}

function MarketDriverCard() {
  const q = useQuery({
    queryKey: ["market-driver"],
    queryFn: fetchMarketDriver,
    refetchInterval: 5 * 60_000,
    staleTime: 3 * 60_000,
  });
  const d = q.data;
  const asOf = d?.as_of_utc ? fmtAgo(d.as_of_utc) : "";

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-bold uppercase tracking-wider text-accent">What&apos;s Driving Markets</h2>
            {d?.regime_label && (
              <span className="text-[0.65rem] font-bold uppercase px-2 py-0.5 rounded bg-accent/15 text-accent tracking-wider">
                {d.regime_label}
              </span>
            )}
          </div>
          <div className="text-[0.6rem] text-text-muted mt-0.5">
            {d?.model ? `${d.model}${d.escalated ? " (escalated)" : ""}` : ""}
            {asOf ? `  ·  ${asOf}` : ""}
            {d?.cache_hit ? "  ·  cached" : ""}
            {d?.confidence != null ? `  ·  conf ${d.confidence}/10` : ""}
          </div>
        </div>
        <button
          onClick={() => q.refetch()}
          disabled={q.isFetching}
          className="text-[0.65rem] px-2 py-1 rounded border border-border hover:bg-surface-alt disabled:opacity-50"
          title="Recompute the driver synthesis"
        >
          {q.isFetching ? "…" : "Refresh"}
        </button>
      </div>

      {q.isLoading && (
        <div className="py-6 text-center">
          <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-xs text-text-muted mt-2">Reading the tape…</p>
        </div>
      )}

      {q.isError && !d && (
        <p className="text-xs text-loss">
          Driver synthesis unavailable: {(q.error as Error)?.message ?? "unknown error"}.
        </p>
      )}

      {d && (
        <>
          <div className="space-y-2.5 text-sm leading-relaxed text-text">
            {d.paragraphs?.what_happened && <p>{d.paragraphs.what_happened}</p>}
            {d.paragraphs?.whats_driving && <p>{d.paragraphs.whats_driving}</p>}
            {d.paragraphs?.what_to_watch && <p>{d.paragraphs.what_to_watch}</p>}
          </div>
          {d.citations && d.citations.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-1 border-t border-border">
              {d.citations.map((c, i) => (
                <DriverPill key={i} label={c.label} source={c.source} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ─── Sector Relative ─────────────────────────────────────────── */

function SectorRelative() {
  const q = useQuery({
    queryKey: ["heatmap", "sectors"],
    queryFn: () => fetchHeatmap("sectors"),
    refetchInterval: 60_000,
    staleTime: 45_000,
  });
  // Fall back inside useMemo so an undefined `q.data` doesn't churn the
  // `[]` reference every render and re-trigger the sort.
  const sorted = useMemo(
    () => [...(q.data?.items ?? [])].sort((a, b) => b.change - a.change),
    [q.data?.items]
  );
  const maxAbs = Math.max(0.5, ...sorted.map((s) => Math.abs(s.change || 0)));

  return (
    <div className="card card-compact space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-accent">Sector Relative</h3>
        <Link href="/sector-analysis" className="text-[0.6rem] text-text-muted hover:text-accent">Full →</Link>
      </div>
      {q.isLoading && <div className="text-xs text-text-muted">Loading…</div>}
      <div className="space-y-1">
        {sorted.map((s) => {
          const pct = s.change || 0;
          const width = Math.abs(pct) / maxAbs * 50;
          const isUp = pct >= 0;
          return (
            <div key={s.symbol} className="flex items-center gap-2 text-xs tabular-nums">
              <div className="w-16 truncate text-text-muted" title={s.label}>{s.label}</div>
              <div className="flex-1 flex h-4 items-center relative">
                <div className="absolute left-1/2 top-0 bottom-0 w-px bg-border" />
                {isUp ? (
                  <div
                    className="absolute left-1/2 top-0.5 bottom-0.5 bg-gain/70 rounded-r"
                    style={{ width: `${width}%` }}
                  />
                ) : (
                  <div
                    className="absolute right-1/2 top-0.5 bottom-0.5 bg-loss/70 rounded-l"
                    style={{ width: `${width}%` }}
                  />
                )}
              </div>
              <div className={`w-14 text-right ${pctClass(pct)}`}>
                {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Vol Landscape Snapshot ──────────────────────────────────── */

function VolLandscapeSnapshot() {
  const q = useQuery({
    queryKey: ["vol-landscape-home"],
    queryFn: fetchVolLandscape,
    refetchInterval: 5 * 60_000,
    staleTime: 4 * 60_000,
  });
  const d = q.data;
  const topRows = useMemo(() => {
    const rows = (d as { rows?: unknown[]; items?: unknown[]; top_dislocations?: unknown[] } | undefined);
    const candidate = rows?.top_dislocations ?? rows?.rows ?? rows?.items ?? [];
    return (candidate as Array<Record<string, unknown>>).slice(0, 5);
  }, [d]);

  return (
    <div className="card card-compact space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-accent">Vol Landscape</h3>
        <Link href="/vol-landscape" className="text-[0.6rem] text-text-muted hover:text-accent">Full →</Link>
      </div>
      {q.isLoading && <div className="text-xs text-text-muted">Loading…</div>}
      {!q.isLoading && topRows.length === 0 && (
        <div className="text-xs text-text-muted">No dislocations surfaced right now.</div>
      )}
      <div className="space-y-1.5 text-xs">
        {topRows.map((r, i) => {
          const ticker = (r.ticker ?? r.symbol ?? r.underlying ?? "?") as string;
          const label = (r.label ?? r.signal ?? r.note ?? "") as string;
          const iv = r.iv ?? r.implied_vol ?? r.implied ?? null;
          const rv = r.rv ?? r.realized_vol ?? r.realized ?? null;
          return (
            <div key={i} className="flex items-center gap-2 justify-between">
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-bold tabular-nums">{ticker}</span>
                <span className="text-text-muted truncate">{label}</span>
              </div>
              <div className="tabular-nums text-text-muted shrink-0">
                {iv != null && `IV ${Number(iv).toFixed(1)}`}
                {rv != null && ` · RV ${Number(rv).toFixed(1)}`}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─── News (derived from driver citations) ─────────────────────── */

function NewsPanel({ citations }: { citations: MarketDriverResponse["citations"] | undefined }) {
  const newsItems = useMemo(
    () => (citations ?? []).filter((c) => c.source === "news" || c.source === "release").slice(0, 6),
    [citations]
  );
  return (
    <div className="card card-compact space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-accent">Market-Moving News</h3>
      </div>
      {newsItems.length === 0 ? (
        <p className="text-xs text-text-muted">No market-moving headlines surfaced in this cycle.</p>
      ) : (
        <ul className="space-y-1.5 text-sm">
          {newsItems.map((c, i) => (
            <li key={i} className="leading-snug">
              <span className="text-text">{c.label}</span>
              {c.detail && <span className="text-text-muted text-xs ml-1">— {c.detail}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ─── Tweet Watch (Trump for now; Fed/Treasury RSS later) ──────── */

function TweetWatch() {
  const q = useQuery({
    queryKey: ["trump-monitor-home"],
    queryFn: fetchTrumpMonitor,
    refetchInterval: 2 * 60_000,
    staleTime: 90_000,
  });
  const posts: TrumpPost[] = q.data?.posts ?? [];
  const latest = posts[0];

  const sentimentColor = (s: string) => {
    const v = (s || "").toLowerCase();
    if (v.includes("bull")) return "text-gain";
    if (v.includes("bear")) return "text-loss";
    return "text-text-muted";
  };

  return (
    <div className="card card-compact space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-accent">Tweet Watch</h3>
        <Link href="/trump-decoder" className="text-[0.6rem] text-text-muted hover:text-accent">Decoder →</Link>
      </div>
      {q.isLoading && <div className="text-xs text-text-muted">Loading…</div>}
      {q.isError && !latest && <p className="text-xs text-loss">Tweet fetch failed.</p>}
      {!q.isLoading && !latest && <p className="text-xs text-text-muted">No recent posts.</p>}
      {latest && (
        <>
          <div className="text-[0.6rem] text-text-muted flex items-center gap-2">
            <span className="font-semibold">@realDonaldTrump</span>
            <span>·</span>
            <span>{fmtAgo(latest.timestamp)}</span>
            <span className={`ml-auto ${sentimentColor(latest.sentiment)}`}>{latest.sentiment}</span>
          </div>
          <p className="text-sm text-text leading-snug line-clamp-4">{latest.text}</p>
          {latest.interpretation && (
            <p className="text-xs text-text-muted leading-snug border-t border-border pt-1.5">
              {latest.interpretation}
            </p>
          )}
        </>
      )}
      {q.data?.market_alert && (
        <div className="text-[0.65rem] font-semibold text-loss border-l-2 border-loss pl-2 mt-1">
          {q.data.market_alert}
        </div>
      )}
    </div>
  );
}

/* ─── Macro Calendar ──────────────────────────────────────────── */

function MacroCalendar() {
  const q = useQuery({
    queryKey: ["events-home"],
    queryFn: fetchEvents,
    refetchInterval: 10 * 60_000,
    staleTime: 9 * 60_000,
  });
  const events = (q.data?.events ?? []).slice(0, 6);

  return (
    <div className="card card-compact space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-accent">Next 2 Weeks — Macro Calendar</h3>
        <Link href="/economic-calendar" className="text-[0.6rem] text-text-muted hover:text-accent">Full →</Link>
      </div>
      {events.length === 0 ? (
        <p className="text-xs text-text-muted">No scheduled events in window.</p>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
          {events.map((ev, i) => (
            <div key={i} className="flex flex-col text-xs">
              <span className="text-text-muted text-[0.6rem] tabular-nums">
                {ev.date} · {ev.days_away === 0 ? "today" : `+${ev.days_away}d`}
              </span>
              <span className="font-semibold text-text truncate" title={ev.name}>{ev.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Page ────────────────────────────────────────────────────── */

export default function HomeClient() {
  const driverQ = useQuery({
    queryKey: ["market-driver"],
    queryFn: fetchMarketDriver,
    refetchInterval: 5 * 60_000,
    staleTime: 3 * 60_000,
  });

  return (
    <div className="space-y-4">
      <MarketPulse />
      <MarketDriverCard />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SectorRelative />
        <VolLandscapeSnapshot />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <NewsPanel citations={driverQ.data?.citations} />
        <TweetWatch />
      </div>
      <MacroCalendar />
    </div>
  );
}
