"use client";

import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchAccuracySummary, fetchPredictions, fetchClosedPositions } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { useState } from "react";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Platform Scorecard", "Tool Breakdown", "Position Performance", "Prediction Log"];

const SOURCE_LABELS: Record<string, string> = {
  stock_analysis: "Stock Analysis (AI)", signal_scanner: "Signal Scanner",
  scenario_analysis: "Scenario Analysis", rl_trading: "RL Trading",
  analyst_consensus: "Analyst Consensus", vol_surface: "Vol Surface",
  options_flow: "Options Flow", calendar_scanner: "Calendar Spread",
};

export default function TrackRecordPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [logFilter, setLogFilter] = useState<string>("");

  const { data: accuracy, isLoading: accLoading } = useQuery({
    queryKey: ["accuracy-summary"],
    queryFn: fetchAccuracySummary,
    staleTime: 5 * 60 * 1000,
  });

  const { data: predictions } = useQuery({
    queryKey: ["predictions", logFilter],
    queryFn: () => fetchPredictions({ source: logFilter || undefined, limit: 200 }),
    staleTime: 5 * 60 * 1000,
  });

  const { data: closedPositions } = useQuery({
    queryKey: ["closed-positions"],
    queryFn: () => fetchClosedPositions(100),
    staleTime: 5 * 60 * 1000,
  });

  if (accLoading) {
    return (
      <div className="space-y-5">
        <div><h1 className="text-2xl font-bold tracking-tight">Track Record</h1></div>
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Loading track record...</p>
        </div>
      </div>
    );
  }

  const acc = accuracy ?? { total: 0, evaluated: 0, correct: 0, accuracy: 0, by_source: {} };
  const sources = Object.entries(acc.by_source);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Track Record</h1>
        <p className="text-text-secondary text-sm mt-1">Every prediction, every signal, every trade — measured against reality.</p>
      </div>

      {/* Hero metrics */}
      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          <Metric label="Total Predictions" value={String(acc.total)} />
          <Metric label="Evaluated" value={String(acc.evaluated)} />
          <Metric label="Correct" value={String(acc.correct)} />
          <Metric label="Overall Accuracy" value={`${(acc.accuracy * 100).toFixed(1)}%`}
            deltaType={acc.accuracy > 0.55 ? "gain" : acc.accuracy < 0.45 ? "loss" : "neutral"} />
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

      {/* Tab 0: Platform Scorecard */}
      {activeTab === 0 && (
        <div className="card space-y-4">
          {sources.length > 0 ? (
            <Plot data={[{
              x: sources.map(([src]) => SOURCE_LABELS[src] ?? src),
              y: sources.map(([, v]) => v.accuracy * 100),
              type: "bar" as const,
              marker: { color: sources.map(([, v]) => v.accuracy > 0.55 ? t.gain : v.accuracy < 0.45 ? t.loss : t.spot) },
              text: sources.map(([, v]) => `${(v.accuracy * 100).toFixed(1)}% (${v.correct}/${v.total})`),
              textposition: "outside" as const,
              textfont: { size: 10, color: t.text },
              hovertemplate: "%{x}: %{y:.1f}% accuracy<extra></extra>",
            }]}
              layout={{ height: 350, ...L, yaxis: { title: "Accuracy (%)", gridcolor: t.grid, range: [0, 100] }, xaxis: { gridcolor: t.grid },
                shapes: [{ type: "line", y0: 50, y1: 50, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1, dash: "dot" } }],
                annotations: [{ x: 1, y: 50, xref: "paper", text: "50% (coin flip)", showarrow: false, font: { size: 8, color: t.muted } }] }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
          ) : (
            <p className="text-sm text-text-muted">No evaluated predictions yet. Predictions are measured at T+30/60/90 days.</p>
          )}
        </div>
      )}

      {/* Tab 1: Tool Breakdown */}
      {activeTab === 1 && (
        <div className="card space-y-4">
          {sources.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Source</th><th>Total</th><th>Correct</th><th>Incorrect</th><th>Accuracy</th><th>Grade</th></tr></thead>
                <tbody>
                  {sources.sort(([, a], [, b]) => b.accuracy - a.accuracy).map(([src, v]) => {
                    const grade = v.accuracy >= 0.7 ? "A" : v.accuracy >= 0.6 ? "B" : v.accuracy >= 0.5 ? "C" : v.accuracy >= 0.4 ? "D" : "F";
                    return (
                      <tr key={src}>
                        <td className="font-semibold">{SOURCE_LABELS[src] ?? src}</td>
                        <td className="font-data">{v.total}</td>
                        <td className="font-data text-gain">{v.correct}</td>
                        <td className="font-data text-loss">{v.total - v.correct}</td>
                        <td className="font-data font-semibold">{(v.accuracy * 100).toFixed(1)}%</td>
                        <td><span className={`badge ${grade <= "B" ? "badge-gain" : grade === "C" ? "badge-warn" : "badge-loss"}`}>{grade}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : <p className="text-sm text-text-muted">No data yet.</p>}
        </div>
      )}

      {/* Tab 2: Position Performance */}
      {activeTab === 2 && (
        <div className="card space-y-4">
          {closedPositions && closedPositions.data.length > 0 ? (<>
            {(() => {
              const pnls = closedPositions.data.map(p => {
                const entry = (p.entry_price as number) ?? 0;
                const close = (p.close_price as number) ?? entry;
                const qty = (p.qty as number) ?? 0;
                return (close - entry) * qty;
              });
              const wins = pnls.filter(p => p > 0);
              const losses = pnls.filter(p => p < 0);
              const totalPnl = pnls.reduce((s, p) => s + p, 0);
              const winRate = pnls.length > 0 ? wins.length / pnls.length : 0;
              const profitFactor = losses.length > 0 ? Math.abs(wins.reduce((s, p) => s + p, 0) / losses.reduce((s, p) => s + p, 0)) : 0;
              return (
                <div className="flex flex-wrap gap-6">
                  <Metric label="Total P&L" value={`$${totalPnl.toFixed(0)}`} deltaType={totalPnl > 0 ? "gain" : "loss"} />
                  <Metric label="Win Rate" value={`${(winRate * 100).toFixed(1)}%`} />
                  <Metric label="Profit Factor" value={profitFactor.toFixed(2)} />
                  <Metric label="Trades" value={String(pnls.length)} />
                </div>
              );
            })()}
            <div className="overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Ticker</th><th>Type</th><th>Qty</th><th>Entry</th><th>Close</th><th>P&L</th></tr></thead>
                <tbody>
                  {closedPositions.data.slice(-30).reverse().map((p, i) => {
                    const pnl = ((p.close_price as number) - (p.entry_price as number)) * (p.qty as number);
                    return (
                      <tr key={i}>
                        <td className="font-semibold">{p.ticker as string}</td>
                        <td>{p.type as string}</td>
                        <td className="font-data">{p.qty as number}</td>
                        <td className="font-data">${(p.entry_price as number).toFixed(2)}</td>
                        <td className="font-data">${(p.close_price as number)?.toFixed(2) ?? "—"}</td>
                        <td className={`font-data font-semibold ${pnl > 0 ? "text-gain" : "text-loss"}`}>${pnl.toFixed(0)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>) : <p className="text-sm text-text-muted">No closed positions yet.</p>}
        </div>
      )}

      {/* Tab 3: Prediction Log */}
      {activeTab === 3 && (
        <div className="card space-y-4">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-text-muted">Filter:</span>
            <button onClick={() => setLogFilter("")} className={`px-2 py-1 text-xs rounded ${!logFilter ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>All</button>
            {Object.entries(SOURCE_LABELS).map(([key, label]) => (
              <button key={key} onClick={() => setLogFilter(key)}
                className={`px-2 py-1 text-xs rounded ${logFilter === key ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
                {label}
              </button>
            ))}
          </div>
          {predictions && predictions.data.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Date</th><th>Source</th><th>Ticker</th><th>Direction</th><th>Conviction</th><th>Status</th></tr></thead>
                <tbody>
                  {predictions.data.slice(-50).reverse().map((p, i) => (
                    <tr key={i}>
                      <td>{(p.timestamp as string)?.slice(0, 10) ?? "—"}</td>
                      <td>{SOURCE_LABELS[p.source as string] ?? (p.source as string)}</td>
                      <td className="font-semibold">{p.ticker as string}</td>
                      <td><span className={`badge ${(p.direction as string) === "bull" ? "badge-gain" : (p.direction as string) === "bear" ? "badge-loss" : "badge-warn"}`}>{p.direction as string}</span></td>
                      <td className="font-data">{((p.conviction as number) ?? 0).toFixed(1)}</td>
                      <td><span className={`badge ${(p.status as string) === "correct" ? "badge-gain" : (p.status as string) === "incorrect" ? "badge-loss" : "badge-info"}`}>{(p.status as string) ?? "pending"}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="text-sm text-text-muted">No predictions found.</p>}
        </div>
      )}
    </div>
  );
}
