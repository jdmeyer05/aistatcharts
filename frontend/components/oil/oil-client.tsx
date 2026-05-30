"use client";

import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchOilBundle, type EIARecord } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { useState, useMemo } from "react";
import { Plot } from "@/components/plot";


const TABS = [
  "Inventories + 5-Year Band", "YoY Seasonality", "Weekly Builds / Draws", "WTI Price Overlay",
  "Cushing Storage", "Imports / Exports", "Refinery Utilization", "Product Inventories",
  "Global / OECD Stocks",
];

const MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function weekOfYear(dateStr: string): number {
  const d = new Date(dateStr + "T12:00:00");
  const jan1 = new Date(d.getFullYear(), 0, 1);
  return Math.ceil(((d.getTime() - jan1.getTime()) / 86400000 + jan1.getDay() + 1) / 7);
}
function yearOf(dateStr: string): number {
  return new Date(dateStr + "T12:00:00").getFullYear();
}
function monthOf(dateStr: string): number {
  return new Date(dateStr + "T12:00:00").getMonth() + 1;
}
function last(arr: EIARecord[]): EIARecord { return arr[arr.length - 1]; }
function tail(arr: EIARecord[], n: number): EIARecord[] { return arr.slice(-n); }

/** Empirical CDF — % of `values` strictly less than `target`. Equal values count
 * as half a position (mid-rank), so a target equal to every observation lands
 * at ~50% rather than 0% or 100%. */
function percentileRank(values: number[], target: number): number {
  if (values.length === 0) return 0;
  let lt = 0, eq = 0;
  for (const v of values) {
    if (v < target) lt += 1;
    else if (v === target) eq += 1;
  }
  return ((lt + 0.5 * eq) / values.length) * 100;
}

/** Color palette for the 5 PADDs. Stable across charts so the regional
 * stacked-area and the ranked table line up visually. */
function paddColors(t: { accent: string; hv60: string; gain: string; spot: string; hv20: string }) {
  return {
    p1: t.accent, // East Coast — coastal blue
    p2: t.hv60,   // Midwest — purple (Cushing / WTI delivery)
    p3: t.gain,   // Gulf Coast — green (the big refining hub)
    p4: t.spot,   // Rocky Mountain — amber
    p5: t.hv20,   // West Coast — orange (isolated market)
  };
}

const PADD_LABELS: Record<1 | 2 | 3 | 4 | 5, string> = {
  1: "PADD 1 (East Coast)",
  2: "PADD 2 (Midwest)",
  3: "PADD 3 (Gulf Coast)",
  4: "PADD 4 (Rocky Mtn)",
  5: "PADD 5 (West Coast)",
};

export default function OilClient() {
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
          {data.spr.length > 0 && (() => {
            const l = last(data.spr); const sprMb = l.value / 1000; const wow = (l.wow_change ?? 0) / 1000;
            // SPR convention is opposite of commercial: builds are a positive
            // (security) signal, draws are negative.
            return <Metric label="Strategic Petroleum Reserve" value={`${sprMb.toFixed(1)}M bbls`}
              delta={`${wow > 0 ? "+" : ""}${wow.toFixed(2)}M WoW`} deltaType={wow >= 0 ? "gain" : "loss"} />;
          })()}
          {data.spr.length > 0 && (() => {
            const totalMb = (latest.value + last(data.spr).value) / 1000;
            return <Metric label="Total US Crude" value={`${totalMb.toFixed(1)}M bbls`} />;
          })()}
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

      {/* Tab 0: Inventories deep-dive — 5-yr band + PADD breakdown + SPR + DoS trend */}
      {activeTab === 0 && (
        <div className="space-y-5">
          {/* Section 1: Commercial crude vs 5-year band */}
          {(() => {
            // All three traces share a numeric week-of-year (1-52) x-axis so
            // the current-year actuals overlay the historical band directly.
            // Prior bug: band used string week labels while actuals used date
            // strings, so Plotly treated them as categorical and laid them
            // side-by-side.
            const currentYearInv = inv
              .filter(r => yearOf(r.period) === currentYear)
              .map(r => ({ week: weekOfYear(r.period), value: r.value / 1000 }))
              .sort((a, b) => a.week - b.week);
            return (
              <div className="card space-y-2">
                <div className="text-sm font-semibold">Commercial Crude vs 5-Year Range</div>
                <Plot data={[
                  ...(fiveYrStats.length > 0 ? [{
                    x: [...fiveYrStats.map(s => s.week), ...fiveYrStats.slice().reverse().map(s => s.week)],
                    y: [...fiveYrStats.map(s => s.max), ...fiveYrStats.slice().reverse().map(s => s.min)],
                    fill: "toself" as const, fillcolor: t.accent + "12", line: { color: "transparent", width: 0 },
                    name: "5-Year Range", hoverinfo: "skip" as const, type: "scatter" as const, mode: "lines" as const,
                  }, {
                    x: fiveYrStats.map(s => s.week),
                    y: fiveYrStats.map(s => s.avg), type: "scatter" as const, mode: "lines" as const,
                    name: "5-Year Average", line: { color: t.spot, width: 2, dash: "dash" as const },
                  }] : []),
                  {
                    x: currentYearInv.map(d => d.week),
                    y: currentYearInv.map(d => d.value),
                    type: "scatter" as const, mode: "lines" as const,
                    name: `${currentYear} Actual`, line: { color: t.hv20, width: 2.5 },
                  },
                ]} layout={{
                  height: 460, ...L,
                  yaxis: { title: "Millions of Barrels", gridcolor: t.grid },
                  xaxis: { title: "Week of Year", gridcolor: t.grid, range: [1, 52], dtick: 4 },
                  hovermode: "x unified",
                  legend: { x: 0.01, y: 0.99, bgcolor: "transparent" },
                }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              </div>
            );
          })()}

          {/* Section 2: PADD regional breakdown — stacked area + ranked table */}
          {(() => {
            const padds: { id: 1 | 2 | 3 | 4 | 5; data: EIARecord[] }[] = [
              { id: 1, data: data.padd1 },
              { id: 2, data: data.padd2 },
              { id: 3, data: data.padd3 },
              { id: 4, data: data.padd4 },
              { id: 5, data: data.padd5 },
            ].filter(p => p.data.length > 0) as typeof padds;
            if (padds.length === 0) return null;
            const colors = paddColors(t);
            const colorById = { 1: colors.p1, 2: colors.p2, 3: colors.p3, 4: colors.p4, 5: colors.p5 };

            // Build ranked-table rows: current Mb, WoW Mb, 5-yr percentile rank.
            // Percentile uses the last 5 years (260 weekly obs) so it scales
            // with PADD size — a PADD2 percentile of 80 is comparable to a
            // PADD5 percentile of 80 even though absolute levels differ ~5×.
            const rows = padds.map(p => {
              const l = last(p.data);
              const mb = l.value / 1000;
              const wow = (l.wow_change ?? 0) / 1000;
              const hist = tail(p.data, 260).map(r => r.value);
              const pct = percentileRank(hist, l.value);
              return { id: p.id, label: PADD_LABELS[p.id], mb, wow, pct, color: colorById[p.id] };
            }).sort((a, b) => b.mb - a.mb); // largest first

            return (
              <div className="card space-y-3">
                <div className="text-sm font-semibold">PADD Regional Breakdown</div>
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                  <div className="lg:col-span-2">
                    <Plot data={padds.map(p => {
                      const recs = tail(p.data, 260);
                      return {
                        x: recs.map(r => r.period),
                        y: recs.map(r => r.value / 1000),
                        type: "scatter" as const, mode: "lines" as const,
                        name: PADD_LABELS[p.id],
                        stackgroup: "one",
                        line: { color: colorById[p.id], width: 0.5 },
                        hovertemplate: `${PADD_LABELS[p.id]}<br>%{x}<br>%{y:.1f}M bbls<extra></extra>`,
                      };
                    })}
                      layout={{
                        height: 420, ...L,
                        yaxis: { title: "Millions of Barrels", gridcolor: t.grid },
                        xaxis: { gridcolor: t.grid },
                        hovermode: "x unified",
                        legend: { x: 0.01, y: 0.99, bgcolor: "transparent" },
                      }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  </div>
                  <div className="overflow-x-auto">
                    <table className="data-table text-xs w-full">
                      <thead><tr><th className="text-left">Region</th><th className="text-right">Now</th><th className="text-right">WoW</th><th className="text-right">5y %ile</th></tr></thead>
                      <tbody>
                        {rows.map(r => (
                          <tr key={r.id}>
                            <td className="font-semibold whitespace-nowrap"><span style={{ color: r.color }}>■</span> {r.label}</td>
                            <td className="font-data text-right">{r.mb.toFixed(1)}M</td>
                            <td className={`font-data text-right ${r.wow > 0 ? "text-loss" : "text-gain"}`}>{r.wow > 0 ? "+" : ""}{r.wow.toFixed(2)}M</td>
                            <td className="font-data text-right">{r.pct.toFixed(0)}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="text-xs text-text-muted mt-3">WoW colored on commercial-inventory convention: builds are bearish (red), draws are bullish (green). Percentile = empirical rank vs prior 260 weeks.</p>
                  </div>
                </div>
              </div>
            );
          })()}

          {/* Section 3: Strategic Petroleum Reserve */}
          {data.spr.length > 0 && (() => {
            const recs = tail(data.spr, 520);
            const l = last(recs);
            const sprMb = l.value / 1000;
            const wow = (l.wow_change ?? 0) / 1000;
            const peakRec = recs.reduce((a, b) => (b.value > a.value ? b : a), recs[0]);
            const troughRec = recs.reduce((a, b) => (b.value < a.value ? b : a), recs[0]);
            const peakMb = peakRec.value / 1000;
            const troughMb = troughRec.value / 1000;
            return (
              <div className="card space-y-3">
                <div className="text-sm font-semibold">Strategic Petroleum Reserve</div>
                <div className="flex flex-wrap gap-6">
                  <Metric label="SPR Stocks" value={`${sprMb.toFixed(1)}M bbls`}
                    delta={`${wow > 0 ? "+" : ""}${wow.toFixed(2)}M WoW`}
                    deltaType={wow >= 0 ? "gain" : "loss"} />
                  <Metric label="10-Year Peak" value={`${peakMb.toFixed(1)}M bbls`}
                    delta={peakRec.period} />
                  <Metric label="10-Year Trough" value={`${troughMb.toFixed(1)}M bbls`}
                    delta={troughRec.period} />
                  <Metric label="From Trough" value={`${(sprMb - troughMb).toFixed(1)}M bbls`}
                    delta={`${((sprMb - troughMb) / troughMb * 100).toFixed(1)}%`}
                    deltaType="gain" />
                </div>
                <Plot data={[{
                  x: recs.map(r => r.period),
                  y: recs.map(r => r.value / 1000),
                  type: "scatter" as const, mode: "lines" as const,
                  name: "SPR Stocks", line: { color: t.accent, width: 2 },
                  fill: "tozeroy" as const, fillcolor: t.accent + "14",
                  hovertemplate: "%{x}<br>%{y:.1f}M bbls<extra></extra>",
                }]}
                  layout={{
                    height: 360, ...L,
                    yaxis: { title: "Millions of Barrels", gridcolor: t.grid, rangemode: "tozero" as const },
                    xaxis: { gridcolor: t.grid },
                    hovermode: "x unified",
                    showlegend: false,
                  }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              </div>
            );
          })()}

          {/* Section 4: Days of Supply trend */}
          {data.supplied.length > 0 && (() => {
            // Align inventory and supplied by report period (both weekly).
            // EIA reports supplied as the 4-week avg of daily product supplied,
            // so DoS at period t = inv_t / supplied_t (same units cancel,
            // result in days).
            const suppliedMap = new Map(data.supplied.map(r => [r.period, r.value]));
            const dosSeries = inv
              .filter(r => {
                const s = suppliedMap.get(r.period);
                return s != null && s > 0;
              })
              .map(r => ({ period: r.period, dos: r.value / (suppliedMap.get(r.period) as number) }));
            if (dosSeries.length === 0) return null;

            // 5-year band of DoS by week-of-year.
            const dosByWeek = new Map<number, number[]>();
            for (const d of dosSeries) {
              const yr = yearOf(d.period);
              if (yr >= currentYear - 5 && yr <= currentYear - 1) {
                const w = weekOfYear(d.period);
                if (!dosByWeek.has(w)) dosByWeek.set(w, []);
                dosByWeek.get(w)!.push(d.dos);
              }
            }
            const dosStats = Array.from(dosByWeek.entries()).map(([week, vals]) => ({
              week,
              avg: vals.reduce((s, v) => s + v, 0) / vals.length,
              min: Math.min(...vals),
              max: Math.max(...vals),
            })).sort((a, b) => a.week - b.week);

            const currentYearDos = dosSeries
              .filter(d => yearOf(d.period) === currentYear)
              .map(d => ({ week: weekOfYear(d.period), value: d.dos }))
              .sort((a, b) => a.week - b.week);

            return (
              <div className="card space-y-2">
                <div className="text-sm font-semibold">Days of Supply — 5-Year Range</div>
                <Plot data={[
                  ...(dosStats.length > 0 ? [{
                    x: [...dosStats.map(s => s.week), ...dosStats.slice().reverse().map(s => s.week)],
                    y: [...dosStats.map(s => s.max), ...dosStats.slice().reverse().map(s => s.min)],
                    fill: "toself" as const, fillcolor: t.accent + "12", line: { color: "transparent", width: 0 },
                    name: "5-Year Range", hoverinfo: "skip" as const, type: "scatter" as const, mode: "lines" as const,
                  }, {
                    x: dosStats.map(s => s.week),
                    y: dosStats.map(s => s.avg),
                    type: "scatter" as const, mode: "lines" as const,
                    name: "5-Year Average", line: { color: t.spot, width: 2, dash: "dash" as const },
                  }] : []),
                  {
                    x: currentYearDos.map(d => d.week),
                    y: currentYearDos.map(d => d.value),
                    type: "scatter" as const, mode: "lines" as const,
                    name: `${currentYear} Actual`, line: { color: t.hv20, width: 2.5 },
                  },
                ]} layout={{
                  height: 360, ...L,
                  yaxis: { title: "Days of Supply", gridcolor: t.grid },
                  xaxis: { title: "Week of Year", gridcolor: t.grid, range: [1, 52], dtick: 4 },
                  hovermode: "x unified",
                  legend: { x: 0.01, y: 0.99, bgcolor: "transparent" },
                }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                <p className="text-xs text-text-muted">Commercial crude ÷ total product supplied (EIA WPSR demand proxy). Higher = looser market.</p>
              </div>
            );
          })()}
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

      {/* Tab 8: Global / OECD Stocks — STEO monthly, history + forecast */}
      {activeTab === 8 && (() => {
        const oecd = data.oecd_stocks;
        if (oecd.length === 0) {
          return <div className="card text-sm text-text-muted">Global / OECD STEO data unavailable.</div>;
        }
        // STEO carries an ~18-mo forecast tail. EIA doesn't tag actual vs
        // forecast in the seriesid feed, so we split on the calendar: periods
        // dated after today are forecast. STEO actuals themselves lag ~1-2
        // months, so the most recent "actual" months are early estimates —
        // noted in the captions.
        const todayISO = new Date().toISOString().slice(0, 10);
        const calYear = new Date().getFullYear();
        const isFcast = (p: string) => p > todayISO;

        /** Split a time series into solid-actual + dashed-forecast trace pair.
         * The forecast trace is seeded with the last actual point so the two
         * segments join without a visual gap. */
        const histFcast = (recs: EIARecord[], color: string, name: string) => {
          const s = recs.slice().sort((a, b) => (a.period < b.period ? -1 : 1));
          const act = s.filter(r => !isFcast(r.period));
          const fc = s.filter(r => isFcast(r.period));
          if (act.length > 0 && fc.length > 0) fc.unshift(act[act.length - 1]);
          return [
            { x: act.map(r => r.period), y: act.map(r => r.value), type: "scatter" as const, mode: "lines" as const,
              name, line: { color, width: 2 } },
            { x: fc.map(r => r.period), y: fc.map(r => r.value), type: "scatter" as const, mode: "lines" as const,
              name: `${name} (forecast)`, line: { color, width: 2, dash: "dot" as const }, showlegend: false,
              hovertemplate: "%{x}<br>%{y:.2f} (forecast)<extra></extra>" },
          ];
        };
        const fcastStart = oecd.find(r => isFcast(r.period))?.period;

        return (
          <div className="space-y-5">
            {/* Data caveat — applies to every section on this tab. */}
            <div className="card card-compact text-xs text-text-secondary border-warn/40 bg-warn-bg">
              <span className="font-semibold text-warn">Data note</span> — EIA STEO&apos;s most recent ~6 months of world
              supply are preliminary and typically revised upward, so monthly balances near the present overstate inventory
              draws. Headline balances below use annual averages; the OECD level and monthly charts show raw STEO values.
            </div>

            {/* Section A: OECD commercial inventory — seasonal band + forecast */}
            {(() => {
              // Seasonal band from the prior 5 complete calendar years.
              const byMonth = new Map<number, number[]>();
              for (const r of oecd) {
                const y = yearOf(r.period);
                if (y >= calYear - 5 && y <= calYear - 1) {
                  const m = monthOf(r.period);
                  if (!byMonth.has(m)) byMonth.set(m, []);
                  byMonth.get(m)!.push(r.value);
                }
              }
              const band = Array.from(byMonth.entries()).map(([month, vals]) => ({
                month, avg: vals.reduce((s, v) => s + v, 0) / vals.length, min: Math.min(...vals), max: Math.max(...vals),
              })).sort((a, b) => a.month - b.month);

              const cyr = oecd.filter(r => yearOf(r.period) === calYear)
                .map(r => ({ month: monthOf(r.period), value: r.value, fc: isFcast(r.period) }))
                .sort((a, b) => a.month - b.month);
              const cyrAct = cyr.filter(d => !d.fc);
              const cyrFc = cyr.filter(d => d.fc);
              if (cyrAct.length > 0 && cyrFc.length > 0) cyrFc.unshift(cyrAct[cyrAct.length - 1]);

              // Metrics: latest actual, MoM, YoY, vs 5-yr avg for that month.
              const actual = oecd.filter(r => !isFcast(r.period));
              const latestA = actual[actual.length - 1];
              const prevA = actual[actual.length - 2];
              const mom = latestA && prevA ? latestA.value - prevA.value : null;
              const latestMonth = latestA ? monthOf(latestA.period) : null;
              const latestY = latestA ? yearOf(latestA.period) : null;
              const yoyRec = actual.find(r => monthOf(r.period) === latestMonth && yearOf(r.period) === (latestY ?? 0) - 1);
              const yoy = latestA && yoyRec ? latestA.value - yoyRec.value : null;
              const bandNow = band.find(b => b.month === latestMonth);
              const vsAvg = latestA && bandNow ? latestA.value - bandNow.avg : null;

              return (
                <div className="card space-y-3">
                  <div className="text-sm font-semibold">OECD Commercial Inventory vs 5-Year Range</div>
                  <div className="flex flex-wrap gap-6">
                    {latestA && (
                      <Metric label={`OECD Stocks (${latestA.period.slice(0, 7)})`} value={`${latestA.value.toFixed(0)}M bbls`}
                        delta={mom != null ? `${mom > 0 ? "+" : ""}${mom.toFixed(0)}M MoM` : undefined}
                        deltaType={mom != null ? (mom > 0 ? "gain" : "loss") : undefined} />
                    )}
                    {yoy != null && <Metric label="YoY Change" value={`${yoy > 0 ? "+" : ""}${yoy.toFixed(0)}M bbls`}
                      delta={`${(yoy / (yoyRec!.value) * 100).toFixed(1)}%`} deltaType={yoy > 0 ? "gain" : "loss"} />}
                    {vsAvg != null && <Metric label="vs 5-Year Average" value={`${Math.abs(vsAvg).toFixed(0)}M ${vsAvg > 0 ? "Above" : "Below"}`} />}
                  </div>
                  <Plot data={[
                    ...(band.length > 0 ? [{
                      x: [...band.map(s => s.month), ...band.slice().reverse().map(s => s.month)],
                      y: [...band.map(s => s.max), ...band.slice().reverse().map(s => s.min)],
                      fill: "toself" as const, fillcolor: t.accent + "12", line: { color: "transparent", width: 0 },
                      name: "5-Year Range", hoverinfo: "skip" as const, type: "scatter" as const, mode: "lines" as const,
                    }, {
                      x: band.map(s => s.month), y: band.map(s => s.avg), type: "scatter" as const, mode: "lines" as const,
                      name: "5-Year Average", line: { color: t.spot, width: 2, dash: "dash" as const },
                    }] : []),
                    ...(cyrAct.length > 0 ? [{ x: cyrAct.map(d => d.month), y: cyrAct.map(d => d.value), type: "scatter" as const, mode: "lines" as const,
                      name: `${calYear} Actual`, line: { color: t.hv20, width: 2.5 } }] : []),
                    ...(cyrFc.length > 1 ? [{ x: cyrFc.map(d => d.month), y: cyrFc.map(d => d.value), type: "scatter" as const, mode: "lines" as const,
                      name: `${calYear} STEO Forecast`, line: { color: t.hv20, width: 2.5, dash: "dot" as const } }] : []),
                  ]} layout={{
                    height: 440, ...L,
                    yaxis: { title: "Millions of Barrels", gridcolor: t.grid },
                    xaxis: { title: "Month", gridcolor: t.grid, tickmode: "array" as const, tickvals: MONTH_ABBR.map((_, i) => i).slice(1), ticktext: MONTH_ABBR.slice(1), range: [0.5, 12.5] },
                    hovermode: "x unified", legend: { x: 0.01, y: 0.99, bgcolor: "transparent" },
                  }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  <p className="text-xs text-text-muted">OECD end-of-period commercial crude + liquids stocks (EIA STEO, PASC_OECD_T3). Dotted = STEO forecast; actuals lag ~1-2 months. Below the band = tight global market.</p>
                </div>
              );
            })()}

            {/* Section B: World supply vs demand balance */}
            {data.world_production.length > 0 && data.world_consumption.length > 0 && (() => {
              const cut = (recs: EIARecord[]) => recs.filter(r => yearOf(r.period) >= calYear - 7);
              const prod = cut(data.world_production);
              const cons = cut(data.world_consumption);
              const crude = cut(data.world_crude);
              // Headline uses ANNUAL averages, not the boundary month: STEO's
              // most recent supply months are preliminary/understated, so a
              // single-month balance overstates draws. Show the last full actual
              // year + the forecast year.
              const annualAvg = (recs: EIARecord[], yr: number): number | null => {
                const v = recs.filter(r => yearOf(r.period) === yr).map(r => r.value);
                return v.length > 0 ? v.reduce((a, b) => a + b, 0) / v.length : null;
              };
              const actualYear = calYear - 1; // last complete calendar year of actuals
              const fcastYear = yearOf(data.world_production[data.world_production.length - 1].period);
              const pA = annualAvg(prod, actualYear), cA = annualAvg(cons, actualYear);
              const balA = pA != null && cA != null ? pA - cA : null;
              const pF = annualAvg(prod, fcastYear), cF = annualAvg(cons, fcastYear);
              const balF = pF != null && cF != null ? pF - cF : null;
              return (
                <div className="card space-y-3">
                  <div className="text-sm font-semibold">World Supply vs Demand Balance</div>
                  <div className="flex flex-wrap gap-6">
                    {pA != null && <Metric label={`World Production (${actualYear})`} value={`${pA.toFixed(1)} mb/d`} delta="annual avg" />}
                    {cA != null && <Metric label={`World Consumption (${actualYear})`} value={`${cA.toFixed(1)} mb/d`} delta="annual avg" />}
                    {balA != null && <Metric label={`Implied Balance (${actualYear})`} value={`${balA > 0 ? "+" : ""}${balA.toFixed(2)} mb/d`}
                      delta={balA > 0 ? "Surplus (build)" : "Deficit (draw)"} deltaType={balA > 0 ? "loss" : "gain"} />}
                    {balF != null && <Metric label={`Balance (${fcastYear}F)`} value={`${balF > 0 ? "+" : ""}${balF.toFixed(2)} mb/d`}
                      delta={balF > 0 ? "Surplus" : "Deficit"} deltaType={balF > 0 ? "loss" : "gain"} />}
                  </div>
                  <Plot data={[
                    // Shaded gap between supply and demand (drawn first, behind
                    // the lines). tonexty fills prod→cons; single neutral fill
                    // since the sign flips over the series.
                    { x: cons.map(r => r.period), y: cons.map(r => r.value), type: "scatter" as const, mode: "lines" as const,
                      line: { width: 0, color: "transparent" }, hoverinfo: "skip" as const, showlegend: false },
                    { x: prod.map(r => r.period), y: prod.map(r => r.value), type: "scatter" as const, mode: "lines" as const,
                      fill: "tonexty" as const, fillcolor: t.accent + "14", line: { width: 0, color: "transparent" },
                      hoverinfo: "skip" as const, showlegend: false },
                    ...histFcast(prod, t.gain, "Total Liquids Production"),
                    ...histFcast(cons, t.spot, "Total Liquids Consumption"),
                    ...(crude.length > 0 ? [{
                      x: crude.filter(r => !isFcast(r.period)).map(r => r.period),
                      y: crude.filter(r => !isFcast(r.period)).map(r => r.value),
                      type: "scatter" as const, mode: "lines" as const, name: "Crude Production",
                      line: { color: t.muted, width: 1.2 },
                    }] : []),
                  ]} layout={{
                    height: 440, ...L,
                    yaxis: { title: "Million Barrels / Day", gridcolor: t.grid },
                    xaxis: { gridcolor: t.grid },
                    hovermode: "x unified", legend: { x: 0.01, y: 0.99, bgcolor: "transparent" },
                    shapes: fcastStart ? [{ type: "rect" as const, xref: "x" as const, yref: "paper" as const,
                      x0: fcastStart, x1: prod[prod.length - 1]?.period, y0: 0, y1: 1,
                      fillcolor: t.muted + "10", line: { width: 0 }, layer: "below" as const }] : [],
                    annotations: fcastStart ? [{ x: fcastStart, y: 1, yref: "paper" as const, yanchor: "bottom" as const,
                      text: "STEO forecast →", showarrow: false, font: { size: 9, color: t.muted } }] : [],
                  }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  <p className="text-xs text-text-muted">Total petroleum &amp; other liquids (EIA STEO). Dotted = forecast; shaded = forecast horizon. Production &gt; consumption builds global stocks. The production dip around the present reflects preliminary, not-yet-complete monthly data and is typically revised up.</p>
                </div>
              );
            })()}

            {/* Section C: Implied global builds / draws */}
            {data.world_stock_change.length > 0 && (() => {
              // T3_STCHANGE_WORLD is net inventory *withdrawals* (mb/d): positive
              // = draw. Flip the sign so the bar shows the inventory *change* —
              // positive bar = build (oversupply, bearish), negative = draw.
              const recs = data.world_stock_change.filter(r => yearOf(r.period) >= calYear - 5);
              const builds = recs.map(r => ({ period: r.period, change: -r.value, fc: isFcast(r.period) }));
              // Disjoint actual / forecast x positions → overlay so each month
              // shows a single bar; forecast bars faded.
              const bAct = builds.filter(b => !b.fc);
              const bFc = builds.filter(b => b.fc);
              return (
                <div className="card space-y-2">
                  <div className="text-sm font-semibold">Implied Global Builds / Draws</div>
                  <Plot data={[
                    { x: bAct.map(b => b.period), y: bAct.map(b => b.change), type: "bar" as const, name: "Actual",
                      marker: { color: bAct.map(b => (b.change > 0 ? t.loss : t.gain)) },
                      hovertemplate: "%{x}<br>%{y:+.2f} mb/d<extra></extra>" },
                    { x: bFc.map(b => b.period), y: bFc.map(b => b.change), type: "bar" as const, name: "Forecast",
                      marker: { color: bFc.map(b => (b.change > 0 ? t.loss : t.gain)), opacity: 0.45 },
                      hovertemplate: "%{x}<br>%{y:+.2f} mb/d (forecast)<extra></extra>" },
                  ]} layout={{
                    height: 360, ...L, barmode: "overlay" as const,
                    yaxis: { title: "Stock Change (mb/d)", gridcolor: t.grid },
                    xaxis: { gridcolor: t.grid },
                    hovermode: "x unified", showlegend: false,
                    shapes: [
                      { type: "line" as const, y0: 0, y1: 0, x0: builds[0]?.period, x1: builds[builds.length - 1]?.period, line: { color: t.muted, width: 1 } },
                      ...(fcastStart ? [{ type: "rect" as const, xref: "x" as const, yref: "paper" as const,
                        x0: fcastStart, x1: builds[builds.length - 1]?.period, y0: 0, y1: 1,
                        fillcolor: t.muted + "10", line: { width: 0 }, layer: "below" as const }] : []),
                    ],
                  }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  <p className="text-xs text-text-muted">Implied world inventory change = production − consumption (EIA STEO T3 balance). Red = build (bearish), green = draw (bullish). Faded bars = forecast. Bars around the present overstate draws — recent supply data is preliminary (see note above).</p>
                </div>
              );
            })()}
          </div>
        );
      })()}
    </div>
  );
}
