"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchFredBatch, fetchAITradeIdeas, fetchPriceHistoryBatch } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Macro Dashboard", "AI Regime Analysis", "Historical Stress Tests", "Custom What-If"];

const KEY_INDICATORS = ["CPIAUCSL", "UNRATE", "FEDFUNDS", "T10Y2Y", "DGS10", "GDP", "PAYEMS", "UMCSENT"];

const STRESS_SCENARIOS = [
  { name: "2008 GFC", spy: -56.8, tlt: 33.7, gld: 5.8, desc: "Credit crisis + Lehman collapse" },
  { name: "2020 COVID", spy: -33.9, tlt: 21.5, gld: -3.1, desc: "Pandemic crash (Feb-Mar)" },
  { name: "2022 Rate Shock", spy: -25.4, tlt: -31.2, gld: -0.3, desc: "Fastest rate hikes in 40 years" },
  { name: "Dot-Com Bust", spy: -49.1, tlt: 18.0, gld: -5.2, desc: "Tech bubble collapse (2000-02)" },
  { name: "1987 Black Monday", spy: -33.5, tlt: 8.2, gld: 3.5, desc: "Single-day 22% crash" },
  { name: "Euro Crisis 2011", spy: -19.4, tlt: 33.5, gld: 10.2, desc: "PIIGS sovereign debt crisis" },
  { name: "Taper Tantrum 2013", spy: -5.8, tlt: -13.4, gld: -26.0, desc: "Bernanke signals tapering" },
  { name: "Oil Crash 2014", spy: -3.2, tlt: 27.0, gld: -1.7, desc: "Crude from $100 to $26" },
];

export default function ScenarioAnalysis() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [fredData, setFredData] = useState<Record<string, Record<string, unknown>[]>>({});
  const [aiContent, setAiContent] = useState("");
  const [whatIf, setWhatIf] = useState({ spy: 0, rates: 0, oil: 0, vix: 0 });

  const loadMacro = useMutation({
    mutationFn: () => fetchFredBatch(KEY_INDICATORS, 60),
    onSuccess: d => setFredData(d),
  });

  const loadAI = useMutation({
    mutationFn: async () => {
      const context = Object.entries(fredData).map(([sid, records]) => {
        const last = records[records.length - 1];
        return `${sid}: ${last?.value ?? "N/A"}`;
      }).join("\n");
      return fetchAITradeIdeas({
        ticker: "MACRO", style: "full_scan",
        context: `MACRO REGIME ANALYSIS\nCurrent economic indicators:\n${context}\n\nAnalyze the current macro regime. Identify: which regime are we in (Goldilocks, Stagflation, Recession, Soft Landing, Re-Acceleration)? What are the key risks? How should portfolios be positioned?`,
      });
    },
    onSuccess: d => setAiContent(d.content),
  });

  const latest = (sid: string) => {
    const records = fredData[sid] ?? [];
    return records.length > 0 ? records[records.length - 1] : null;
  };

  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-bold tracking-tight">Scenario Analysis</h1>
        <p className="text-text-secondary text-sm mt-1">Macro regime analysis, historical stress tests, and portfolio impact modeling.</p></div>

      <div className="card card-compact">
        <button onClick={() => loadMacro.mutate()} disabled={loadMacro.isPending}
          className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
          {loadMacro.isPending ? "Fetching macro data..." : "Load Macro Dashboard"}</button>
      </div>

      {loadMacro.isPending && <div className="card text-center py-12"><div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" /></div>}

      {Object.keys(fredData).length > 0 && (<>
        <div className="card card-compact">
          <div className="flex flex-wrap gap-6">
            {[["FEDFUNDS","Fed Funds","%"], ["UNRATE","Unemployment","%"], ["T10Y2Y","2s10s Spread","%"], ["DGS10","10Y Yield","%"], ["UMCSENT","Cons. Sentiment",""]].map(([sid, label, unit]) => {
              const d = latest(sid);
              return d ? <Metric key={sid} label={label} value={`${Number(d.value).toFixed(2)}${unit}`} /> : null;
            })}
          </div>
        </div>

        <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
          {TABS.map((tab, i) => (<button key={tab} onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>{tab}</button>))}
        </div>

        {/* Tab 0: Macro Dashboard */}
        {activeTab === 0 && (
          <div className="card space-y-4">
            {KEY_INDICATORS.map(sid => {
              const records = fredData[sid] ?? [];
              if (records.length < 2) return null;
              return (
                <div key={sid}>
                  <div className="text-xs font-bold mb-1">{sid}</div>
                  <Plot data={[{ x: records.map(r => (r.date as string) ?? (r.period as string)),
                    y: records.map(r => r.value as number), type: "scatter" as const, mode: "lines" as const,
                    line: { color: t.accent, width: 1.5 } }]}
                    layout={{ height: 180, ...L, margin: { l: 40, r: 10, t: 5, b: 25 }, yaxis: { gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </div>
              );
            })}
          </div>
        )}

        {/* Tab 1: AI Regime */}
        {activeTab === 1 && (
          <div className="card space-y-4">
            {!aiContent && <button onClick={() => loadAI.mutate()} disabled={loadAI.isPending}
              className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
              {loadAI.isPending ? "Analyzing..." : "Run AI Regime Analysis (Gemini)"}</button>}
            {loadAI.isPending && <div className="text-center py-8"><div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" /></div>}
            {aiContent && <div className="prose prose-sm max-w-none text-sm dark:prose-invert" dangerouslySetInnerHTML={{
              __html: aiContent.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
                .replace(/^## (.*?)$/gm, '<h3 class="text-base font-bold mt-4 mb-2">$1</h3>')
                .replace(/^#### (.*?)$/gm, '<h4 class="text-sm font-semibold mt-3 mb-1">$1</h4>')
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, "<br/>"),
            }} />}
          </div>
        )}

        {/* Tab 2: Historical Stress */}
        {activeTab === 2 && (
          <div className="card">
            <div className="overflow-x-auto"><table className="data-table text-xs">
              <thead><tr><th>Scenario</th><th>Description</th><th>S&P 500</th><th>Treasuries</th><th>Gold</th></tr></thead>
              <tbody>{STRESS_SCENARIOS.map(s => (
                <tr key={s.name}><td className="font-semibold">{s.name}</td><td className="text-text-muted">{s.desc}</td>
                  <td className={`font-data font-semibold ${s.spy>0?"text-gain":"text-loss"}`}>{s.spy>0?"+":""}{s.spy.toFixed(1)}%</td>
                  <td className={`font-data ${s.tlt>0?"text-gain":"text-loss"}`}>{s.tlt>0?"+":""}{s.tlt.toFixed(1)}%</td>
                  <td className={`font-data ${s.gld>0?"text-gain":"text-loss"}`}>{s.gld>0?"+":""}{s.gld.toFixed(1)}%</td></tr>
              ))}</tbody></table></div>
            <Plot data={STRESS_SCENARIOS.map(s => ({
              x: ["S&P 500","Treasuries","Gold"], y: [s.spy, s.tlt, s.gld], type: "bar" as const, name: s.name,
            }))} layout={{ height: 400, ...L, barmode: "group", yaxis: { title: "Drawdown (%)", gridcolor: t.grid },
              shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } }] }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
          </div>
        )}

        {/* Tab 3: Custom What-If */}
        {activeTab === 3 && (
          <div className="card space-y-4">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <div><label className="metric-label">S&P 500 Shock (%)</label>
                <input type="number" value={whatIf.spy} onChange={e => setWhatIf({...whatIf, spy: Number(e.target.value)})} step={5}
                  className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
              <div><label className="metric-label">Rate Change (bps)</label>
                <input type="number" value={whatIf.rates} onChange={e => setWhatIf({...whatIf, rates: Number(e.target.value)})} step={25}
                  className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
              <div><label className="metric-label">Oil Shock (%)</label>
                <input type="number" value={whatIf.oil} onChange={e => setWhatIf({...whatIf, oil: Number(e.target.value)})} step={10}
                  className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
              <div><label className="metric-label">VIX Change (pts)</label>
                <input type="number" value={whatIf.vix} onChange={e => setWhatIf({...whatIf, vix: Number(e.target.value)})} step={5}
                  className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
            </div>
            <div className="text-sm">
              <div className="font-bold mb-2">Estimated Portfolio Impact</div>
              <div className="flex flex-wrap gap-6">
                <Metric label="Equities" value={`${whatIf.spy > 0 ? "+" : ""}${whatIf.spy.toFixed(1)}%`} deltaType={whatIf.spy >= 0 ? "gain" : "loss"} />
                <Metric label="Bonds" value={`${(-whatIf.rates * 0.08) > 0 ? "+" : ""}${(-whatIf.rates * 0.08).toFixed(1)}%`} deltaType={-whatIf.rates >= 0 ? "gain" : "loss"} />
                <Metric label="Gold" value={`${(-whatIf.rates * 0.05 + whatIf.vix * 0.3) > 0 ? "+" : ""}${(-whatIf.rates * 0.05 + whatIf.vix * 0.3).toFixed(1)}%`} />
                <Metric label="60/40 Portfolio" value={`${(whatIf.spy * 0.6 + (-whatIf.rates * 0.08) * 0.4).toFixed(1)}%`}
                  deltaType={(whatIf.spy * 0.6 + (-whatIf.rates * 0.08) * 0.4) >= 0 ? "gain" : "loss"} />
              </div>
            </div>
          </div>
        )}
      </>)}
    </div>
  );
}
