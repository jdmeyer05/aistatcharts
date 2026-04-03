"use client";

import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchOilBundle, type EIARecord } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { useState, useMemo } from "react";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = [
  "Inventories + 5-Year Band", "YoY Seasonality", "Weekly Builds / Draws", "WTI Price Overlay",
  "Cushing Storage", "Imports / Exports", "Refinery Utilization", "Product Inventories",
];

function weekOfYear(dateStr: string): number {
  const d = new Date(dateStr + "T12:00:00");
  const jan1 = new Date(d.getFullYear(), 0, 1);
  return Math.ceil(((d.getTime() - jan1.getTime()) / 86400000 + jan1.getDay() + 1) / 7);
}
function yearOf(dateStr: string): number {
  return new Date(dateStr + "T12:00:00").getFullYear();
}
function last(arr: EIARecord[]): EIARecord { return arr[arr.length - 1]; }
function tail(arr: EIARecord[], n: number): EIARecord[] { return arr.slice(-n); }

export default function OilFundamentals() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["oil-bundle"],
    queryFn: fetchOilBundle,
    staleTime: 30 * 60 * 1000,
  });

  const computed = useMemo(() => {
    if (!data || data.inventories.length === 0) return null;
    const inv = data.inventories;
    const latest = last(inv);
    const invMb = latest.value / 1000;
    const invWow = (latest.wow_change ?? 0) / 1000;
    const currentYear = yearOf(latest.period);

    // 5-year stats
    const byWeek = new Map<number, number[]>();
    for (const r of inv) {
      const yr = yearOf(r.period);
      if (yr >= currentYear - 5 && yr <= currentYear - 1) {
        const w = weekOfYear(r.period);
        if (!byWeek.has(w)) byWeek.set(w, []);
        byWeek.get(w)!.push(r.value / 1000);
      }
    }
    const fiveYrStats = Array.from(byWeek.entries()).map(([week, vals]) => ({
      week, avg: vals.reduce((s, v) => s + v, 0) / vals.length, min: Math.min(...vals), max: Math.max(...vals),
    })).sort((a, b) => a.week - b.week);

    const currentWeek = weekOfYear(latest.period);
    const avgNow = fiveYrStats.find(s => s.week === currentWeek);
    const deltaVsAvg = avgNow ? invMb - avgNow.avg : null;

    let daysOfSupply: number | null = null;
    if (data.supplied.length > 0) {
      const dailyMb = last(data.supplied).value;
      if (dailyMb > 0) daysOfSupply = Math.round(latest.value / dailyMb);
    }

    const years = [...new Set(inv.map(r => yearOf(r.period)))].sort().slice(-5);
    return { latest, invMb, invWow, currentYear, fiveYrStats, deltaVsAvg, daysOfSupply, years, inv };
  }, [data]);

  if (isLoading) {
    return (
      <div className="space-y-5">
        <div><h1 className="text-2xl font-bold tracking-tight">Oil Fundamentals</h1></div>
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching EIA data...</p>
        </div>
      </div>
    );
  }
  if (isError || !data || !computed) {
    return (
      <div className="space-y-5">
        <div><h1 className="text-2xl font-bold tracking-tight">Oil Fundamentals</h1></div>
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm">EIA data unavailable. Check EIA_API_KEY.</div>
      </div>
    );
  }

  const { latest, invMb, invWow, currentYear, fiveYrStats, deltaVsAvg, daysOfSupply, years, inv } = computed;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Oil Fundamentals</h1>
        <p className="text-text-secondary text-sm mt-1">Weekly EIA petroleum status report — {latest.period}</p>
      </div>

      {/* Primary metrics */}
      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          <Metric label="Commercial Inventories" value={`${invMb.toFixed(1)}M bbls`}
            delta={`${invWow > 0 ? "+" : ""}${invWow.toFixed(2)}M WoW`} deltaType={invWow > 0 ? "loss" : "gain"} />
          {data.production.length > 0 && (() => {
            const l = last(data.production); const mbpd = l.value / 1000; const wow = (l.wow_change ?? 0) / 1000;
            return <Metric label="US Field Production" value={`${mbpd.toFixed(1)}M bpd`} delta={`${wow > 0 ? "+" : ""}${wow.toFixed(2)}M WoW`} deltaType={wow > 0 ? "gain" : "loss"} />;
          })()}
          {deltaVsAvg != null && <Metric label="vs 5-Year Average" value={`${Math.abs(deltaVsAvg).toFixed(1)}M ${deltaVsAvg > 0 ? "Above" : "Below"}`} />}
          {daysOfSupply != null && <Metric label="Days of Supply" value={`${daysOfSupply} days`} />}
        </div>
      </div>

      {/* Secondary metrics */}
      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          {data.wti.length > 0 && (() => {
            const l = last(data.wti);
            return <Metric label="WTI Spot" value={`$${l.value.toFixed(2)}/bbl`} delta={`$${(l.wow_change ?? 0) > 0 ? "+" : ""}${(l.wow_change ?? 0).toFixed(2)} WoW`} deltaType={(l.wow_change ?? 0) > 0 ? "gain" : "loss"} />;
          })()}
          {data.cushing.length > 0 && (() => {
            const l = last(data.cushing);
            return <Metric label="Cushing, OK" value={`${(l.value / 1000).toFixed(1)}M bbls`} delta={`${((l.wow_change ?? 0) / 1000) > 0 ? "+" : ""}${((l.wow_change ?? 0) / 1000).toFixed(2)}M WoW`} deltaType={(l.wow_change ?? 0) > 0 ? "loss" : "gain"} />;
          })()}
          {data.refinery.length > 0 && <Metric label="Refinery Util." value={`${last(data.refinery).value.toFixed(1)}%`} />}
          {data.imports.length > 0 && data.exports.length > 0 && (
            <Metric label="Net Crude Imports" value={`${((last(data.imports).value - last(data.exports).value) / 1000).toFixed(1)}M bpd`} />
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
        {TABS.map((tab, i) => (
          <button key={tab} onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
              activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
            {tab}
          </button>
        ))}
      </div>

      {/* Tab 0: Inventories + 5-Year Band */}
      {activeTab === 0 && (
        <div className="card">
          <Plot data={[
            ...(fiveYrStats.length > 0 ? [{
              x: [...fiveYrStats.map(s => `${currentYear}-W${String(s.week).padStart(2, "0")}`), ...fiveYrStats.slice().reverse().map(s => `${currentYear}-W${String(s.week).padStart(2, "0")}`)],
              y: [...fiveYrStats.map(s => s.max), ...fiveYrStats.slice().reverse().map(s => s.min)],
              fill: "toself" as const, fillcolor: t.accent + "12", line: { color: "transparent", width: 0 },
              name: "5-Year Range", hoverinfo: "skip" as const, type: "scatter" as const, mode: "lines" as const,
            }, {
              x: fiveYrStats.map(s => `${currentYear}-W${String(s.week).padStart(2, "0")}`),
              y: fiveYrStats.map(s => s.avg), type: "scatter" as const, mode: "lines" as const,
              name: "5-Year Average", line: { color: t.spot, width: 2, dash: "dash" as const },
            }] : []),
            { x: inv.filter(r => yearOf(r.period) >= currentYear - 4).map(r => r.period),
              y: inv.filter(r => yearOf(r.period) >= currentYear - 4).map(r => r.value / 1000),
              type: "scatter" as const, mode: "lines" as const, name: "Actual Inventories", line: { color: t.hv20, width: 2.5 } },
          ]} layout={{ height: 500, ...L, yaxis: { title: "Millions of Barrels", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified", legend: { x: 0.01, y: 0.99, bgcolor: "transparent" } }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
        </div>
      )}

      {/* Tab 1: YoY Seasonality */}
      {activeTab === 1 && (
        <div className="card">
          <Plot data={years.map((yr, i) => {
            const yrData = inv.filter(r => yearOf(r.period) === yr).map(r => ({ week: weekOfYear(r.period), value: r.value / 1000 })).sort((a, b) => a.week - b.week);
            const colors = [t.hv20, t.accent, t.gain, t.spot, t.hv60];
            return { x: yrData.map(d => d.week), y: yrData.map(d => d.value), type: "scatter" as const, mode: "lines" as const,
              name: String(yr), line: { color: colors[i % colors.length], width: yr === currentYear ? 3 : 1.5 }, opacity: yr === currentYear ? 1 : 0.7 };
          })} layout={{ height: 500, ...L, xaxis: { title: "Week of Year", gridcolor: t.grid }, yaxis: { title: "Millions of Barrels", gridcolor: t.grid }, hovermode: "x unified", legend: { x: 0.01, y: 0.99, bgcolor: "transparent" } }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
        </div>
      )}

      {/* Tab 2: Weekly Builds / Draws */}
      {activeTab === 2 && (() => { const d = tail(inv, 260); return (
        <div className="card">
          <Plot data={[{ x: d.map(r => r.period), y: d.map(r => (r.wow_change ?? 0) / 1000), type: "bar" as const,
            marker: { color: d.map(r => (r.wow_change ?? 0) > 0 ? t.loss : t.gain) },
            hovertemplate: "%{x}<br>%{y:.2f}M bbls<extra></extra>" }]}
            layout={{ height: 500, ...L, yaxis: { title: "Weekly Change (M bbls)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid },
              shapes: [{ type: "line", y0: 0, y1: 0, x0: d[0]?.period, x1: d[d.length - 1]?.period, line: { color: t.muted, width: 1 } }] }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
          <p className="text-xs text-text-muted mt-2">Green = Draw (Bullish) | Red = Build (Bearish)</p>
        </div>
      ); })()}

      {/* Tab 3: WTI Price Overlay */}
      {activeTab === 3 && (
        <div className="card">
          {data.wti.length > 0 ? (
            <Plot data={[
              { x: tail(inv, 260).map(r => r.period), y: tail(inv, 260).map(r => r.value / 1000), type: "scatter" as const, mode: "lines" as const, name: "Inventories", line: { color: t.hv20, width: 2 }, yaxis: "y" },
              { x: tail(data.wti, 260).map(r => r.period), y: tail(data.wti, 260).map(r => r.value), type: "scatter" as const, mode: "lines" as const, name: "WTI Spot ($/bbl)", line: { color: t.gain, width: 2 }, yaxis: "y2" },
            ]} layout={{ height: 500, ...L, hovermode: "x unified",
              yaxis: { title: "Inventories (M bbls)", side: "left", showgrid: false, color: t.text },
              yaxis2: { title: "WTI ($/bbl)", side: "right", overlaying: "y", showgrid: false, color: t.text },
              legend: { x: 0.01, y: 0.99, bgcolor: "transparent" } }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
          ) : <p className="text-sm text-text-muted">WTI price data unavailable.</p>}
        </div>
      )}

      {/* Tab 4: Cushing Storage */}
      {activeTab === 4 && (
        <div className="card space-y-4">
          {data.cushing.length > 0 ? (<>
            <Plot data={[{ x: tail(data.cushing, 260).map(r => r.period), y: tail(data.cushing, 260).map(r => r.value / 1000),
              type: "scatter" as const, mode: "lines" as const, name: "Cushing Storage", line: { color: t.hv60, width: 2 },
              fill: "tozeroy" as const, fillcolor: t.hv60 + "1a" }]}
              layout={{ height: 400, ...L, yaxis: { title: "Millions of Barrels", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            {/* Weekly change bars */}
            {(() => { const d = tail(data.cushing, 260); return (
              <Plot data={[{ x: d.map(r => r.period), y: d.map(r => (r.wow_change ?? 0) / 1000), type: "bar" as const,
                marker: { color: d.map(r => (r.wow_change ?? 0) > 0 ? t.loss : t.gain) },
                hovertemplate: "%{x}<br>%{y:.2f}M bbls<extra></extra>" }]}
                layout={{ height: 250, ...L, yaxis: { title: "Weekly Change (M bbls)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid },
                  shapes: [{ type: "line", y0: 0, y1: 0, x0: d[0]?.period, x1: d[d.length - 1]?.period, line: { color: t.muted, width: 1 } }] }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            ); })()}
          </>) : <p className="text-sm text-text-muted">Cushing data unavailable.</p>}
        </div>
      )}

      {/* Tab 5: Imports / Exports */}
      {activeTab === 5 && (
        <div className="card">
          {data.imports.length > 0 && data.exports.length > 0 ? (() => {
            const imp = tail(data.imports, 260);
            const exp = tail(data.exports, 260);
            // Build net imports by matching periods
            const expMap = new Map(exp.map(r => [r.period, r.value]));
            const trade = imp.filter(r => expMap.has(r.period)).map(r => ({
              period: r.period, imports: r.value / 1000, exports: (expMap.get(r.period) ?? 0) / 1000,
              net: (r.value - (expMap.get(r.period) ?? 0)) / 1000,
            }));
            return (
              <Plot data={[
                { x: trade.map(r => r.period), y: trade.map(r => r.imports), type: "scatter" as const, mode: "lines" as const, name: "Imports", line: { color: t.accent, width: 2 } },
                { x: trade.map(r => r.period), y: trade.map(r => r.exports), type: "scatter" as const, mode: "lines" as const, name: "Exports", line: { color: t.loss, width: 2 } },
                { x: trade.map(r => r.period), y: trade.map(r => r.net), type: "scatter" as const, mode: "lines" as const, name: "Net Imports", line: { color: t.spot, width: 2, dash: "dash" as const } },
              ]} layout={{ height: 500, ...L, yaxis: { title: "M Barrels Per Day", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified",
                shapes: [{ type: "line", y0: 0, y1: 0, x0: trade[0]?.period, x1: trade[trade.length - 1]?.period, line: { color: t.muted, width: 1 } }],
                legend: { x: 0.01, y: 0.99, bgcolor: "transparent" } }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            );
          })() : <p className="text-sm text-text-muted">Import/export data unavailable.</p>}
        </div>
      )}

      {/* Tab 6: Refinery Utilization */}
      {activeTab === 6 && (
        <div className="card">
          {data.refinery.length > 0 ? (
            <Plot data={[{ x: tail(data.refinery, 260).map(r => r.period), y: tail(data.refinery, 260).map(r => r.value),
              type: "scatter" as const, mode: "lines" as const, name: "Utilization Rate",
              line: { color: t.gain, width: 2 }, fill: "tozeroy" as const, fillcolor: t.gain + "1a" }]}
              layout={{ height: 500, ...L,
                yaxis: { title: "Utilization Rate (%)", gridcolor: t.grid, range: [75, 100] },
                xaxis: { gridcolor: t.grid }, hovermode: "x unified",
                shapes: [{ type: "line", y0: 90, y1: 90, x0: 0, x1: 1, xref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }],
                annotations: [{ x: 1, y: 90, xref: "paper", text: "90% Threshold", showarrow: false, font: { size: 9, color: t.spot } }] }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
          ) : <p className="text-sm text-text-muted">Refinery data unavailable.</p>}
        </div>
      )}

      {/* Tab 7: Product Inventories */}
      {activeTab === 7 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Gasoline */}
          <div className="card space-y-2">
            <div className="text-sm font-bold">Gasoline Inventories</div>
            {data.gasoline.length > 0 ? (<>
              <Metric label="Total Motor Gasoline" value={`${(last(data.gasoline).value / 1000).toFixed(1)}M bbls`}
                delta={`${((last(data.gasoline).wow_change ?? 0) / 1000) > 0 ? "+" : ""}${((last(data.gasoline).wow_change ?? 0) / 1000).toFixed(2)}M WoW`}
                deltaType={(last(data.gasoline).wow_change ?? 0) > 0 ? "loss" : "gain"} />
              <Plot data={[{ x: tail(data.gasoline, 260).map(r => r.period), y: tail(data.gasoline, 260).map(r => r.value / 1000),
                type: "scatter" as const, mode: "lines" as const, line: { color: t.accent, width: 2 },
                fill: "tozeroy" as const, fillcolor: t.accent + "1a" }]}
                layout={{ height: 300, ...L, margin: { l: 40, r: 10, t: 10, b: 30 }, yaxis: { title: "M bbls", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified", showlegend: false }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </>) : <p className="text-sm text-text-muted">Unavailable.</p>}
          </div>
          {/* Distillate */}
          <div className="card space-y-2">
            <div className="text-sm font-bold">Distillate Inventories</div>
            {data.distillate.length > 0 ? (<>
              <Metric label="Distillate Fuel Oil" value={`${(last(data.distillate).value / 1000).toFixed(1)}M bbls`}
                delta={`${((last(data.distillate).wow_change ?? 0) / 1000) > 0 ? "+" : ""}${((last(data.distillate).wow_change ?? 0) / 1000).toFixed(2)}M WoW`}
                deltaType={(last(data.distillate).wow_change ?? 0) > 0 ? "loss" : "gain"} />
              <Plot data={[{ x: tail(data.distillate, 260).map(r => r.period), y: tail(data.distillate, 260).map(r => r.value / 1000),
                type: "scatter" as const, mode: "lines" as const, line: { color: t.loss, width: 2 },
                fill: "tozeroy" as const, fillcolor: t.loss + "1a" }]}
                layout={{ height: 300, ...L, margin: { l: 40, r: 10, t: 10, b: 30 }, yaxis: { title: "M bbls", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified", showlegend: false }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </>) : <p className="text-sm text-text-muted">Unavailable.</p>}
          </div>
        </div>
      )}
    </div>
  );
}
