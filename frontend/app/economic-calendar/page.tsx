"use client";

import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchEvents, fetchPriceHistoryBatch } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { useState, useMemo } from "react";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Upcoming Events", "Yield Curve"];

const YIELD_TICKERS: Record<string, string> = {
  "^IRX": "3M", "^FVX": "5Y", "^TNX": "10Y", "^TYX": "30Y",
};

export default function EconomicCalendar() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);

  const { data: events, isLoading } = useQuery({
    queryKey: ["events"],
    queryFn: fetchEvents,
    staleTime: 10 * 60 * 1000,
  });

  const { data: yieldData } = useQuery({
    queryKey: ["yield-curve"],
    queryFn: () => fetchPriceHistoryBatch(Object.keys(YIELD_TICKERS), 60),
    staleTime: 10 * 60 * 1000,
    enabled: activeTab === 1,
  });

  const yieldCurve = useMemo(() => {
    if (!yieldData) return null;
    const points: { label: string; yield: number }[] = [];
    for (const [tk, label] of Object.entries(YIELD_TICKERS)) {
      const hist = yieldData[tk];
      if (hist && hist.length > 0) {
        points.push({ label, yield: hist[hist.length - 1].Close });
      }
    }
    return points;
  }, [yieldData]);

  if (isLoading) {
    return (
      <div className="space-y-5">
        <div><h1 className="text-2xl font-bold tracking-tight">Economic Calendar</h1></div>
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      </div>
    );
  }

  const evts = events?.events ?? [];
  const todayEvents = evts.filter(e => e.days_away === 0);
  const thisWeek = evts.filter(e => e.days_away > 0 && e.days_away <= 7);
  const later = evts.filter(e => e.days_away > 7);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Economic Calendar</h1>
        <p className="text-text-secondary text-sm mt-1">Upcoming macro releases, FOMC dates, and yield curve.</p>
      </div>

      {/* Hero metrics */}
      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          <Metric label="Today" value={todayEvents.length > 0 ? `${todayEvents.length} event${todayEvents.length > 1 ? "s" : ""}` : "No events"} />
          <Metric label="This Week" value={String(thisWeek.length)} />
          <Metric label="Next Event" value={evts[0]?.name ?? "—"} delta={evts[0] ? `${evts[0].days_away}d away` : undefined} />
        </div>
      </div>

      <div className="flex gap-1 border-b border-border pb-1">
        {TABS.map((tab, i) => (
          <button key={tab} onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
              activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
            {tab}
          </button>
        ))}
      </div>

      {/* Tab 0: Events */}
      {activeTab === 0 && (
        <div className="space-y-4">
          {/* Today */}
          {todayEvents.length > 0 && (
            <div className="card border-l-4 border-l-loss">
              <div className="text-xs font-bold uppercase tracking-wider text-loss mb-2">Today</div>
              {todayEvents.map((e, i) => (
                <div key={i} className="flex justify-between items-center py-1 border-b border-border last:border-0">
                  <span className="text-sm font-semibold">{e.name}</span>
                  <span className="badge badge-loss">TODAY</span>
                </div>
              ))}
            </div>
          )}

          {/* This week */}
          {thisWeek.length > 0 && (
            <div className="card">
              <div className="text-xs font-bold uppercase tracking-wider text-warn mb-2">This Week</div>
              {thisWeek.map((e, i) => (
                <div key={i} className="flex justify-between items-center py-1.5 border-b border-border last:border-0">
                  <div>
                    <span className="text-sm font-semibold">{e.name}</span>
                    <span className="text-xs text-text-muted ml-2">{e.date}</span>
                  </div>
                  <span className={`badge ${e.days_away <= 2 ? "badge-warn" : "badge-info"}`}>{e.days_away}d</span>
                </div>
              ))}
            </div>
          )}

          {/* Later */}
          {later.length > 0 && (
            <div className="card">
              <div className="text-xs font-bold uppercase tracking-wider text-text-muted mb-2">Coming Up</div>
              {later.map((e, i) => (
                <div key={i} className="flex justify-between items-center py-1.5 border-b border-border last:border-0">
                  <div>
                    <span className="text-sm">{e.name}</span>
                    <span className="text-xs text-text-muted ml-2">{e.date}</span>
                  </div>
                  <span className="text-xs text-text-muted">{e.days_away}d</span>
                </div>
              ))}
            </div>
          )}

          {evts.length === 0 && <div className="card text-center py-8 text-text-muted">No upcoming events found.</div>}
        </div>
      )}

      {/* Tab 1: Yield Curve */}
      {activeTab === 1 && (
        <div className="card space-y-4">
          {yieldCurve && yieldCurve.length > 1 ? (<>
            <Plot data={[{
              x: yieldCurve.map(p => p.label),
              y: yieldCurve.map(p => p.yield),
              type: "scatter" as const, mode: "lines+markers" as const,
              line: { color: t.accent, width: 3 }, marker: { size: 10, color: t.accent },
              text: yieldCurve.map(p => `${p.yield.toFixed(2)}%`),
              textposition: "top center" as const,
              textfont: { size: 11, color: t.text },
              hovertemplate: "%{x}: %{y:.2f}%<extra></extra>",
            }]}
              layout={{ height: 400, ...L, xaxis: { title: "Maturity", gridcolor: t.grid }, yaxis: { title: "Yield (%)", gridcolor: t.grid } }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            <div className="flex gap-6">
              {yieldCurve.map(p => <Metric key={p.label} label={p.label} value={`${p.yield.toFixed(2)}%`} />)}
              {yieldCurve.length >= 2 && (
                <Metric label="2s10s Spread" value={`${(yieldCurve[yieldCurve.length - 2].yield - yieldCurve[0].yield).toFixed(2)}%`}
                  deltaType={(yieldCurve[yieldCurve.length - 2].yield - yieldCurve[0].yield) < 0 ? "loss" : "gain"} />
              )}
            </div>
          </>) : <p className="text-sm text-text-muted">Loading yield data...</p>}
        </div>
      )}
    </div>
  );
}
