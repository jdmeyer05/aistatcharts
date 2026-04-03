"use client";

import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchFuturesSnapshot, fetchPriceHistory, type FuturesItem } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { useState } from "react";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Heatmap & Snapshot", "Historical Charts", "Sector Performance"];

const SECTOR_COLORS: Record<string, string> = {
  Indices: "#00d1ff", Energy: "#ff9900", Metals: "#ffdd00",
  Rates: "#a78bfa", Agriculture: "#3fb950", FX: "#f85149",
};

function fmtPrice(p: number): string {
  if (p > 1000) return p.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (p > 10) return p.toFixed(2);
  return p.toFixed(4);
}

export default function FuturesDashboard() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [histTicker, setHistTicker] = useState<{ ticker: string; name: string } | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["futures-snapshot"],
    queryFn: fetchFuturesSnapshot,
    staleTime: 2 * 60 * 1000,
  });

  const { data: histData, isFetching: histLoading } = useQuery({
    queryKey: ["futures-hist", histTicker?.ticker],
    queryFn: () => histTicker ? fetchPriceHistory(histTicker.ticker, 180) : Promise.resolve({ ticker: "", data: [] }),
    enabled: !!histTicker,
    staleTime: 5 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="space-y-5">
        <div><h1 className="text-2xl font-bold tracking-tight">Futures Dashboard</h1></div>
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching futures data...</p>
        </div>
      </div>
    );
  }

  if (!data || Object.keys(data).length === 0) {
    return (
      <div className="space-y-5">
        <div><h1 className="text-2xl font-bold tracking-tight">Futures Dashboard</h1></div>
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Futures data unavailable.</div>
      </div>
    );
  }

  const sectors = Object.keys(data);
  const allItems: (FuturesItem & { sector: string })[] = sectors.flatMap(s => (data[s] || []).map(item => ({ ...item, sector: s })));

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Futures Dashboard</h1>
        <p className="text-text-secondary text-sm mt-1">Real-time futures snapshot across indices, energy, metals, rates, agriculture, and FX.</p>
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

      {/* Tab 0: Heatmap + Snapshot */}
      {activeTab === 0 && (
        <div className="space-y-5">
          {/* Heatmap */}
          {(() => {
            const maxContracts = Math.max(...sectors.map(s => (data[s] || []).length));
            const zVals: (number | null)[][] = [];
            const annotText: string[][] = [];
            const yLabels: string[] = [];

            for (const sector of sectors) {
              const items = [...(data[sector] || [])].sort((a, b) => b.pct_change - a.pct_change);
              const rowZ: (number | null)[] = [];
              const rowAnn: string[] = [];
              for (let i = 0; i < maxContracts; i++) {
                if (i < items.length) {
                  rowZ.push(items[i].pct_change);
                  rowAnn.push(`${items[i].name}<br>${fmtPrice(items[i].price)}<br>${items[i].pct_change >= 0 ? "+" : ""}${items[i].pct_change.toFixed(2)}%`);
                } else {
                  rowZ.push(null);
                  rowAnn.push("");
                }
              }
              zVals.push(rowZ);
              annotText.push(rowAnn);
              yLabels.push(sector);
            }

            return (
              <div className="card">
                <Plot data={[{
                  type: "heatmap" as const,
                  z: zVals, y: yLabels,
                  colorscale: [[0, t.loss], [0.35, t.loss + "66"], [0.5, t.grid], [0.65, t.gain + "66"], [1, t.gain]],
                  zmid: 0, showscale: false, xgap: 3, ygap: 3,
                  text: annotText, texttemplate: "%{text}",
                  hovertemplate: "%{text}<extra></extra>",
                  textfont: { size: 11, color: t.text },
                }]}
                  layout={{
                    height: 420, ...L, margin: { l: 100, r: 10, t: 10, b: 10 },
                    xaxis: { visible: false }, yaxis: { autorange: "reversed", tickfont: { size: 12 } },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            );
          })()}

          {/* Per-sector metrics */}
          {sectors.map(sector => {
            const items = data[sector] || [];
            if (items.length === 0) return null;
            return (
              <div key={sector} className="card card-compact">
                <div className="text-xs font-bold uppercase tracking-wider mb-2" style={{ color: SECTOR_COLORS[sector] }}>{sector}</div>
                <div className="flex flex-wrap gap-6">
                  {items.map(item => (
                    <Metric key={item.ticker} label={item.name} value={fmtPrice(item.price)}
                      delta={`${item.pct_change >= 0 ? "+" : ""}${item.pct_change.toFixed(2)}%`}
                      deltaType={item.pct_change >= 0 ? "gain" : "loss"} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Tab 1: Historical Charts */}
      {activeTab === 1 && (
        <div className="card space-y-4">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-xs font-semibold text-text-muted">Select contract:</span>
            {allItems.slice(0, 20).map(item => (
              <button key={item.ticker} onClick={() => setHistTicker({ ticker: item.ticker, name: item.name })}
                className={`px-2 py-1 text-xs rounded ${histTicker?.ticker === item.ticker ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
                {item.name}
              </button>
            ))}
          </div>

          {histTicker && histLoading && <p className="text-sm text-text-muted">Loading {histTicker.name}...</p>}

          {histTicker && histData && histData.data.length > 0 && (
            <>
              <Plot data={[{
                x: histData.data.map(d => d.Date),
                y: histData.data.map(d => d.Close),
                type: "scatter" as const, mode: "lines" as const,
                name: histTicker.name,
                line: { color: SECTOR_COLORS[allItems.find(i => i.ticker === histTicker.ticker)?.sector ?? "Indices"] ?? t.accent, width: 2 },
              }]}
                layout={{ height: 400, ...L, yaxis: { title: histTicker.name, gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

              {/* Volume bars */}
              <Plot data={[{
                x: histData.data.map(d => d.Date),
                y: histData.data.map(d => d.Volume),
                type: "bar" as const,
                marker: { color: histData.data.map((d, i) => i > 0 && d.Close >= histData.data[i - 1].Close ? t.gain : t.loss), opacity: 0.6 },
              }]}
                layout={{ height: 150, ...L, margin: { l: 50, r: 20, t: 5, b: 30 }, yaxis: { title: "Volume", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, showlegend: false }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </>
          )}
        </div>
      )}

      {/* Tab 2: Sector Performance */}
      {activeTab === 2 && (
        <div className="card">
          {(() => {
            const sectorAvgs = sectors.map(s => {
              const items = data[s] || [];
              const avg = items.length > 0 ? items.reduce((sum, i) => sum + i.pct_change, 0) / items.length : 0;
              return { sector: s, avg };
            }).sort((a, b) => b.avg - a.avg);

            return (
              <Plot data={[{
                x: sectorAvgs.map(s => s.sector),
                y: sectorAvgs.map(s => s.avg),
                type: "bar" as const,
                marker: { color: sectorAvgs.map(s => s.avg >= 0 ? t.gain : t.loss) },
                text: sectorAvgs.map(s => `${s.avg >= 0 ? "+" : ""}${s.avg.toFixed(2)}%`),
                textposition: "outside" as const,
                textfont: { size: 11, color: t.text },
                hovertemplate: "%{x}: %{y:.2f}%<extra></extra>",
              }]}
                layout={{ height: 400, ...L, yaxis: { title: "Avg Daily Change (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            );
          })()}
        </div>
      )}
    </div>
  );
}
