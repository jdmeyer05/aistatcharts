"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchPriceHistoryBatch, fetchSnapshot } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Performance", "Relative Strength", "Correlation"];

const SECTORS: Record<string, { etf: string; companies: string[] }> = {
  "Energy (XLE)": { etf: "XLE", companies: ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "OXY", "DVN"] },
  "Technology (XLK)": { etf: "XLK", companies: ["AAPL", "MSFT", "NVDA", "AVGO", "CRM", "ORCL", "AMD", "ADBE", "ACN", "CSCO"] },
  "Financials (XLF)": { etf: "XLF", companies: ["JPM", "BAC", "WFC", "GS", "MS", "BLK", "SCHW", "C", "AXP", "MMC"] },
  "Healthcare (XLV)": { etf: "XLV", companies: ["UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR", "AMGN"] },
  "Industrials (XLI)": { etf: "XLI", companies: ["CAT", "HON", "UNP", "GE", "RTX", "BA", "DE", "LMT", "MMM", "UPS"] },
  "Consumer Disc (XLY)": { etf: "XLY", companies: ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "CMG"] },
  "Consumer Staples (XLP)": { etf: "XLP", companies: ["PG", "COST", "KO", "PEP", "WMT", "PM", "MO", "CL", "MDLZ", "KHC"] },
  "Utilities (XLU)": { etf: "XLU", companies: ["NEE", "DUK", "SO", "D", "AEP", "SRE", "EXC", "XEL", "WEC", "ED"] },
  "Materials (XLB)": { etf: "XLB", companies: ["LIN", "APD", "SHW", "ECL", "FCX", "NEM", "NUE", "DOW", "DD", "VMC"] },
  "Real Estate (XLRE)": { etf: "XLRE", companies: ["PLD", "AMT", "EQIX", "CCI", "PSA", "SPG", "O", "DLR", "WELL", "AVB"] },
  "Comms (XLC)": { etf: "XLC", companies: ["META", "GOOGL", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR", "EA"] },
};

export default function SectorAnalysis() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [sector, setSector] = useState("Technology (XLK)");
  const [activeTab, setActiveTab] = useState(0);
  const [data, setData] = useState<Record<string, { Date: string; Close: number }[]>>({});

  const sectorInfo = SECTORS[sector];
  const allTickers = sectorInfo ? [sectorInfo.etf, "SPY", ...sectorInfo.companies] : [];

  const load = useMutation({
    mutationFn: () => fetchPriceHistoryBatch(allTickers, 252),
    onSuccess: d => setData(d),
  });

  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-bold tracking-tight">Sector Analysis</h1>
        <p className="text-text-secondary text-sm mt-1">All 11 SPDR sectors: performance, relative strength, and company analysis.</p></div>

      <div className="card card-compact">
        <div className="flex items-center gap-3 flex-wrap">
          <select value={sector} onChange={e => setSector(e.target.value)}
            className="px-3 py-2 border border-border rounded-lg text-sm bg-surface min-w-[200px]">
            {Object.keys(SECTORS).map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <button onClick={() => load.mutate()} disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {load.isPending ? "Loading..." : "Load Sector"}</button>
        </div>
      </div>

      {load.isPending && <div className="card text-center py-12"><div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" /></div>}

      {Object.keys(data).length > 2 && sectorInfo && (<>
        {/* Metrics */}
        {(() => {
          const etfHist = data[sectorInfo.etf] ?? [];
          const spyHist = data["SPY"] ?? [];
          if (etfHist.length < 2) return null;
          const etfRet = (etfHist[etfHist.length - 1].Close / etfHist[0].Close - 1) * 100;
          const spyRet = spyHist.length > 1 ? (spyHist[spyHist.length - 1].Close / spyHist[0].Close - 1) * 100 : 0;
          return (
            <div className="card card-compact"><div className="flex flex-wrap gap-6">
              <Metric label={`${sectorInfo.etf} 1Y Return`} value={`${etfRet > 0 ? "+" : ""}${etfRet.toFixed(1)}%`} deltaType={etfRet > 0 ? "gain" : "loss"} />
              <Metric label="SPY 1Y Return" value={`${spyRet > 0 ? "+" : ""}${spyRet.toFixed(1)}%`} />
              <Metric label="Alpha" value={`${(etfRet - spyRet) > 0 ? "+" : ""}${(etfRet - spyRet).toFixed(1)}%`} deltaType={(etfRet - spyRet) > 0 ? "gain" : "loss"} />
              <Metric label="Companies" value={String(sectorInfo.companies.length)} />
            </div></div>
          );
        })()}

        <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
          {TABS.map((tab, i) => (<button key={tab} onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>{tab}</button>))}
        </div>

        {/* Tab 0: Performance chart */}
        {activeTab === 0 && (
          <div className="card">
            <Plot data={[sectorInfo.etf, "SPY"].filter(tk => data[tk]?.length > 1).map((tk, i) => {
              const hist = data[tk]; const base = hist[0].Close;
              return { x: hist.map(d => d.Date), y: hist.map(d => (d.Close / base - 1) * 100),
                type: "scatter" as const, mode: "lines" as const, name: tk,
                line: { width: i === 0 ? 2.5 : 1.5, color: i === 0 ? t.accent : t.muted, dash: i === 0 ? undefined : "dash" as const } };
            })} layout={{ height: 400, ...L, yaxis: { title: "Return (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified",
              shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } }] }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
          </div>
        )}

        {/* Tab 1: Relative Strength — company performance bar chart */}
        {activeTab === 1 && (
          <div className="card">
            {(() => {
              const compStats = sectorInfo.companies.filter(tk => data[tk]?.length > 1).map(tk => {
                const hist = data[tk]; return { ticker: tk, ret: (hist[hist.length - 1].Close / hist[0].Close - 1) * 100 };
              }).sort((a, b) => b.ret - a.ret);
              return (
                <Plot data={[{ x: compStats.map(c => c.ticker), y: compStats.map(c => c.ret), type: "bar" as const,
                  marker: { color: compStats.map(c => c.ret > 0 ? t.gain : t.loss) },
                  text: compStats.map(c => `${c.ret > 0 ? "+" : ""}${c.ret.toFixed(1)}%`), textposition: "outside" as const, textfont: { size: 9, color: t.text } }]}
                  layout={{ height: 400, ...L, yaxis: { title: "1Y Return (%)", gridcolor: t.grid },
                    shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } }] }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              );
            })()}
          </div>
        )}

        {/* Tab 2: Correlation between sector companies */}
        {activeTab === 2 && (
          <div className="card">
            {(() => {
              const tickers = sectorInfo.companies.filter(tk => data[tk]?.length > 30);
              const minLen = Math.min(...tickers.map(tk => data[tk].length));
              const retArrays = tickers.map(tk => {
                const c = data[tk].slice(-minLen).map(d => d.Close);
                return c.slice(1).map((v, i) => c[i] > 0 ? (v - c[i]) / c[i] : 0);
              });
              const n = tickers.length;
              const corr: number[][] = Array.from({ length: n }, () => Array(n).fill(0));
              for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
                const T = Math.min(retArrays[i].length, retArrays[j].length);
                const a = retArrays[i].slice(-T), b = retArrays[j].slice(-T);
                const ma = a.reduce((s, v) => s + v, 0) / T, mb = b.reduce((s, v) => s + v, 0) / T;
                let num = 0, da = 0, db = 0;
                for (let k = 0; k < T; k++) { num += (a[k] - ma) * (b[k] - mb); da += (a[k] - ma) ** 2; db += (b[k] - mb) ** 2; }
                corr[i][j] = da > 0 && db > 0 ? num / Math.sqrt(da * db) : 0;
              }
              return (
                <Plot data={[{ type: "heatmap" as const, z: corr, x: tickers, y: tickers,
                  colorscale: [[0, t.loss], [0.5, t.grid], [1, t.gain]], zmid: 0, zmin: -1, zmax: 1,
                  text: corr.map(row => row.map(v => v.toFixed(2))), texttemplate: "%{text}", textfont: { size: 8 },
                  colorbar: { title: { text: "Corr", font: { size: 9 } }, thickness: 12 } }]}
                  layout={{ height: Math.max(400, n * 25), ...L, margin: { l: 60, r: 20, t: 10, b: 60 }, yaxis: { autorange: "reversed" } }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              );
            })()}
          </div>
        )}
      </>)}
    </div>
  );
}
