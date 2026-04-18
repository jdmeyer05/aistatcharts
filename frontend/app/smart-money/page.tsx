"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import {
  fetchRecent13D,
  fetchEdgarEarningsCalendar,
  fetch8KEvents,
  fetchInsiderTransactions,
  type Activist13D,
} from "@/lib/api";
import { getChartTheme } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { fmtBn, shortDate } from "./_shared/utils";

// ──────────────────────────────────────────────────────────────────
// Category nav — matches the Smart Money section in lib/nav.ts
// ──────────────────────────────────────────────────────────────────

const CATEGORIES: { label: string; href: string; description: string; tag?: "new" | "pending" }[] = [
  { label: "Insider Activity", href: "/smart-money/insiders", description: "Form 4 trades + cluster buy detection", tag: "new" },
  { label: "13F Holdings", href: "/smart-money/13f", description: "Institutional holdings from SEC filings" },
  { label: "Global Smart Money", href: "/smart-money/global", description: "Sovereign wealth, pensions, endowments", tag: "pending" },
  { label: "Congressional & Political", href: "/smart-money/political", description: "House/Senate trades + leaderboard" },
  { label: "Activist Campaigns", href: "/smart-money/activist", description: "13D filings + track records" },
  { label: "Short Side", href: "/smart-money/shorts", description: "Short interest + squeeze setups", tag: "pending" },
  { label: "Buybacks & Returns", href: "/smart-money/buybacks", description: "Repurchases + 10b5-1 plans", tag: "pending" },
  { label: "Exit Signals", href: "/smart-money/exits", description: "Inverse tracker — where smart money is leaving", tag: "new" },
  { label: "Material Events", href: "/smart-money/events", description: "8-K filings + guidance" },
  { label: "Alerts", href: "/smart-money/alerts", description: "Notifications on tracked filings", tag: "pending" },
];

// ──────────────────────────────────────────────────────────────────
// Conviction Score — aggregates per-ticker smart money signals
// Applies the user's validated confluence methodology (2+ families
// → 5.3× SPY Sharpe, 3+ → 8.5×) to institutional signals.
// ──────────────────────────────────────────────────────────────────

interface ConvictionBreakdown {
  insiderNet: number;        // -100..100
  insiderBuys: number;
  insiderSells: number;
  insiderClusterBuy: boolean;
  insiderClusterSell: boolean;
  activistNew: boolean;
  activistFilings: Activist13D[];
  recentEightK: number;
  score: number;             // 0..100
  families: number;          // how many signal families fired in user's direction
  direction: "bullish" | "bearish" | "neutral";
}

function classify(txn: string): "BUY" | "SELL" | "OTHER" {
  const s = (txn ?? "").toLowerCase();
  if (s.includes("purchase") || s.includes("buy") || s.includes("acquire")) return "BUY";
  if (s.includes("sale") || s.includes("sell") || s.includes("dispose")) return "SELL";
  return "OTHER";
}

function computeConviction(
  insiderRaw: Record<string, unknown>[],
  activistFilings: Activist13D[],
  eightKCount: number,
  ticker: string,
): ConvictionBreakdown {
  let buyCount = 0;
  let sellCount = 0;
  let buyValue = 0;
  let sellValue = 0;
  const buysByDate: { date: string; insider: string }[] = [];
  const sellsByDate: { date: string; insider: string }[] = [];

  for (const raw of insiderRaw) {
    const txn = String(raw.Transaction ?? "");
    const dir = classify(txn);
    const value = Number(raw.Value ?? 0) || 0;
    const date = String(raw["Start Date"] ?? "").slice(0, 10);
    const insider = String(raw.Insider ?? "");
    if (dir === "BUY") {
      buyCount++;
      buyValue += value;
      if (date) buysByDate.push({ date, insider });
    } else if (dir === "SELL") {
      sellCount++;
      sellValue += value;
      if (date) sellsByDate.push({ date, insider });
    }
  }

  // Detect cluster (3+ distinct insiders within 30 days, same direction).
  const hasCluster = (arr: { date: string; insider: string }[]) => {
    if (arr.length < 3) return false;
    const sorted = [...arr].sort((a, b) => a.date.localeCompare(b.date));
    for (let i = 0; i < sorted.length; i++) {
      const anchor = new Date(sorted[i].date + "T00:00:00");
      const names = new Set<string>();
      for (let j = i; j < sorted.length; j++) {
        const d = new Date(sorted[j].date + "T00:00:00");
        if ((d.getTime() - anchor.getTime()) / 86400000 > 30) break;
        names.add(sorted[j].insider);
      }
      if (names.size >= 3) return true;
    }
    return false;
  };

  const insiderNet = buyValue - sellValue;
  const totalAction = buyCount + sellCount;
  const insiderClusterBuy = hasCluster(buysByDate);
  const insiderClusterSell = hasCluster(sellsByDate);

  const relevantActivist = activistFilings.filter(
    (f) => f.ticker?.toUpperCase() === ticker.toUpperCase(),
  );
  const activistNew = relevantActivist.some((f) => f.is_new);

  // Families: insider-flow, activist, events. Each can fire bullish or bearish.
  let bullFamilies = 0;
  let bearFamilies = 0;

  // Family 1: insider flow
  if (insiderClusterBuy || (insiderNet > 0 && buyCount >= 2)) bullFamilies++;
  if (insiderClusterSell || (insiderNet < 0 && sellCount >= 3 && totalAction >= 4)) bearFamilies++;

  // Family 2: activist (new campaigns are bullish; amendments are neutral here)
  if (activistNew) bullFamilies++;

  // Family 3: high 8-K activity (neither bull nor bear by itself — increases signal strength)
  const eventPulse = eightKCount >= 5 ? 0.5 : 0;
  bullFamilies += eventPulse;
  bearFamilies += eventPulse;

  const netFamilies = bullFamilies - bearFamilies;
  const direction: "bullish" | "bearish" | "neutral" =
    netFamilies > 0.3 ? "bullish" : netFamilies < -0.3 ? "bearish" : "neutral";

  // Score 0-100 — combines confluence count with magnitude
  let score = 50;
  // Family confluence drives most of the variance.
  score += netFamilies * 18;
  // Magnitude from raw $-flow (capped so a single huge trade doesn't dominate).
  const magSignal = Math.max(-1, Math.min(1, insiderNet / 5e7)); // saturate at $50M net
  score += magSignal * 10;
  score = Math.max(0, Math.min(100, Math.round(score)));

  return {
    insiderNet,
    insiderBuys: buyCount,
    insiderSells: sellCount,
    insiderClusterBuy,
    insiderClusterSell,
    activistNew,
    activistFilings: relevantActivist,
    recentEightK: eightKCount,
    score,
    families: Math.max(bullFamilies, bearFamilies),
    direction,
  };
}

function scoreColor(score: number, t: ReturnType<typeof getChartTheme>) {
  if (score >= 70) return t.gain;
  if (score >= 55) return t.hv20;
  if (score <= 30) return t.loss;
  if (score <= 45) return t.hv60;
  return t.muted;
}

// ──────────────────────────────────────────────────────────────────
// Overview page
// ──────────────────────────────────────────────────────────────────

export default function SmartMoneyOverviewPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");

  // Global feeds — prefetched on page load
  const activist90dQ = useQuery({
    queryKey: ["overview-13d-90"],
    queryFn: () => fetchRecent13D(90),
    staleTime: 30 * 60_000,
  });
  const earningsCalQ = useQuery({
    queryKey: ["overview-earnings-7"],
    queryFn: () => fetchEdgarEarningsCalendar(7),
    staleTime: 30 * 60_000,
  });

  // Ticker lookup for conviction score
  const [ticker, setTicker] = useState("");
  const convictionLoad = useMutation({
    mutationFn: async () => {
      const tk = ticker.trim().toUpperCase();
      if (!tk) throw new Error("Enter a ticker");
      const [insider, events] = await Promise.all([
        fetchInsiderTransactions(tk).catch(() => ({ ticker: tk, count: 0, data: [] })),
        fetch8KEvents(tk, 90).catch(() => ({ ticker: tk, count: 0, data: [] })),
      ]);
      return {
        ticker: tk,
        insiderRaw: insider.data,
        eightKCount: events.count,
      };
    },
  });

  const conviction = useMemo(() => {
    if (!convictionLoad.data) return null;
    const activists = activist90dQ.data?.data ?? [];
    return {
      ticker: convictionLoad.data.ticker,
      ...computeConviction(
        convictionLoad.data.insiderRaw,
        activists,
        convictionLoad.data.eightKCount,
        convictionLoad.data.ticker,
      ),
    };
  }, [convictionLoad.data, activist90dQ.data]);

  // "This week" summaries
  const recentNew13D = useMemo(() => {
    const base = activist90dQ.data?.data ?? [];
    return [...base]
      .filter((f) => f.is_new)
      .sort((a, b) => b.filed.localeCompare(a.filed))
      .slice(0, 8);
  }, [activist90dQ.data]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Smart Money</h1>
        <p className="text-text-secondary text-sm mt-1">
          Track institutional positioning, insider activity, activist campaigns, and congressional trades — from
          public filings. The conviction score below aggregates signals across sources into a single ticker-level
          score using the multi-family confluence methodology (2+ families = 5.3× SPY Sharpe in our backtests).
        </p>
      </div>

      {/* Conviction Score */}
      <div className="card">
        <div className="flex items-baseline justify-between flex-wrap gap-2 mb-3">
          <div>
            <div className="text-sm font-bold">Smart Money Conviction Score</div>
            <div className="text-xs text-text-muted">
              Enter a ticker. Score aggregates insider flow, activist involvement, and 8-K event pulse.
            </div>
          </div>
        </div>
        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-[240px]">
            <label className="metric-label">Ticker</label>
            <input
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && ticker && convictionLoad.mutate()}
              placeholder="NVDA"
              className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
            />
          </div>
          <button
            onClick={() => ticker && convictionLoad.mutate()}
            disabled={!ticker || convictionLoad.isPending}
            className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {convictionLoad.isPending ? "Analyzing…" : "Compute Score"}
          </button>
        </div>

        {convictionLoad.isPending && (
          <div className="mt-4 text-center py-4">
            <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
            <div className="text-xs text-text-muted mt-2">
              Pulling insider Form 4 + activist 13D + 8-K events…
            </div>
          </div>
        )}

        {conviction && (() => {
          // "No data" means none of our signal sources returned anything actionable.
          // A default score of 50 + neutral is misleading when it just reflects absence.
          const hasAnySignal =
            conviction.insiderBuys + conviction.insiderSells > 0 ||
            conviction.activistFilings.length > 0 ||
            conviction.recentEightK > 0;

          if (!hasAnySignal) {
            return (
              <div className="mt-5 card card-compact border-l-4 border-l-warn">
                <div className="text-sm font-semibold mb-1">No signal activity for {conviction.ticker}</div>
                <div className="text-xs text-text-muted">
                  The last 90 days show no insider Form 4 filings, no 13D activity, and no 8-K events for this
                  ticker. This can mean the data source hasn&apos;t picked it up, or the company genuinely has low
                  corporate-filing activity. Try a larger-cap ticker (NVDA, AAPL, TSLA) to sanity-check.
                </div>
              </div>
            );
          }

          return (
          <div className="mt-5 grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Score card */}
            <div
              className="rounded-lg p-5 text-center"
              style={{
                border: `2px solid ${scoreColor(conviction.score, t)}`,
                background: `${scoreColor(conviction.score, t)}15`,
              }}
            >
              <div className="text-xs font-bold uppercase tracking-wider text-text-muted">Score</div>
              <div
                className="text-5xl font-bold my-1"
                style={{ color: scoreColor(conviction.score, t) }}
              >
                {conviction.score}
              </div>
              <div
                className="text-sm font-bold uppercase tracking-wider"
                style={{ color: scoreColor(conviction.score, t) }}
              >
                {conviction.direction}
              </div>
              <div className="text-xs text-text-muted mt-2">
                {conviction.families.toFixed(1)} signal {conviction.families === 1 ? "family" : "families"} firing
              </div>
            </div>

            {/* Signal breakdown */}
            <div className="md:col-span-2 rounded-lg p-4 border border-border">
              <div className="text-sm font-semibold mb-3">
                Signal breakdown — {conviction.ticker}
              </div>
              <div className="space-y-2.5 text-xs">
                <div className="flex items-baseline gap-2">
                  <span className="font-bold w-6">{conviction.insiderClusterBuy ? "●" : conviction.insiderClusterSell ? "●" : "○"}</span>
                  <span className="flex-1">
                    <b>Insider cluster:</b>{" "}
                    {conviction.insiderClusterBuy ? (
                      <span className="text-gain font-semibold">BUY cluster detected</span>
                    ) : conviction.insiderClusterSell ? (
                      <span className="text-loss font-semibold">SELL cluster detected</span>
                    ) : (
                      <span className="text-text-muted">No cluster</span>
                    )}
                  </span>
                </div>
                <div className="flex items-baseline gap-2">
                  <span className="font-bold w-6">{Math.abs(conviction.insiderNet) > 1e6 ? "●" : "○"}</span>
                  <span className="flex-1">
                    <b>Insider $ flow:</b>{" "}
                    <span className={conviction.insiderNet > 0 ? "text-gain" : conviction.insiderNet < 0 ? "text-loss" : "text-text-muted"}>
                      {fmtBn(conviction.insiderNet)}
                    </span>
                    <span className="text-text-muted ml-2">
                      ({conviction.insiderBuys} buys / {conviction.insiderSells} sells)
                    </span>
                  </span>
                </div>
                <div className="flex items-baseline gap-2">
                  <span className="font-bold w-6">{conviction.activistNew ? "●" : "○"}</span>
                  <span className="flex-1">
                    <b>Activist:</b>{" "}
                    {conviction.activistNew ? (
                      <span className="text-accent font-semibold">
                        New 13D campaign by {conviction.activistFilings.find((f) => f.is_new)?.activist ?? "an activist"}
                      </span>
                    ) : conviction.activistFilings.length > 0 ? (
                      <span className="text-text-muted">
                        {conviction.activistFilings.length} amendment(s) — no new campaign
                      </span>
                    ) : (
                      <span className="text-text-muted">No 13D filings in last 90 days</span>
                    )}
                  </span>
                </div>
                <div className="flex items-baseline gap-2">
                  <span className="font-bold w-6">{conviction.recentEightK >= 5 ? "●" : "○"}</span>
                  <span className="flex-1">
                    <b>Event pulse:</b>{" "}
                    <span className={conviction.recentEightK >= 5 ? "text-accent" : "text-text-muted"}>
                      {conviction.recentEightK} 8-K filings in the last 90 days
                    </span>
                  </span>
                </div>
              </div>

              <div className="text-[11px] text-text-muted mt-4 pt-3 border-t border-border">
                <strong>Interpretation:</strong> Confluence &ge; 2 families in one direction historically produces
                institutional-grade edge. Single-family signals are noise.
              </div>
            </div>
          </div>
          );
        })()}
      </div>

      {/* This week feeds */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <div className="card">
          <div className="flex items-baseline justify-between mb-2">
            <div className="text-sm font-bold">Recent Activist Campaigns</div>
            <Link href="/smart-money/activist" className="text-xs text-accent hover:underline">
              See all →
            </Link>
          </div>
          {activist90dQ.isPending && (
            <div className="text-xs text-text-muted">Loading 13D feed…</div>
          )}
          {recentNew13D.length > 0 ? (
            <div className="space-y-2">
              {recentNew13D.map((f, i) => (
                <div key={i} className="p-2 rounded border border-border text-xs">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    {f.ticker && (
                      <span
                        className="px-1.5 py-0.5 rounded text-[10px] font-bold font-data"
                        style={{ background: t.spot, color: "#000" }}
                      >
                        {f.ticker}
                      </span>
                    )}
                    <span className="font-semibold flex-1 truncate">{f.target.slice(0, 50)}</span>
                    <span className="text-[10px] text-text-muted">{shortDate(f.filed)}</span>
                  </div>
                  <div className="text-[11px] text-text-muted mt-1 truncate">{f.activist.slice(0, 50)}</div>
                </div>
              ))}
            </div>
          ) : activist90dQ.isSuccess && !activist90dQ.isPending ? (
            <div className="text-xs text-text-muted">No new activist filings in the last 90 days.</div>
          ) : null}
        </div>

        <div className="card">
          <div className="flex items-baseline justify-between mb-2">
            <div className="text-sm font-bold">Recent Earnings Releases</div>
            <Link href="/smart-money/events" className="text-xs text-accent hover:underline">
              See all →
            </Link>
          </div>
          {earningsCalQ.isPending && (
            <div className="text-xs text-text-muted">Loading earnings feed…</div>
          )}
          {earningsCalQ.data && earningsCalQ.data.count > 0 ? (
            <div className="space-y-1.5 max-h-[400px] overflow-y-auto">
              {earningsCalQ.data.data.slice(0, 20).map((row, i) => (
                <div key={i} className="flex items-baseline gap-2 text-xs py-1 border-b border-border/50">
                  <span className="text-text-muted font-data w-20">{shortDate(row.filed)}</span>
                  <span className="font-bold font-data w-16">{row.ticker}</span>
                  <span className="flex-1 truncate">{row.company}</span>
                </div>
              ))}
            </div>
          ) : earningsCalQ.isSuccess ? (
            <div className="text-xs text-text-muted">No earnings releases this week.</div>
          ) : null}
        </div>
      </div>

      {/* Headline metrics */}
      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          <Metric
            label="New 13D (90d)"
            value={String(recentNew13D.length)}
            deltaType="gain"
          />
          <Metric
            label="Total 13D Filings (90d)"
            value={String(activist90dQ.data?.count ?? 0)}
          />
          <Metric
            label="Earnings This Week"
            value={String(earningsCalQ.data?.count ?? 0)}
          />
        </div>
      </div>

      {/* Category nav */}
      <div className="card">
        <div className="text-sm font-bold mb-3">Deep-dive pages</div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {CATEGORIES.map((c) => (
            <Link
              key={c.href}
              href={c.href}
              className="p-3 rounded border border-border hover:border-accent hover:bg-surface-alt/50 transition-colors"
            >
              <div className="flex items-baseline justify-between mb-1">
                <span className="text-sm font-semibold">{c.label}</span>
                {c.tag === "new" && (
                  <span className="text-[9px] font-bold uppercase tracking-wider text-gain">New</span>
                )}
                {c.tag === "pending" && (
                  <span className="text-[9px] font-bold uppercase tracking-wider text-warn">Pending</span>
                )}
              </div>
              <div className="text-[11px] text-text-muted leading-relaxed">{c.description}</div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
