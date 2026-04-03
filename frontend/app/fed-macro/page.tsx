"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchFredBatch, fetchPriceHistoryBatch } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Signal Matrix", "Inflation", "Labor Market", "Yield Curve", "Fed Funds"];

const INDICATORS: Record<string, { name: string; unit: string; color: string; category: string }> = {
  CPIAUCSL: { name: "CPI (All Items)", unit: "index", color: "#f85149", category: "Inflation" },
  PCEPILFE: { name: "Core PCE", unit: "index", color: "#f59e0b", category: "Inflation" },
  UNRATE: { name: "Unemployment", unit: "%", color: "#00d1ff", category: "Employment" },
  PAYEMS: { name: "Nonfarm Payrolls", unit: "K", color: "#3fb950", category: "Employment" },
  FEDFUNDS: { name: "Fed Funds Rate", unit: "%", color: "#a78bfa", category: "Fed" },
  T10Y2Y: { name: "2s10s Spread", unit: "%", color: "#ff69b4", category: "Rates" },
  DGS10: { name: "10Y Treasury", unit: "%", color: "#58a6ff", category: "Rates" },
  DGS2: { name: "2Y Treasury", unit: "%", color: "#79c0ff", category: "Rates" },
  UMCSENT: { name: "Consumer Sentiment", unit: "index", color: "#f59e0b", category: "Consumer" },
  RSAFS: { name: "Retail Sales", unit: "$M", color: "#3fb950", category: "Consumer" },
  ICSA: { name: "Initial Claims", unit: "K", color: "#f85149", category: "Employment" },
  INDPRO: { name: "Industrial Production", unit: "index", color: "#8b949e", category: "Production" },
};

const SERIES_IDS = Object.keys(INDICATORS);

export default function FedMacroPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [fredData, setFredData] = useState<Record<string, Record<string, unknown>[]>>({});

  const load = useMutation({
    mutationFn: async () => {
      const [fred, yields] = await Promise.all([
        fetchFredBatch(SERIES_IDS, 120),
        fetchPriceHistoryBatch(["^TNX", "^FVX", "^IRX", "^TYX"], 252),
      ]);
      return { fred, yields };
    },
    onSuccess: (d) => setFredData(d.fred),
  });

  const latest = useMemo(() => {
    const result: Record<string, { value: number; prev: number; date: string }> = {};
    for (const [sid, records] of Object.entries(fredData)) {
      if (records.length > 0) {
        const last = records[records.length - 1];
        const prev = records.length > 1 ? records[records.length - 2] : last;
        result[sid] = {
          value: (last.value as number) ?? 0,
          prev: (prev.value as number) ?? 0,
          date: (last.date as string) ?? (last.period as string) ?? "",
        };
      }
    }
    return result;
  }, [fredData]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Fed & Macro Drivers</h1>
        <p className="text-text-secondary text-sm mt-1">Key economic indicators the Fed watches when setting monetary policy.</p>
      </div>

      <div className="card card-compact">
        <button onClick={() => load.mutate()} disabled={load.isPending}
          className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
          {load.isPending ? "Fetching FRED data..." : "Load Macro Dashboard"}
        </button>
      </div>

      {load.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching {SERIES_IDS.length} FRED series...</p>
        </div>
      )}

      {Object.keys(fredData).length > 0 && (
        <>
          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
            {TABS.map((tab, i) => (
              <button key={tab} onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
                  activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {tab}
              </button>
            ))}
          </div>

          {/* Tab 0: Signal Matrix */}
          {activeTab === 0 && (
            <div className="card">
              <div className="overflow-x-auto">
                <table className="data-table text-xs">
                  <thead><tr><th>Indicator</th><th>Category</th><th>Latest</th><th>Previous</th><th>Change</th><th>Date</th></tr></thead>
                  <tbody>
                    {Object.entries(INDICATORS).map(([sid, info]) => {
                      const d = latest[sid];
                      if (!d) return null;
                      const change = d.value - d.prev;
                      const unit = info.unit === "%" ? "%" : info.unit === "index" ? "" : ` ${info.unit}`;
                      return (
                        <tr key={sid}>
                          <td className="font-semibold" style={{ color: info.color }}>{info.name}</td>
                          <td className="text-text-muted">{info.category}</td>
                          <td className="font-data">{d.value.toLocaleString(undefined, { maximumFractionDigits: 2 })}{unit}</td>
                          <td className="font-data text-text-muted">{d.prev.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                          <td className={`font-data font-semibold ${change > 0 ? "text-gain" : change < 0 ? "text-loss" : ""}`}>
                            {change > 0 ? "+" : ""}{change.toFixed(2)}
                          </td>
                          <td className="text-text-muted">{d.date.slice(0, 10)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Tab 1: Inflation */}
          {activeTab === 1 && (
            <div className="card space-y-4">
              {["CPIAUCSL", "PCEPILFE"].map(sid => {
                const records = fredData[sid] ?? [];
                const info = INDICATORS[sid];
                if (records.length < 2) return null;
                return (
                  <div key={sid}>
                    <div className="text-sm font-bold mb-1">{info.name}</div>
                    <Plot data={[{
                      x: records.map(r => (r.date as string) ?? (r.period as string)),
                      y: records.map(r => r.value as number),
                      type: "scatter" as const, mode: "lines" as const,
                      line: { color: info.color, width: 2 },
                    }]} layout={{ height: 280, ...L, yaxis: { title: info.unit, gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  </div>
                );
              })}
            </div>
          )}

          {/* Tab 2: Labor Market */}
          {activeTab === 2 && (
            <div className="card space-y-4">
              {["UNRATE", "PAYEMS", "ICSA"].map(sid => {
                const records = fredData[sid] ?? [];
                const info = INDICATORS[sid];
                if (records.length < 2) return null;
                return (
                  <div key={sid}>
                    <div className="text-sm font-bold mb-1">{info.name}</div>
                    <Plot data={[{
                      x: records.map(r => (r.date as string) ?? (r.period as string)),
                      y: records.map(r => r.value as number),
                      type: sid === "PAYEMS" ? "bar" as const : "scatter" as const,
                      mode: sid === "PAYEMS" ? undefined : "lines" as const,
                      line: sid !== "PAYEMS" ? { color: info.color, width: 2 } : undefined,
                      marker: sid === "PAYEMS" ? { color: records.map(r => {
                        const v = r.value as number;
                        const idx = records.indexOf(r);
                        const prev = idx > 0 ? records[idx - 1].value as number : v;
                        return v > prev ? t.gain : t.loss;
                      }) } : undefined,
                    }]} layout={{ height: 280, ...L, yaxis: { title: info.unit, gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  </div>
                );
              })}
            </div>
          )}

          {/* Tab 3: Yield Curve */}
          {activeTab === 3 && (
            <div className="card space-y-4">
              {["DGS10", "DGS2", "T10Y2Y", "FEDFUNDS"].map(sid => {
                const records = fredData[sid] ?? [];
                const info = INDICATORS[sid];
                if (records.length < 2) return null;
                return (
                  <div key={sid}>
                    <div className="text-sm font-bold mb-1">{info.name}</div>
                    <Plot data={[{
                      x: records.map(r => (r.date as string) ?? (r.period as string)),
                      y: records.map(r => r.value as number),
                      type: "scatter" as const, mode: "lines" as const,
                      line: { color: info.color, width: 2 },
                      fill: sid === "T10Y2Y" ? "tozeroy" as const : undefined,
                      fillcolor: sid === "T10Y2Y" ? info.color + "15" : undefined,
                    }]} layout={{ height: 250, ...L, yaxis: { title: "%", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified",
                      ...(sid === "T10Y2Y" ? { shapes: [{ type: "line" as const, y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper" as const, line: { color: t.muted, width: 1 } }] } : {}) }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  </div>
                );
              })}
            </div>
          )}

          {/* Tab 4: Fed Funds */}
          {activeTab === 4 && (
            <div className="card space-y-4">
              {(() => {
                const records = fredData["FEDFUNDS"] ?? [];
                if (records.length < 2) return <p className="text-sm text-text-muted">No Fed Funds data.</p>;
                const l = latest["FEDFUNDS"];
                return (<>
                  <div className="flex gap-6">
                    <Metric label="Fed Funds Rate" value={`${l?.value.toFixed(2) ?? "—"}%`} />
                    <Metric label="10Y Treasury" value={`${latest["DGS10"]?.value.toFixed(2) ?? "—"}%`} />
                    <Metric label="2s10s Spread" value={`${latest["T10Y2Y"]?.value.toFixed(2) ?? "—"}%`}
                      deltaType={(latest["T10Y2Y"]?.value ?? 0) < 0 ? "loss" : "gain"} />
                  </div>
                  <Plot data={[{
                    x: records.map(r => (r.date as string) ?? (r.period as string)),
                    y: records.map(r => r.value as number),
                    type: "scatter" as const, mode: "lines" as const,
                    line: { color: INDICATORS.FEDFUNDS.color, width: 2 },
                    fill: "tozeroy" as const, fillcolor: INDICATORS.FEDFUNDS.color + "15",
                  }]} layout={{ height: 400, ...L, yaxis: { title: "Rate (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </>);
              })()}
            </div>
          )}
        </>
      )}
    </div>
  );
}
