"use client";

import { useQuery } from "@tanstack/react-query";
import {
  fetchSnapshot, fetchMarketNews, fetchSignalSummary, fetchTopIdeas,
  fetchPortfolioSummary, fetchHeatmap, fetchEvents, fetchRisk,
  type HeatmapItem,
} from "@/lib/api";
import { Metric } from "@/components/ui/metric";
import { FreshnessBar } from "@/components/ui/freshness-dot";
import ReactMarkdown from "react-markdown";
import { useState } from "react";
import Link from "next/link";

// ─── MARKET PULSE ────────────────────────────────────────────

const PULSE_TICKERS = [
  { symbol: "SPY", label: "S&P 500" },
  { symbol: "QQQ", label: "Nasdaq" },
  { symbol: "IWM", label: "Russell" },
  { symbol: "^VIX", label: "VIX" },
  { symbol: "GLD", label: "Gold" },
  { symbol: "USO", label: "Crude" },
  { symbol: "TLT", label: "20Y Bond" },
  { symbol: "DX-Y.NYB", label: "Dollar" },
];

const FUTURES_TICKERS = [
  { symbol: "ES=F", label: "ES" },
  { symbol: "NQ=F", label: "NQ" },
  { symbol: "YM=F", label: "Dow" },
  { symbol: "CL=F", label: "Crude" },
  { symbol: "GC=F", label: "Gold" },
  { symbol: "SI=F", label: "Silver" },
  { symbol: "NG=F", label: "NatGas" },
  { symbol: "BTC-USD", label: "Bitcoin" },
];

function PulseBar({ tickers, label }: { tickers: typeof PULSE_TICKERS; label: string }) {
  const { data } = useQuery({
    queryKey: ["pulse", label],
    queryFn: () => fetchSnapshot(tickers.map((t) => t.symbol)),
    refetchInterval: 2 * 60 * 1000,
  });

  return (
    <div className="card card-compact">
      <div className="flex items-center gap-2">
        <span className="metric-label text-[0.55rem] hidden sm:block [writing-mode:vertical-lr] rotate-180">{label}</span>
        <div className="flex flex-wrap gap-1 flex-1">
          {tickers.map(({ symbol, label: tickerLabel }) => {
            const snap = data?.[symbol];
            if (!snap?.price) {
              return (
                <div key={symbol} className="flex-1 min-w-[80px] text-center py-1.5">
                  <div className="text-[0.6rem] text-text-muted">{tickerLabel}</div>
                  <div className="h-5 bg-surface-alt rounded animate-pulse mt-0.5 mx-3" />
                </div>
              );
            }
            const chg = snap.change ?? 0;
            const isUp = chg >= 0;
            const priceStr =
              ["^VIX", "DX-Y.NYB"].includes(symbol)
                ? snap.price.toFixed(2)
                : snap.price >= 1000
                  ? `$${(snap.price / 1000).toFixed(1)}k`
                  : snap.price >= 100
                    ? `$${snap.price.toFixed(0)}`
                    : `$${snap.price.toFixed(2)}`;

            return (
              <div key={symbol} className="flex-1 min-w-[80px] text-center py-1.5">
                <div className="text-[0.6rem] text-text-muted">{tickerLabel}</div>
                <div className="text-sm font-semibold font-data">{priceStr}</div>
                <div className={`text-[0.65rem] font-semibold font-data ${isUp ? "text-gain" : "text-loss"}`}>
                  {isUp ? "▲" : "▼"}{Math.abs(chg).toFixed(2)}%
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function MarketPulse() {
  const { dataUpdatedAt } = useQuery({
    queryKey: ["pulse", "Equities"],
    queryFn: () => fetchSnapshot(PULSE_TICKERS.map((t) => t.symbol)),
    refetchInterval: 2 * 60 * 1000,
  });

  const ageMin = dataUpdatedAt ? (Date.now() - dataUpdatedAt) / 60000 : null;

  return (
    <section className="space-y-1.5">
      <PulseBar tickers={PULSE_TICKERS} label="EQUITIES" />
      <PulseBar tickers={FUTURES_TICKERS} label="FUTURES" />
      <FreshnessBar
        sources={[{ label: "Prices", ageMinutes: ageMin, greenThreshold: 5, yellowThreshold: 15 }]}
      />
    </section>
  );
}

// ─── MARKET NEWS ─────────────────────────────────────────────

function MarketNews() {
  const { data } = useQuery({
    queryKey: ["market-news"],
    queryFn: fetchMarketNews,
    staleTime: 30 * 60 * 1000,
  });

  const ageLabel =
    data?.age_hours === 0 ? "< 1 hour ago" : data?.age_hours === 1 ? "1-2 hours ago" : null;

  return (
    <div className="card h-full flex flex-col">
      <div className="flex justify-between items-center mb-3">
        <div className="section-title">Market Intelligence</div>
        {ageLabel && (
          <span className={`text-[0.65rem] font-mono ${data?.age_hours === 0 ? "dot-fresh" : "dot-aging"}`}>
            {ageLabel}
          </span>
        )}
      </div>
      <div className="flex-1">
        {data?.content ? (
          <div className="text-sm leading-relaxed text-text-secondary prose prose-sm prose-gray max-w-none
                          prose-strong:text-text prose-li:my-0.5 prose-ul:my-1 prose-p:my-1">
            <ReactMarkdown>{data.content}</ReactMarkdown>
          </div>
        ) : (
          <p className="text-sm text-text-muted">
            Market news scan not yet available. Updates hourly during market hours.
          </p>
        )}
      </div>
    </div>
  );
}

// ─── SIGNALS ─────────────────────────────────────────────────

function SignalSpotlight() {
  const { data: summary } = useQuery({
    queryKey: ["signal-summary"],
    queryFn: fetchSignalSummary,
    refetchInterval: 60 * 1000,
  });

  const { data: ideas } = useQuery({
    queryKey: ["top-ideas"],
    queryFn: () => fetchTopIdeas(5),
    refetchInterval: 60 * 1000,
  });

  return (
    <div className="card">
      <div className="section-title">Signal Engine</div>
      {summary && summary.n_tickers > 0 ? (
        <>
          <div className="flex gap-4 mb-3">
            <Metric label="Bullish" value={String(summary.n_bullish)} deltaType="gain" />
            <Metric label="Bearish" value={String(summary.n_bearish)} deltaType="loss" />
          </div>
          {ideas && ideas.length > 0 && (
            <div className="space-y-1.5">
              {ideas.map((t) => (
                <div key={t.ticker} className="flex items-center justify-between text-sm py-0.5
                                               border-b border-border last:border-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={`w-2 h-2 rounded-full ${
                        t.overall_direction === "bull"
                          ? "bg-gain"
                          : t.overall_direction === "bear"
                            ? "bg-loss"
                            : "bg-text-muted"
                      }`}
                    />
                    <span className="font-semibold">{t.ticker}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-16 h-1.5 bg-surface-alt rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${t.overall_direction === "bull" ? "bg-gain" : "bg-loss"}`}
                        style={{ width: `${t.overall_conviction * 100}%` }}
                      />
                    </div>
                    <span className="text-text-muted text-xs font-data w-16 text-right">
                      {(t.overall_conviction * 100).toFixed(0)}% · {t.n_signals}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <p className="text-sm text-text-muted">Run analysis pages to generate signals.</p>
      )}
    </div>
  );
}

// ─── POSITIONS ───────────────────────────────────────────────

function PositionSummary() {
  const { data } = useQuery({
    queryKey: ["portfolio-summary"],
    queryFn: fetchPortfolioSummary,
    refetchInterval: 2 * 60 * 1000,
  });

  const pnl = data?.total_pnl ?? 0;
  const isUp = pnl >= 0;

  return (
    <div className="card">
      <div className="flex justify-between items-center mb-3">
        <div className="section-title">Position Book</div>
        {data && data.n_positions > 0 && (
          <span className={`badge ${isUp ? "badge-gain" : "badge-loss"}`}>
            {isUp ? "▲" : "▼"} ${Math.abs(pnl).toLocaleString("en-US", { maximumFractionDigits: 0 })}
          </span>
        )}
      </div>
      {data && data.n_positions > 0 ? (
        <Metric label="Open Positions" value={String(data.n_positions)} />
      ) : (
        <p className="text-sm text-text-muted">No open positions.</p>
      )}
    </div>
  );
}

// ─── HEATMAP ─────────────────────────────────────────────────

const HEATMAP_GROUPS = [
  { key: "sectors", label: "Sectors" },
  { key: "indices", label: "Indices" },
  { key: "fixed_income", label: "Fixed Income" },
  { key: "commodities", label: "Commodities" },
  { key: "mega_caps", label: "Mega Caps" },
];

function heatColor(change: number): string {
  if (change >= 2) return "bg-[#0f7b3f] text-white";
  if (change >= 1) return "bg-[#16a34a] text-white";
  if (change >= 0.3) return "bg-[#dcfce7] text-[#0f7b3f]";
  if (change > -0.3) return "bg-gray-50 text-gray-500";
  if (change > -1) return "bg-[#fee2e2] text-[#b91c1c]";
  if (change > -2) return "bg-[#dc2626] text-white";
  return "bg-[#b91c1c] text-white";
}

function MarketHeatmap() {
  const [group, setGroup] = useState("sectors");
  const { data } = useQuery({
    queryKey: ["heatmap", group],
    queryFn: () => fetchHeatmap(group),
    staleTime: 5 * 60 * 1000,
  });

  return (
    <div className="card">
      <div className="flex justify-between items-center mb-3">
        <div className="section-title">Market Heatmap</div>
        <div className="flex gap-1">
          {HEATMAP_GROUPS.map((g) => (
            <button
              key={g.key}
              onClick={() => setGroup(g.key)}
              className={`px-2 py-0.5 text-[0.65rem] rounded-md transition-colors ${
                group === g.key
                  ? "bg-accent text-white"
                  : "text-text-muted hover:text-text hover:bg-surface-alt"
              }`}
            >
              {g.label}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-1.5">
        {data?.items?.map((item: HeatmapItem) => (
          <div
            key={item.symbol}
            className={`rounded-lg p-2 text-center ${heatColor(item.change)}`}
          >
            <div className="text-[0.65rem] font-semibold">{item.symbol}</div>
            <div className="text-xs font-data font-bold">
              {item.change >= 0 ? "+" : ""}{item.change.toFixed(2)}%
            </div>
          </div>
        )) ?? (
          Array.from({ length: 11 }).map((_, i) => (
            <div key={i} className="h-12 bg-surface-alt rounded-lg animate-pulse" />
          ))
        )}
      </div>
    </div>
  );
}

// ─── EVENTS ──────────────────────────────────────────────────

function UpcomingEvents() {
  const { data } = useQuery({
    queryKey: ["events"],
    queryFn: fetchEvents,
    staleTime: 60 * 60 * 1000,
  });

  return (
    <div className="card">
      <div className="section-title">Upcoming Events</div>
      {data?.events && data.events.length > 0 ? (
        <div className="space-y-0">
          {data.events.map((ev, i) => {
            const urgencyClass =
              ev.days_away === 0
                ? "text-loss font-bold"
                : ev.days_away <= 2
                  ? "text-warn font-semibold"
                  : "text-text-muted";
            const whenLabel = ev.days_away === 0 ? "TODAY" : `in ${ev.days_away}d`;

            return (
              <div
                key={i}
                className="flex justify-between items-center py-1.5 border-b border-border last:border-0 text-sm"
              >
                <span>{ev.name}</span>
                <div className="flex items-center gap-3">
                  <span className="text-[0.7rem] text-text-muted">{ev.date}</span>
                  <span className={`text-[0.7rem] ${urgencyClass}`}>{whenLabel}</span>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-sm text-text-muted">No major events in the next 14 days.</p>
      )}
    </div>
  );
}

// ─── RISK SNAPSHOT ───────────────────────────────────────────

const REGIME_COLORS: Record<string, string> = {
  "Stagflation": "text-red-500",
  "Recession": "text-orange-500",
  "Soft Landing": "text-green-500",
  "Financial Crisis": "text-red-600",
  "Re-Acceleration": "text-sky-500",
  "Goldilocks": "text-purple-500",
};

function RiskSnapshot() {
  const { data } = useQuery({
    queryKey: ["risk"],
    queryFn: fetchRisk,
    staleTime: 5 * 60 * 1000,
  });

  return (
    <div className="card">
      <div className="section-title">Risk Dashboard</div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {/* Iran */}
        <div className="text-center">
          <div className="text-[0.6rem] text-text-muted uppercase tracking-wider">Iran</div>
          {data?.iran ? (
            <>
              <div className={`text-2xl font-bold font-data ${
                data.iran.score >= 8 ? "text-red-500" :
                data.iran.score >= 6 ? "text-orange-500" :
                data.iran.score >= 4 ? "text-warn" : "text-gain"
              }`}>
                {data.iran.score}/10
              </div>
              <div className="text-[0.6rem] text-text-muted">{data.iran.level}</div>
            </>
          ) : <div className="text-sm text-text-muted mt-1">N/A</div>}
        </div>

        {/* Macro */}
        <div className="text-center">
          <div className="text-[0.6rem] text-text-muted uppercase tracking-wider">Macro</div>
          {data?.macro ? (
            <>
              <div className={`text-sm font-bold mt-1 ${REGIME_COLORS[data.macro.top_regime] || "text-text"}`}>
                {data.macro.top_regime}
              </div>
              <div className="text-[0.6rem] text-text-muted">{data.macro.top_prob}%</div>
            </>
          ) : <div className="text-sm text-text-muted mt-1">N/A</div>}
        </div>

        {/* Vol */}
        <div className="text-center">
          <div className="text-[0.6rem] text-text-muted uppercase tracking-wider">SPY Vol</div>
          {data?.vol ? (
            <>
              <div className={`text-sm font-bold font-data mt-1 ${
                data.vol.level === "High" ? "text-loss" :
                data.vol.level === "Low" ? "text-gain" : "text-warn"
              }`}>
                {data.vol.atm_iv}% ({data.vol.level})
              </div>
              {data.vol.vrp !== null && (
                <div className="text-[0.6rem] text-text-muted">VRP {data.vol.vrp}%</div>
              )}
            </>
          ) : <div className="text-sm text-text-muted mt-1">N/A</div>}
        </div>

        {/* Strategy */}
        <div className="text-center">
          <div className="text-[0.6rem] text-text-muted uppercase tracking-wider">Strategy</div>
          {data?.strategy ? (
            <>
              <div className="text-sm font-bold mt-1 text-accent">{data.strategy.rec}</div>
              <div className="text-[0.6rem] text-text-muted">{data.strategy.reason}</div>
            </>
          ) : <div className="text-sm text-text-muted mt-1">N/A</div>}
        </div>
      </div>
    </div>
  );
}

// ─── QUICK NAV ───────────────────────────────────────────────

const NAV_CARDS = [
  { label: "Iron Condor Scanner", href: "/iron-condor", desc: "Short premium setups" },
  { label: "Calendar Spreads", href: "/calendar-spread", desc: "Term structure plays" },
  { label: "Stock Analysis", href: "http://localhost:8501/Stock_Analysis", desc: "3-model AI consensus", external: true },
  { label: "Vol Surface", href: "http://localhost:8501/Vol_Surface", desc: "IV surface + trade ideas", external: true },
  { label: "Signal Scanner", href: "http://localhost:8501/Signal_Scanner", desc: "Multi-factor ranking", external: true },
  { label: "Fed & Macro", href: "http://localhost:8501/Fed_Macro_Drivers", desc: "FOMC, inflation, yields", external: true },
];

function QuickNav() {
  return (
    <div>
      <div className="section-title">Tools</div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        {NAV_CARDS.map((item) => {
          const inner = (
            <div className="card card-compact text-center hover:border-accent transition-colors cursor-pointer group">
              <div className="text-sm font-semibold group-hover:text-accent transition-colors">{item.label}</div>
              <div className="text-[0.65rem] text-text-muted mt-0.5">{item.desc}</div>
            </div>
          );
          return item.external ? (
            <a key={item.label} href={item.href} target="_blank" rel="noopener noreferrer">{inner}</a>
          ) : (
            <Link key={item.label} href={item.href}>{inner}</Link>
          );
        })}
      </div>
    </div>
  );
}

// ─── DASHBOARD ───────────────────────────────────────────────

export default function Dashboard() {
  return (
    <div className="space-y-5">
      <MarketPulse />

      {/* Row 1: News + Signals/Positions */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-3">
          <MarketNews />
        </div>
        <div className="lg:col-span-2 space-y-4">
          <SignalSpotlight />
          <PositionSummary />
        </div>
      </div>

      {/* Row 2: Risk + Events */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <RiskSnapshot />
        </div>
        <div>
          <UpcomingEvents />
        </div>
      </div>

      {/* Row 3: Heatmap */}
      <MarketHeatmap />

      {/* Row 4: Quick Nav */}
      <QuickNav />
    </div>
  );
}
