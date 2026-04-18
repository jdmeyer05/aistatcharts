"use client";

import { useQuery } from "@tanstack/react-query";
import {
  fetchSnapshot, fetchMarketNews, fetchSignalSummary, fetchTopIdeas,
  fetchHeatmap, fetchEvents, fetchRisk,
  fetchAccuracySummary, fetchTickerMetrics,
  fetchVolLandscape, fetchPolymarket, fetchPolymarketHistory,
  type HeatmapItem, type VolLandscapeMetric,
  type PolymarketEvent, type PolymarketHistoryPoint,
} from "@/lib/api";
import { FreshnessBar } from "@/components/ui/freshness-dot";
import ReactMarkdown from "react-markdown";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";

/* ═══════════════════════════════════════════════════════════════
   TICKER TAPE — full-width, no card border, horizontal scroll
   ═══════════════════════════════════════════════════════════════ */

const EQ_TICKERS = [
  { s: "SPY", l: "S&P 500" }, { s: "QQQ", l: "Nasdaq" }, { s: "IWM", l: "Russell" },
  { s: "^VIX", l: "VIX" }, { s: "GLD", l: "Gold" }, { s: "USO", l: "Crude" },
  { s: "TLT", l: "Bonds" }, { s: "DX-Y.NYB", l: "Dollar" },
];
const FUT_TICKERS = [
  { s: "ES=F", l: "ES" }, { s: "NQ=F", l: "NQ" }, { s: "YM=F", l: "Dow" },
  { s: "CL=F", l: "Crude" }, { s: "GC=F", l: "Gold" }, { s: "SI=F", l: "Silver" },
  { s: "NG=F", l: "NatGas" }, { s: "ZB=F", l: "30Y" }, { s: "ZN=F", l: "10Y" },
  { s: "6E=F", l: "Euro" }, { s: "BTC-USD", l: "BTC" },
];

function TickerTape() {
  const { data: eq } = useQuery({ queryKey: ["pulse-eq"], queryFn: () => fetchSnapshot(EQ_TICKERS.map(t => t.s)), refetchInterval: 120_000 });
  const { data: fut, dataUpdatedAt } = useQuery({ queryKey: ["pulse-fut"], queryFn: () => fetchSnapshot(FUT_TICKERS.map(t => t.s)), refetchInterval: 120_000 });
  const ageMin = dataUpdatedAt ? (Date.now() - dataUpdatedAt) / 60000 : null;

  const all = [...EQ_TICKERS.map(t => ({ ...t, snap: eq?.[t.s] })), ...FUT_TICKERS.map(t => ({ ...t, snap: fut?.[t.s] }))];

  return (
    <div className="-mx-4 sm:-mx-6 lg:-mx-8 px-4 sm:px-6 lg:px-8 py-2 border-b border-border bg-surface/50 backdrop-blur-sm">
      <div className="flex gap-5 overflow-x-auto pb-1 scrollbar-none">
        {all.map(({ s, l, snap }) => {
          if (!snap?.price) return (
            <div key={s} className="shrink-0 flex items-center gap-1.5 opacity-40">
              <span className="text-[0.6rem] text-text-muted">{l}</span>
              <span className="text-xs font-data">—</span>
            </div>
          );
          const chg = snap.change ?? 0;
          const up = chg >= 0;
          const fmt = ["^VIX", "DX-Y.NYB"].includes(s)
            ? snap.price.toFixed(2)
            : snap.price >= 1000 ? `${(snap.price / 1000).toFixed(1)}k` : snap.price >= 100 ? snap.price.toFixed(0) : snap.price.toFixed(2);
          return (
            <div key={s} className="shrink-0 flex items-center gap-1.5">
              <span className="text-[0.6rem] text-text-muted">{l}</span>
              <span className="text-xs font-bold font-data">{fmt}</span>
              <span className={`text-[0.6rem] font-data font-semibold ${up ? "text-gain" : "text-loss"}`}>{up ? "+" : ""}{chg.toFixed(2)}%</span>
            </div>
          );
        })}
      </div>
      <FreshnessBar sources={[{ label: "Prices", ageMinutes: ageMin, greenThreshold: 5, yellowThreshold: 15 }]} />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   RISK STRIP — horizontal metrics bar, no card borders
   ═══════════════════════════════════════════════════════════════ */

const REGIME_COLORS: Record<string, string> = {
  "Stagflation": "text-loss", "Recession": "text-warn", "Soft Landing": "text-gain",
  "Financial Crisis": "text-loss", "Re-Acceleration": "text-accent", "Goldilocks": "text-info",
};

function RiskStrip() {
  const { data } = useQuery({ queryKey: ["risk"], queryFn: fetchRisk, staleTime: 5 * 60 * 1000 });
  const { data: metrics } = useQuery({ queryKey: ["spy-metrics"], queryFn: () => fetchTickerMetrics("SPY"), staleTime: 10 * 60 * 1000 });
  const p = metrics?.percentiles ?? {};

  return (
    <div className="flex flex-wrap items-center gap-x-8 gap-y-2 py-3 border-b border-border">
      {data?.macro && (
        <div className="flex items-center gap-2">
          <span className="text-[0.55rem] text-text-muted uppercase tracking-wider">Regime</span>
          <span className={`text-sm font-bold ${REGIME_COLORS[data.macro.top_regime] ?? "text-text"}`}>{data.macro.top_regime}</span>
          <span className="text-[0.55rem] text-text-muted">{data.macro.top_prob}%</span>
        </div>
      )}
      {data?.vol && (
        <div className="flex items-center gap-2">
          <span className="text-[0.55rem] text-text-muted uppercase tracking-wider">IV</span>
          <span className={`text-sm font-bold font-data ${data.vol.level === "High" ? "text-loss" : data.vol.level === "Low" ? "text-gain" : ""}`}>
            {data.vol.atm_iv}%
          </span>
          {data.vol.vrp !== null && <span className="text-[0.55rem] text-text-muted">VRP {data.vol.vrp}%</span>}
          {p.atm_iv != null && <span className="text-[0.55rem] text-text-muted">{(Number(p.atm_iv) * 100).toFixed(0)}th</span>}
        </div>
      )}
      {data?.strategy && (
        <div className="flex items-center gap-2">
          <span className="text-[0.55rem] text-text-muted uppercase tracking-wider">Play</span>
          <span className="text-sm font-bold text-accent">{data.strategy.rec}</span>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   VOL SNAPSHOT — cross-asset options lens (NEW)
   ═══════════════════════════════════════════════════════════════ */

// Fixed display list: what a trader wants to check first. If vol-landscape
// doesn't cover all of these, we show what it returns.
const VOL_WATCH = ["SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "USO", "HYG", "EEM", "FXI", "XLE", "SMH"];

function ivRankColor(pct: number | null): string {
  if (pct == null) return "bg-surface-alt text-text-muted";
  if (pct >= 80) return "bg-loss/70 text-white";
  if (pct >= 60) return "bg-warn/40 text-warn";
  if (pct >= 40) return "bg-surface-alt text-text";
  if (pct >= 20) return "bg-gain/20 text-gain";
  return "bg-gain/50 text-white";
}

// Risk reversal = call IV - put IV. Negative = puts bid (skew-for-fear).
// Magnitude tells you how one-sided the tail is.
function skewBadge(rr: number): { tone: string; label: string } {
  if (rr <= -2) return { tone: "text-loss", label: "puts bid" };
  if (rr <= -0.5) return { tone: "text-warn", label: "put skew" };
  if (rr >= 2) return { tone: "text-gain", label: "calls bid" };
  if (rr >= 0.5) return { tone: "text-info", label: "call skew" };
  return { tone: "text-text-muted", label: "flat" };
}

function VolTile({ m }: { m: VolLandscapeMetric }) {
  const ivPct = m.IV_Pctile != null ? Math.round(m.IV_Pctile * 100) : null;
  const skew = skewBadge(m.Risk_Rev);
  const ivhv = m.IV_HV;
  return (
    <Link
      href={`/options-analysis?ticker=${m.Ticker}`}
      className="block rounded-lg border border-border bg-surface hover:border-accent/40 transition-colors p-3"
    >
      <div className="flex items-center justify-between mb-1">
        <div className="flex flex-col">
          <span className="text-sm font-bold">{m.Ticker}</span>
          <span className="text-[0.55rem] text-text-muted leading-tight">{m.Label}</span>
        </div>
        <span className={`text-[0.55rem] px-1.5 py-0.5 rounded font-data font-semibold ${ivRankColor(ivPct)}`}>
          {ivPct != null ? `${ivPct}r` : "—"}
        </span>
      </div>
      <div className="flex items-baseline gap-1 mt-1.5">
        <span className="text-lg font-bold font-data">{(m.Front_IV * 100).toFixed(0)}</span>
        <span className="text-[0.55rem] text-text-muted">IV</span>
        {ivhv != null && (
          <span className={`ml-auto text-[0.6rem] font-data ${ivhv > 1.2 ? "text-loss" : ivhv < 0.9 ? "text-gain" : "text-text-muted"}`}>
            {ivhv.toFixed(2)}× HV
          </span>
        )}
      </div>
      <div className="flex items-center justify-between mt-1 text-[0.6rem]">
        <span className={`font-data font-semibold ${skew.tone}`}>
          RR {m.Risk_Rev >= 0 ? "+" : ""}{m.Risk_Rev.toFixed(1)}
        </span>
        <span className="text-text-muted">{skew.label}</span>
      </div>
    </Link>
  );
}

function VolSnapshot() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["vol-landscape-home"],
    queryFn: fetchVolLandscape,
    staleTime: 10 * 60_000,
  });

  const byTicker = new Map((data?.metrics ?? []).map(m => [m.Ticker, m]));
  const rows = VOL_WATCH.map(t => byTicker.get(t)).filter((m): m is VolLandscapeMetric => !!m);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-bold uppercase tracking-wider">Cross-Asset Vol</h2>
          <p className="text-[0.6rem] text-text-muted mt-0.5">
            IV · IV rank · skew (call-put RR) · IV/HV — what the options market is pricing
          </p>
        </div>
        <Link href="/vol-landscape" className="text-[0.65rem] text-accent hover:underline shrink-0">
          Full landscape →
        </Link>
      </div>
      {isLoading ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-2">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="h-24 rounded-lg bg-surface-alt animate-pulse" />
          ))}
        </div>
      ) : error || rows.length === 0 ? (
        <div className="text-xs text-text-muted py-4">Vol landscape unavailable right now.</div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-2">
          {rows.map(m => <VolTile key={m.Ticker} m={m} />)}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   SIGNALS / TOP IDEAS
   ═══════════════════════════════════════════════════════════════ */

function SignalSpotlight() {
  const { data: summary } = useQuery({ queryKey: ["signal-summary"], queryFn: fetchSignalSummary, refetchInterval: 60_000 });
  const { data: ideas } = useQuery({ queryKey: ["top-ideas"], queryFn: () => fetchTopIdeas(7), refetchInterval: 60_000 });

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold uppercase tracking-wider">Top Ideas</h2>
        {summary && summary.n_tickers > 0 && (
          <div className="flex gap-3 text-xs font-data">
            <span className="text-gain">{summary.n_bullish} bull</span>
            <span className="text-loss">{summary.n_bearish} bear</span>
          </div>
        )}
      </div>
      {ideas && ideas.length > 0 ? (
        <div className="space-y-1">
          {ideas.map(t => (
            <Link
              key={t.ticker}
              href={`/stock-analysis?ticker=${t.ticker}`}
              className="flex items-center justify-between py-1.5 -mx-2 px-2 rounded hover:bg-surface-alt transition-colors"
            >
              <div className="flex items-center gap-2">
                <div className={`w-1 h-6 rounded-full ${t.overall_direction === "bull" ? "bg-gain" : t.overall_direction === "bear" ? "bg-loss" : "bg-text-muted"}`} />
                <span className="font-semibold text-sm">{t.ticker}</span>
              </div>
              <div className="flex items-center gap-3">
                <div className="w-20 h-1 bg-surface-alt rounded-full overflow-hidden">
                  <div className={`h-full rounded-full ${t.overall_direction === "bull" ? "bg-gain" : "bg-loss"}`} style={{ width: `${t.overall_conviction * 100}%` }} />
                </div>
                <span className="text-text-muted text-xs font-data w-8 text-right">{(t.overall_conviction * 100).toFixed(0)}%</span>
              </div>
            </Link>
          ))}
        </div>
      ) : <p className="text-sm text-text-muted">No signals yet.</p>}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   EVENTS
   ═══════════════════════════════════════════════════════════════ */

function UpcomingEvents() {
  const { data } = useQuery({ queryKey: ["events"], queryFn: fetchEvents, staleTime: 3600_000 });

  return (
    <div className="space-y-2">
      <h2 className="text-sm font-bold uppercase tracking-wider">Events</h2>
      {data?.events && data.events.length > 0 ? (
        <div className="space-y-0">
          {data.events.slice(0, 6).map((ev, i) => (
            <div key={i} className="flex justify-between items-center py-1.5 border-b border-border/50 last:border-0">
              <span className="text-xs">{ev.name}</span>
              <span className={`text-[0.6rem] font-data font-semibold ${ev.days_away === 0 ? "text-loss" : ev.days_away <= 2 ? "text-warn" : "text-text-muted"}`}>
                {ev.days_away === 0 ? "TODAY" : `${ev.days_away}d`}
              </span>
            </div>
          ))}
        </div>
      ) : <p className="text-xs text-text-muted">No upcoming events.</p>}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   HEATMAP
   ═══════════════════════════════════════════════════════════════ */

const HEATMAP_GROUPS = [
  { key: "sectors", label: "Sectors" }, { key: "indices", label: "Indices" },
  { key: "fixed_income", label: "Bonds" }, { key: "commodities", label: "Commodities" },
  { key: "mega_caps", label: "Mega Caps" },
];

function heatColor(c: number): string {
  if (c >= 2) return "bg-gain text-white"; if (c >= 0.8) return "bg-gain/70 text-white";
  if (c >= 0.2) return "bg-gain/20 text-gain"; if (c > -0.2) return "bg-surface-alt text-text-muted";
  if (c > -0.8) return "bg-loss/20 text-loss"; if (c > -2) return "bg-loss/70 text-white";
  return "bg-loss text-white";
}

function MarketHeatmap() {
  const [group, setGroup] = useState("sectors");
  const { data } = useQuery({ queryKey: ["heatmap", group], queryFn: () => fetchHeatmap(group), staleTime: 300_000 });

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold uppercase tracking-wider">Market</h2>
        <div className="flex gap-0.5">
          {HEATMAP_GROUPS.map(g => (
            <button key={g.key} onClick={() => setGroup(g.key)}
              className={`px-2.5 py-1 text-[0.6rem] rounded-full transition-colors ${group === g.key ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
              {g.label}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-11 gap-1">
        {data?.items?.map((item: HeatmapItem) => (
          <div key={item.symbol} className={`rounded-lg p-2.5 text-center ${heatColor(item.change)}`}>
            <div className="text-[0.65rem] font-bold">{item.symbol}</div>
            <div className="text-[0.5rem] opacity-70 leading-tight">{item.label}</div>
            <div className="text-xs font-bold font-data mt-1">{item.change >= 0 ? "+" : ""}{item.change.toFixed(2)}%</div>
          </div>
        )) ?? Array.from({ length: 11 }).map((_, i) => <div key={i} className="h-16 bg-surface-alt rounded-lg animate-pulse" />)}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   INTELLIGENCE (news)
   ═══════════════════════════════════════════════════════════════ */

function MarketNews() {
  const { data } = useQuery({ queryKey: ["market-news"], queryFn: fetchMarketNews, staleTime: 1800_000 });

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold uppercase tracking-wider">Intelligence</h2>
        {data?.age_hours === 0 && <span className="text-[0.55rem] font-data dot-fresh">Live</span>}
      </div>
      {data?.content ? (
        <div className="text-[0.78rem] leading-relaxed text-text-secondary markdown-body">
          <ReactMarkdown>{data.content}</ReactMarkdown>
        </div>
      ) : <p className="text-xs text-text-muted">Updates hourly during market hours.</p>}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   POLYMARKET PULSE — prediction markets (carried over from market-scan)
   ═══════════════════════════════════════════════════════════════ */

function Sparkline({ points, width = 160, height = 48 }: { points: PolymarketHistoryPoint[]; width?: number; height?: number }) {
  if (points.length < 2) return null;
  const prices = points.map(p => p.p);
  const min = Math.min(...prices), max = Math.max(...prices), range = max - min || 1;
  const pad = 4, textH = 12, w = width - pad * 2, h = height - pad - textH;
  const d = points.map((pt, i) => {
    const x = pad + (i / (points.length - 1)) * w;
    const y = pad + h - ((pt.p - min) / range) * h;
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const up = prices[prices.length - 1] >= prices[0];
  const c = up ? "#22c55e" : "#ef4444";
  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <path d={d} fill="none" stroke={c} strokeWidth="1.5" strokeLinejoin="round" />
      <text x={pad} y={height - 1} fill="#888" fontSize="9" fontFamily="monospace">{prices[0].toFixed(0)}%</text>
      <text x={width - pad} y={height - 1} fill={c} fontSize="9" fontFamily="monospace" textAnchor="end">{prices[prices.length - 1].toFixed(0)}%</text>
    </svg>
  );
}

function PolyPill({ ev }: { ev: PolymarketEvent }) {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<PolymarketHistoryPoint[] | null>(null);
  const fetchedRef = useRef(false);
  const ref = useRef<HTMLDivElement>(null);
  const top = ev.outcomes[0];

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent | TouchEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("touchstart", handler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("touchstart", handler);
    };
  }, [open]);

  if (!top) return null;
  const shortTitle = ev.title.replace(/\?$/, "").replace(/^(Will |What |Who will |US )/, "");

  const reveal = async () => {
    setOpen(true);
    if (!fetchedRef.current && top.token_id) {
      fetchedRef.current = true;
      try { const res = await fetchPolymarketHistory(top.token_id); if (res.success) setHistory(res.points); } catch {}
    }
  };

  return (
    <div ref={ref} className="relative" onMouseEnter={reveal} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        onClick={() => (open ? setOpen(false) : reveal())}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-border bg-surface-alt text-[0.6rem] font-data hover:border-accent/50 transition-colors cursor-pointer"
      >
        <span className="text-text-muted">{shortTitle.slice(0, 35)}</span>
        <span className={`font-bold ${top.yes_pct >= 80 ? "text-gain" : top.yes_pct >= 40 ? "text-warn" : "text-text"}`}>
          {top.label.slice(0, 20)}: {top.yes_pct}%
        </span>
      </button>
      {open && (
        <div className="absolute z-50 top-full left-0 mt-1 p-2 rounded-lg border border-border bg-surface shadow-lg max-w-[calc(100vw-2rem)]" style={{ minWidth: 220 }}>
          <div className="text-[0.6rem] font-semibold text-text mb-1">{ev.title}</div>
          {ev.outcomes.slice(0, 4).map((o, j) => (
            <div key={j} className="flex justify-between text-[0.55rem] font-data">
              <span className="text-text-muted">{o.label}</span>
              <span className={o.yes_pct >= 80 ? "text-gain font-bold" : o.yes_pct >= 40 ? "text-warn" : "text-text-muted"}>{o.yes_pct}%</span>
            </div>
          ))}
          <div className="text-[0.5rem] text-text-muted mt-1">${(ev.volume_24h / 1000).toFixed(0)}k vol/24h · ${(ev.liquidity / 1000).toFixed(0)}k liq</div>
          {history && history.length > 2 && (
            <div className="mt-1.5 border-t border-border pt-1.5">
              <div className="text-[0.5rem] text-text-muted mb-0.5">30-day trend</div>
              <Sparkline points={history} />
            </div>
          )}
          {history === null && top.token_id && <div className="mt-1.5 text-[0.5rem] text-text-muted animate-pulse">Loading chart...</div>}
        </div>
      )}
    </div>
  );
}

function PolymarketPulse() {
  const { data } = useQuery({ queryKey: ["polymarket"], queryFn: fetchPolymarket, staleTime: 5 * 60_000 });
  const markets = (data?.markets ?? []).slice(0, 12);
  if (markets.length === 0) return null;

  return (
    <div className="space-y-2">
      <h2 className="text-sm font-bold uppercase tracking-wider">Prediction Markets</h2>
      <div className="flex flex-wrap gap-1.5">
        {markets.map((ev, i) => <PolyPill key={i} ev={ev} />)}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   TRACK RECORD
   ═══════════════════════════════════════════════════════════════ */

function TrackRecord() {
  const { data } = useQuery({ queryKey: ["accuracy-summary"], queryFn: fetchAccuracySummary, staleTime: 600_000 });
  if (!data || data.evaluated === 0) return null;
  const sources = Object.entries(data.by_source).sort(([, a], [, b]) => b.accuracy - a.accuracy);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold uppercase tracking-wider">Track Record</h2>
        <Link href="/track-record" className="text-[0.6rem] text-accent hover:underline">Details →</Link>
      </div>
      <div className="flex items-baseline gap-3">
        <span className={`text-2xl font-bold font-data ${data.accuracy > 0.55 ? "text-gain" : data.accuracy < 0.45 ? "text-loss" : ""}`}>
          {(data.accuracy * 100).toFixed(0)}%
        </span>
        <span className="text-xs text-text-muted">{data.correct}/{data.evaluated} correct</span>
      </div>
      {sources.length > 0 && (
        <div className="space-y-1">
          {sources.slice(0, 5).map(([src, v]) => (
            <div key={src} className="flex items-center gap-2">
              <span className="text-[0.6rem] text-text-muted w-24 truncate">{src.replace(/_/g, " ")}</span>
              <div className="flex-1 h-1 bg-surface-alt rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${v.accuracy > 0.55 ? "bg-gain" : v.accuracy < 0.45 ? "bg-loss" : "bg-accent"}`}
                  style={{ width: `${v.accuracy * 100}%` }} />
              </div>
              <span className="text-[0.6rem] font-data font-semibold w-8 text-right">{(v.accuracy * 100).toFixed(0)}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   QUICK NAV
   ═══════════════════════════════════════════════════════════════ */

const TOOLS = [
  { l: "Stock Analysis", h: "/stock-analysis" }, { l: "Vol Landscape", h: "/vol-landscape" },
  { l: "Options Intelligence", h: "/options-analysis" }, { l: "Signal Scanner", h: "/signal-scanner" },
  { l: "Fed & Macro", h: "/fed-macro" }, { l: "Backtester", h: "/algo-backtester" },
  { l: "Optimizer", h: "/portfolio-optimizer" }, { l: "Correlation", h: "/correlation" },
  { l: "Calendar", h: "/economic-calendar" }, { l: "Smart Money", h: "/smart-money" },
  { l: "Track Record", h: "/track-record" },
];

function QuickNav() {
  return (
    <div className="pt-2 border-t border-border">
      <div className="flex flex-wrap gap-1.5">
        {TOOLS.map(t => (
          <Link key={t.l} href={t.h}
            className="px-3 py-1.5 text-[0.65rem] font-semibold rounded-full border border-border
                       text-text-muted hover:text-accent hover:border-accent/40 transition-colors">
            {t.l}
          </Link>
        ))}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   DASHBOARD — unified homebase
   Order: quick pulse → options lens → performance → narrative →
          predictions → your plays → events → trust.
   ═══════════════════════════════════════════════════════════════ */

export function DashboardContent() {
  return (
    <div className="space-y-6">
      {/* Pulse: prices + regime/vol/play (fast-twitch signal) */}
      <TickerTape />
      <RiskStrip />

      {/* Cross-asset vol snapshot — what the options market is pricing */}
      <VolSnapshot />

      {/* Sector/asset performance heatmap */}
      <MarketHeatmap />

      {/* Intelligence + Prediction markets — market narrative */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-8 card p-5">
          <MarketNews />
        </div>
        <div className="lg:col-span-4 card p-5">
          <PolymarketPulse />
        </div>
      </div>

      {/* Your plays + upcoming events */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-7 card p-5">
          <SignalSpotlight />
        </div>
        <div className="lg:col-span-5 card p-5">
          <UpcomingEvents />
        </div>
      </div>

      {/* Trust signal */}
      <div className="card p-5">
        <TrackRecord />
      </div>

      {/* Tool pills */}
      <QuickNav />
    </div>
  );
}
