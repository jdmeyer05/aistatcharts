"use client";

import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchNatGasBundle, type EIARecord } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { useState, useMemo } from "react";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Storage + 5-Year Band", "Year-over-Year", "Injections / Withdrawals", "Regional Breakdown", "Henry Hub Overlay"];

const REGION_COLORS: Record<string, string> = {
  East: "#00d1ff",
  Midwest: "#3fb950",
  Mountain: "#f59e0b",
  Pacific: "#a78bfa",
  "South Central": "#f85149",
};

function weekOfYear(dateStr: string): number {
  const d = new Date(dateStr + "T12:00:00");
  const jan1 = new Date(d.getFullYear(), 0, 1);
  return Math.ceil(((d.getTime() - jan1.getTime()) / 86400000 + jan1.getDay() + 1) / 7);
}

function yearOf(dateStr: string): number {
  return new Date(dateStr + "T12:00:00").getFullYear();
}

export default function NatGasFundamentals() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const [activeTab, setActiveTab] = useState(0);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["natgas-bundle"],
    queryFn: fetchNatGasBundle,
    staleTime: 30 * 60 * 1000,
  });

  const computed = useMemo(() => {
    if (!data || data.storage.length === 0) return null;

    const storage = data.storage;
    const latest = storage[storage.length - 1];
    const storageBcf = latest.value;
    const wowBcf = latest.wow_change ?? 0;
    const flowType = wowBcf > 0 ? "Injection" : "Withdrawal";

    const currentYear = yearOf(latest.period);
    const byWeek = new Map<number, number[]>();
    for (const r of storage) {
      const yr = yearOf(r.period);
      if (yr >= currentYear - 5 && yr <= currentYear - 1) {
        const w = weekOfYear(r.period);
        if (!byWeek.has(w)) byWeek.set(w, []);
        byWeek.get(w)!.push(r.value);
      }
    }
    const fiveYrStats = Array.from(byWeek.entries()).map(([week, vals]) => ({
      week,
      avg: vals.reduce((s, v) => s + v, 0) / vals.length,
      min: Math.min(...vals),
      max: Math.max(...vals),
    })).sort((a, b) => a.week - b.week);

    const currentWeek = weekOfYear(latest.period);
    const avgNow = fiveYrStats.find(s => s.week === currentWeek);
    const deltaVsAvg = avgNow ? storageBcf - avgNow.avg : null;

    let daysOfSupply: number | null = null;
    if (data.consumption.length > 0) {
      const latestCons = data.consumption[data.consumption.length - 1];
      const consDate = new Date(latestCons.period + "T12:00:00");
      const daysInMonth = new Date(consDate.getFullYear(), consDate.getMonth() + 1, 0).getDate();
      const dailyBcf = latestCons.value / 1000 / daysInMonth;
      if (dailyBcf > 0) daysOfSupply = Math.round(storageBcf / dailyBcf);
    }

    const recentStorage = storage.filter(r => yearOf(r.period) >= currentYear - 4);
    const years = [...new Set(storage.map(r => yearOf(r.period)))].sort().slice(-5);

    return { latest, storageBcf, wowBcf, flowType, fiveYrStats, currentYear, deltaVsAvg, daysOfSupply, recentStorage, years, storage };
  }, [data]);

  if (isLoading) {
    return (
      <div className="space-y-5">
        <div><h1 className="text-2xl font-bold tracking-tight">Natural Gas Fundamentals</h1></div>
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
        <div><h1 className="text-2xl font-bold tracking-tight">Natural Gas Fundamentals</h1></div>
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm">
          EIA data unavailable. Check EIA_API_KEY configuration.
        </div>
      </div>
    );
  }

  const { latest, storageBcf, wowBcf, flowType, fiveYrStats, currentYear, deltaVsAvg, daysOfSupply, recentStorage, years, storage } = computed;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Natural Gas Fundamentals</h1>
        <p className="text-text-secondary text-sm mt-1">
          Weekly EIA Working Gas in Underground Storage — report date: {latest.period}
        </p>
      </div>

      {/* Metrics Row */}
      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          <Metric label="Lower 48 Working Gas" value={`${storageBcf.toLocaleString(undefined, { maximumFractionDigits: 0 })} Bcf`} />
          <Metric label={`Weekly Net ${flowType}`} value={`${wowBcf > 0 ? "+" : ""}${wowBcf.toFixed(0)} Bcf`}
            deltaType={wowBcf > 0 ? "loss" : "gain"} />
          {deltaVsAvg != null && (
            <Metric label="vs 5-Year Average"
              value={`${Math.abs(deltaVsAvg).toFixed(0)} Bcf ${deltaVsAvg > 0 ? "Above" : "Below"}`}
              delta={`${deltaVsAvg > 0 ? "+" : ""}${deltaVsAvg.toFixed(0)} Bcf`}
              deltaType={deltaVsAvg > 0 ? "loss" : "gain"} />
          )}
          {daysOfSupply != null && (
            <Metric label="Implied Days of Supply" value={`${daysOfSupply} days`} />
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
        {TABS.map((tab, i) => (
          <button key={tab} onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
              activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"
            }`}>
            {tab}
          </button>
        ))}
      </div>

      {/* Tab 0: Storage + 5-Year Band */}
      {activeTab === 0 && (
        <div className="card">
          <Plot
            data={[
              ...(fiveYrStats.length > 0 ? [{
                x: [...fiveYrStats.map(s => `${currentYear}-W${String(s.week).padStart(2, "0")}`),
                    ...fiveYrStats.slice().reverse().map(s => `${currentYear}-W${String(s.week).padStart(2, "0")}`)],
                y: [...fiveYrStats.map(s => s.max), ...fiveYrStats.slice().reverse().map(s => s.min)],
                fill: "toself" as const, fillcolor: t.accent + "12",
                line: { color: "transparent", width: 0 },
                name: "5-Year Range", hoverinfo: "skip" as const, type: "scatter" as const, mode: "lines" as const,
              }] : []),
              ...(fiveYrStats.length > 0 ? [{
                x: fiveYrStats.map(s => `${currentYear}-W${String(s.week).padStart(2, "0")}`),
                y: fiveYrStats.map(s => s.avg),
                type: "scatter" as const, mode: "lines" as const,
                name: "5-Year Average", line: { color: t.spot, width: 2, dash: "dash" as const },
              }] : []),
              {
                x: recentStorage.map(r => r.period),
                y: recentStorage.map(r => r.value),
                type: "scatter" as const, mode: "lines" as const,
                name: "Actual Storage", line: { color: t.loss, width: 2.5 },
              },
            ]}
            layout={{
              height: 500, ...L,
              yaxis: { title: "Billion Cubic Feet (Bcf)", gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              hovermode: "x unified",
              legend: { x: 0.01, y: 0.99, bgcolor: "transparent" },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* Tab 1: Year-over-Year Seasonality */}
      {activeTab === 1 && (
        <div className="card">
          <Plot
            data={years.map((yr, i) => {
              const yrData = storage.filter(r => yearOf(r.period) === yr)
                .map(r => ({ week: weekOfYear(r.period), value: r.value }))
                .sort((a, b) => a.week - b.week);
              const isCurrent = yr === currentYear;
              const colors = [t.loss, t.accent, t.gain, t.spot, t.hv60];
              return {
                x: yrData.map(d => d.week),
                y: yrData.map(d => d.value),
                type: "scatter" as const, mode: "lines" as const,
                name: String(yr),
                line: { color: colors[i % colors.length], width: isCurrent ? 3 : 1.5 },
                opacity: isCurrent ? 1 : 0.7,
              };
            })}
            layout={{
              height: 500, ...L,
              xaxis: { title: "Week of Year", gridcolor: t.grid },
              yaxis: { title: "Billion Cubic Feet (Bcf)", gridcolor: t.grid },
              hovermode: "x unified",
              legend: { x: 0.01, y: 0.99, bgcolor: "transparent" },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* Tab 2: Injections / Withdrawals */}
      {activeTab === 2 && (() => {
        const plotData = storage.slice(-260);
        return (
          <div className="card">
            <Plot
              data={[{
                x: plotData.map(r => r.period),
                y: plotData.map(r => r.wow_change ?? 0),
                type: "bar" as const,
                marker: { color: plotData.map(r => (r.wow_change ?? 0) > 0 ? t.gain : t.loss) },
                hovertemplate: "Date: %{x}<br>Net Flow: %{y:.0f} Bcf<extra></extra>",
              }]}
              layout={{
                height: 500, ...L,
                yaxis: { title: "Net Change (Bcf)", gridcolor: t.grid },
                xaxis: { gridcolor: t.grid },
                shapes: [{
                  type: "line", y0: 0, y1: 0,
                  x0: plotData[0]?.period, x1: plotData[plotData.length - 1]?.period,
                  line: { color: t.muted, width: 1 },
                }],
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        );
      })()}

      {/* Tab 3: Regional Breakdown */}
      {activeTab === 3 && (
        <div className="card space-y-4">
          <Plot
            data={Object.entries(data.regions).filter(([, recs]) => recs.length > 0).map(([name, recs]) => ({
              x: recs.map(r => r.period),
              y: recs.map(r => r.value),
              type: "scatter" as const, mode: "lines" as const,
              name, stackgroup: "one",
              line: { color: REGION_COLORS[name] ?? t.muted, width: 0.5 },
            }))}
            layout={{
              height: 500, ...L,
              yaxis: { title: "Billion Cubic Feet (Bcf)", gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              hovermode: "x unified",
              legend: { x: 0.01, y: 0.99, bgcolor: "transparent" },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
          <div className="overflow-x-auto">
            <table className="data-table text-xs">
              <thead><tr><th>Region</th><th>Storage (Bcf)</th><th>Weekly Change</th></tr></thead>
              <tbody>
                {Object.entries(data.regions).filter(([, recs]) => recs.length > 0).map(([name, recs]) => {
                  const last = recs[recs.length - 1];
                  return (
                    <tr key={name}>
                      <td className="font-semibold">{name}</td>
                      <td className="font-data">{last.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                      <td className={`font-data ${(last.wow_change ?? 0) > 0 ? "text-gain" : "text-loss"}`}>
                        {(last.wow_change ?? 0) > 0 ? "+" : ""}{(last.wow_change ?? 0).toFixed(0)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Tab 4: Henry Hub Price Overlay */}
      {activeTab === 4 && (
        <div className="card space-y-4">
          {data.henry_hub.length > 0 ? (
            <>
              <Plot
                data={[
                  {
                    x: storage.slice(-260).map(r => r.period),
                    y: storage.slice(-260).map(r => r.value),
                    type: "scatter" as const, mode: "lines" as const,
                    name: "Working Gas Storage", line: { color: t.loss, width: 2 },
                    yaxis: "y",
                  },
                  {
                    x: data.henry_hub.slice(-260).map(r => r.period),
                    y: data.henry_hub.slice(-260).map(r => r.value),
                    type: "scatter" as const, mode: "lines" as const,
                    name: "Henry Hub Spot ($/MMBtu)", line: { color: t.gain, width: 2 },
                    yaxis: "y2",
                  },
                ]}
                layout={{
                  height: 500, ...L,
                  hovermode: "x unified",
                  yaxis: { title: "Storage (Bcf)", side: "left", showgrid: false, color: t.text },
                  yaxis2: { title: "Henry Hub ($/MMBtu)", side: "right", overlaying: "y", showgrid: false, color: t.text },
                  legend: { x: 0.01, y: 0.99, bgcolor: "transparent" },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
              {(() => {
                const hh = data.henry_hub;
                const hhLatest = hh[hh.length - 1];
                const hhPrev = hh.length > 1 ? hh[hh.length - 2] : hhLatest;
                const hhChange = hhLatest.value - hhPrev.value;
                return (
                  <div className="flex gap-6">
                    <Metric label="Henry Hub Spot" value={`$${hhLatest.value.toFixed(2)}/MMBtu`}
                      delta={`${hhChange > 0 ? "+" : ""}$${hhChange.toFixed(2)}`}
                      deltaType={hhChange > 0 ? "gain" : "loss"} />
                  </div>
                );
              })()}
            </>
          ) : (
            <p className="text-sm text-text-muted">Henry Hub price data unavailable.</p>
          )}
        </div>
      )}
    </div>
  );
}
