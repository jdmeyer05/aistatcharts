"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import {
  fetchStockDataFull, fetchStockAIAnalysis, fetchPeerComparison,
  type StockDataFull, type StockAIResult, type StockModelResult,
} from "@/lib/api";
import { getChartTheme } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { Plot } from "@/components/plot";


const REC_COLORS: Record<string, string> = {
  "Strong Buy": "#00ff96", "Buy": "#00cc66", "Hold": "#ffaa00", "Sell": "#ff6644", "Strong Sell": "#ff4444",
};
const SCORE_COLOR = (v: number) => v >= 7 ? "text-gain" : v >= 4 ? "text-warn" : "text-loss";
const DIMS = ["technical", "fundamental", "sentiment", "macro", "valuation"] as const;

function fmtMktCap(v: number) {
  if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString()}`;
}

export default function StockAnalysis() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const [ticker, setTicker] = useState("AAPL");
  const [timeframe, setTimeframe] = useState<"3M" | "1Y" | "5Y">("1Y");
  const [data, setData] = useState<StockDataFull | null>(null);
  const [ai, setAI] = useState<StockAIResult | null>(null);
  const [tab, setTab] = useState(0);
  const peersQ = useQuery({
    queryKey: ["peers", data?.ticker],
    queryFn: () => fetchPeerComparison(data!.ticker),
    enabled: !!data?.ticker && tab === 5,
    staleTime: 30 * 60 * 1000,
  });

  const load = useMutation({
    mutationFn: ({ tk, days }: { tk: string; days: number }) => {
      return fetchStockDataFull(tk, days);
    },
    onSuccess: (d) => { setData(d); setAI(null); },
  });

  const loadAI = useMutation({
    mutationFn: () => {
      if (!data) throw new Error("No stock data");
      return fetchStockAIAnalysis(data.ticker, buildPrompt(data));
    },
    onSuccess: (r) => setAI(r),
  });

  const daysMap = { "3M": 90, "1Y": 365, "5Y": 1825 } as const;

  function handleAnalyze() {
    const tk = ticker.trim().toUpperCase();
    if (!tk) return;
    setTicker(tk);
    load.mutate({ tk, days: daysMap[timeframe] });
  }

  function buildPrompt(d: StockDataFull): string {
    const f = d.fundamentals;
    const tech = d.technicals;
    const st = d.stocktwits;
    let p = `STOCK ANALYSIS for ${d.ticker}\n`;
    p += `Company: ${d.info?.name || d.ticker} | Sector: ${f.sector || d.info?.sic_description || "N/A"} | Industry: ${f.industry || "N/A"}\n`;
    p += `Price: $${d.price} | Change: ${d.change_pct > 0 ? "+" : ""}${d.change_pct}% | Market Cap: ${d.info?.market_cap ? fmtMktCap(d.info.market_cap as number) : "N/A"}\n\n`;
    p += `FUNDAMENTALS:\n`;
    const entries: string[] = [];
    if (f.pe) entries.push(`P/E: ${f.pe}`);
    if (f.forward_pe) entries.push(`Fwd P/E: ${f.forward_pe}`);
    if (f.ps) entries.push(`P/S: ${f.ps}`);
    if (f.pb) entries.push(`P/B: ${f.pb}`);
    if (f.de || f.debt_to_equity) entries.push(`D/E: ${f.de ?? f.debt_to_equity}`);
    if (f.roe != null) entries.push(`ROE: ${typeof f.roe === "number" && Math.abs(f.roe as number) < 1 ? ((f.roe as number) * 100).toFixed(1) + "%" : f.roe}`);
    if (f.margin != null) entries.push(`Margin: ${typeof f.margin === "number" && Math.abs(f.margin as number) < 1 ? ((f.margin as number) * 100).toFixed(1) + "%" : f.margin}`);
    if (f.rev_growth != null) entries.push(`Rev Growth: ${typeof f.rev_growth === "number" ? ((f.rev_growth as number) * 100).toFixed(1) + "%" : f.rev_growth}`);
    if (f.beta) entries.push(`Beta: ${f.beta}`);
    if (f.short_pct != null) entries.push(`Short%: ${typeof f.short_pct === "number" ? ((f.short_pct as number) * 100).toFixed(1) + "%" : f.short_pct}`);
    if (f.div_yield != null) entries.push(`Div Yield: ${typeof f.div_yield === "number" ? ((f.div_yield as number) * 100).toFixed(2) + "%" : f.div_yield}`);
    p += entries.join(" | ") + "\n\n";
    p += `TECHNICALS:\n`;
    p += `Price: $${d.price} | EMA20: ${tech.ema20 ?? "N/A"} | EMA50: ${tech.ema50 ?? "N/A"} | EMA200: ${tech.ema200 ?? "N/A"}\n`;
    p += `RSI(14): ${tech.rsi ?? "N/A"} | MACD: ${tech.macd_hist != null ? (tech.macd_bullish ? "Bullish" : "Bearish") + ` (${tech.macd_hist.toFixed(3)})` : "N/A"} | BB %B: ${tech.bb_pctb ?? "N/A"}\n`;
    p += `ATR%: ${tech.atr_pct ?? "N/A"} | Vol Ratio: ${tech.volume_ratio ?? "N/A"} | Trend Score: ${tech.trend_score ?? "N/A"}/4\n\n`;
    if (st) {
      p += `STOCKTWITS SENTIMENT:\nBullish: ${st.bullish} | Bearish: ${st.bearish} | Total: ${st.messages} | Bull Ratio: ${st.bull_ratio}% | Signal: ${st.signal}\n\n`;
    }
    if (d.analyst_summary?.total) {
      const a = d.analyst_summary;
      p += `ANALYST CONSENSUS: ${a.consensus} (Buy:${a.buys} Hold:${a.holds} Sell:${a.sells}) | Target: $${a.target_mean} (${a.upside_pct}% upside) | Range: $${a.target_low}-$${a.target_high}\n\n`;
    }
    if (d.insider_score) {
      p += `INSIDER SCORE: ${d.insider_score.score}/100 (${d.insider_score.signal})\n\n`;
    }
    return p;
  }

  const TABS = ["Chart & Technicals", "AI Analysis", "Insiders & EDGAR", "Financials", "Model Comparison", "Peer Comparison"];
  const hist = data?.history || [];
  const tech = data?.technicals || {};
  const fund = data?.fundamentals || {};

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Stock Analysis</h1>
        <p className="text-text-secondary text-sm mt-1">Multi-model AI equity research with quantitative scoring</p>
      </div>

      {/* Input */}
      <div className="card card-compact">
        <div className="flex gap-3 items-end">
          <div>
            <label className="metric-label">Ticker</label>
            <input type="text" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === "Enter" && handleAnalyze()}
              className="w-32 mt-1 px-3 py-1.5 border border-border rounded-lg text-sm font-data bg-surface uppercase" />
          </div>
          <div className="flex gap-1">
            {(["3M", "1Y", "5Y"] as const).map(tf => (
              <button key={tf} onClick={() => { setTimeframe(tf); if (data) load.mutate({ tk: data.ticker, days: daysMap[tf] }); }}
                className={`px-2.5 py-1.5 text-xs rounded-md font-semibold ${timeframe === tf ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
                {tf}
              </button>
            ))}
          </div>
          <button onClick={handleAnalyze} disabled={load.isPending}
            className="px-6 py-1.5 bg-accent text-white font-semibold rounded-lg text-sm hover:bg-accent-hover disabled:opacity-50">
            {load.isPending ? "Loading..." : "Analyze"}
          </button>
        </div>
      </div>

      {load.isError && (
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Error: {(load.error as Error).message}</div>
      )}

      {data && (
        <>
          {/* Header metrics */}
          <div className="card card-compact">
            <div className="flex flex-wrap items-center gap-6">
              <div>
                <div className="text-lg font-bold">{data.info?.name || data.ticker}</div>
                <div className="text-2xl font-bold font-data">${data.price.toFixed(2)}</div>
                <div className={`text-sm font-data ${data.change >= 0 ? "text-gain" : "text-loss"}`}>
                  {data.change >= 0 ? "+" : ""}{data.change.toFixed(2)} ({data.change_pct > 0 ? "+" : ""}{data.change_pct.toFixed(2)}%)
                </div>
              </div>
              {data.info?.market_cap && <Metric label="Market Cap" value={fmtMktCap(data.info.market_cap as number)} />}
              {(fund.sector || data.info?.sic_description) && <Metric label="Sector" value={String(fund.sector || data.info?.sic_description || "").slice(0, 30)} />}
              {fund.beta != null && <Metric label="Beta" value={String(fund.beta)} />}
              {ai?.success && ai.recommendation && (
                <div className="ml-auto text-center">
                  <div className="text-xs text-text-muted uppercase mb-0.5">AI Consensus</div>
                  <span className="px-3 py-1 rounded-full text-sm font-bold text-black"
                    style={{ background: REC_COLORS[ai.recommendation] || "#888" }}>
                    {ai.recommendation}
                  </span>
                  <div className="text-xs text-text-muted mt-0.5">Confidence: {ai.confidence}/10</div>
                </div>
              )}
            </div>
            {ai?.success && ai.scores && (
              <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mt-3 pt-3 border-t border-border">
                {DIMS.map(dim => (
                  <div key={dim} className="text-center">
                    <div className="metric-label capitalize">{dim}</div>
                    <div className={`text-lg font-bold ${SCORE_COLOR(ai.scores![dim])}`}>{ai.scores![dim]}</div>
                  </div>
                ))}
                <div className="text-center border-l border-border pl-3">
                  <div className="metric-label">Composite</div>
                  <div className={`text-lg font-bold ${SCORE_COLOR(ai.composite_score!)}`}>{ai.composite_score}</div>
                </div>
              </div>
            )}
          </div>

          {/* Tabs */}
          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
            {TABS.map((label, i) => (
              <button key={label} onClick={() => setTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md whitespace-nowrap transition-colors ${
                  tab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {label}
              </button>
            ))}
          </div>

          {/* ═══ Tab 0: Chart & Technicals ═══ */}
          {tab === 0 && hist.length > 0 && (
            <div className="space-y-4">
              <div className="card">
                <Plot
                  data={[
                    { x: hist.map(h => h.Date), open: hist.map(h => h.Open), high: hist.map(h => h.High),
                      low: hist.map(h => h.Low), close: hist.map(h => h.Close),
                      type: "candlestick" as const, increasing: { line: { color: t.gain } }, decreasing: { line: { color: t.loss } },
                      name: data.ticker, showlegend: false },
                    ...(hist.some(h => h.ema20 != null) ? [{
                      x: hist.map(h => h.Date), y: hist.map(h => h.ema20), type: "scatter" as const, mode: "lines" as const,
                      line: { color: t.accent, width: 1 }, name: "EMA 20" }] : []),
                    ...(hist.some(h => h.ema50 != null) ? [{
                      x: hist.map(h => h.Date), y: hist.map(h => h.ema50), type: "scatter" as const, mode: "lines" as const,
                      line: { color: t.spot, width: 1 }, name: "EMA 50" }] : []),
                    ...(hist.some(h => h.ema200 != null) ? [{
                      x: hist.map(h => h.Date), y: hist.map(h => h.ema200), type: "scatter" as const, mode: "lines" as const,
                      line: { color: t.loss, width: 1 }, name: "EMA 200" }] : []),
                    ...(hist.some(h => h.bb_upper != null) ? [
                      { x: hist.map(h => h.Date), y: hist.map(h => h.bb_upper), type: "scatter" as const, mode: "lines" as const,
                        line: { color: t.muted, width: 0.5, dash: "dot" as const }, showlegend: false },
                      { x: hist.map(h => h.Date), y: hist.map(h => h.bb_lower), type: "scatter" as const, mode: "lines" as const,
                        line: { color: t.muted, width: 0.5, dash: "dot" as const }, showlegend: false },
                    ] : []),
                  ]}
                  layout={{
                    height: 400, margin: { l: 50, r: 20, t: 10, b: 30 },
                    paper_bgcolor: "transparent", plot_bgcolor: t.plot,
                    font: { family: "Inter", color: t.text, size: 9 },
                    xaxis: { gridcolor: t.grid, rangeslider: { visible: false } },
                    yaxis: { title: "Price ($)", gridcolor: t.grid },
                    legend: { orientation: "h", y: 1.02, x: 0, font: { size: 9 } },
                    showlegend: true,
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>

              <div className="card">
                <div className="metric-label mb-1">Volume</div>
                <Plot
                  data={[{
                    x: hist.map(h => h.Date), y: hist.map(h => h.Volume), type: "bar" as const,
                    marker: { color: hist.map((h, i) => (h.Close ?? 0) >= (hist[i - 1]?.Close ?? h.Close ?? 0) ? t.gain + "80" : t.loss + "80") },
                    showlegend: false,
                  }]}
                  layout={{
                    height: 100, margin: { l: 50, r: 20, t: 5, b: 30 },
                    paper_bgcolor: "transparent", plot_bgcolor: t.plot,
                    font: { family: "Inter", color: t.text, size: 9 },
                    xaxis: { gridcolor: t.grid }, yaxis: { title: "Vol", gridcolor: t.grid }, showlegend: false,
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {hist.some(h => h.rsi != null) && (
                  <div className="card">
                    <div className="metric-label mb-1">RSI (14)</div>
                    <Plot
                      data={[{ x: hist.map(h => h.Date), y: hist.map(h => h.rsi), type: "scatter" as const, mode: "lines" as const,
                        line: { color: t.accent, width: 1.5 }, showlegend: false }]}
                      layout={{
                        height: 140, margin: { l: 40, r: 10, t: 5, b: 25 },
                        paper_bgcolor: "transparent", plot_bgcolor: t.plot,
                        font: { family: "Inter", color: t.text, size: 8 },
                        xaxis: { gridcolor: t.grid }, yaxis: { range: [0, 100], gridcolor: t.grid },
                        shapes: [
                          { type: "line", x0: 0, x1: 1, xref: "paper", y0: 70, y1: 70, line: { color: t.loss, width: 0.5, dash: "dot" } },
                          { type: "line", x0: 0, x1: 1, xref: "paper", y0: 30, y1: 30, line: { color: t.gain, width: 0.5, dash: "dot" } },
                        ],
                      }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%" }}
                    />
                  </div>
                )}
                {hist.some(h => h.macd_line != null) && (
                  <div className="card">
                    <div className="metric-label mb-1">MACD (12,26,9)</div>
                    <Plot
                      data={[
                        { x: hist.map(h => h.Date), y: hist.map(h => h.macd_hist), type: "bar" as const,
                          marker: { color: hist.map(h => Number(h.macd_hist ?? 0) >= 0 ? t.gain + "80" : t.loss + "80") }, showlegend: false },
                        { x: hist.map(h => h.Date), y: hist.map(h => h.macd_line), type: "scatter" as const, mode: "lines" as const,
                          line: { color: t.accent, width: 1 }, name: "MACD" },
                        { x: hist.map(h => h.Date), y: hist.map(h => h.macd_signal_line), type: "scatter" as const, mode: "lines" as const,
                          line: { color: t.spot, width: 1 }, name: "Signal" },
                      ]}
                      layout={{
                        height: 140, margin: { l: 40, r: 10, t: 5, b: 25 },
                        paper_bgcolor: "transparent", plot_bgcolor: t.plot,
                        font: { family: "Inter", color: t.text, size: 8 },
                        xaxis: { gridcolor: t.grid }, yaxis: { gridcolor: t.grid },
                        legend: { orientation: "h", y: 1.02, font: { size: 8 } },
                      }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%" }}
                    />
                  </div>
                )}
              </div>

              <div className="grid grid-cols-3 sm:grid-cols-6 gap-3">
                <Metric label="RSI (14)" value={tech.rsi != null ? `${tech.rsi}` : "—"} />
                <Metric label="MACD" value={tech.macd_bullish ? "Bullish" : "Bearish"} />
                <Metric label="BB %B" value={tech.bb_pctb != null ? `${tech.bb_pctb}` : "—"} />
                <Metric label="ATR %" value={tech.atr_pct != null ? `${tech.atr_pct}%` : "—"} />
                <Metric label="Vol Ratio" value={tech.volume_ratio != null ? `${tech.volume_ratio}x` : "—"} />
                <Metric label="Trend" value={`${tech.trend_score ?? 0}/4`} />
              </div>

              <div className="card">
                <div className="metric-label mb-2">Fundamentals</div>
                <div className="grid grid-cols-3 sm:grid-cols-5 gap-3">
                  <Metric label="P/E" value={fund.pe != null ? String(fund.pe) : "—"} />
                  <Metric label="P/S" value={fund.ps != null ? String(fund.ps) : "—"} />
                  <Metric label="P/B" value={fund.pb != null ? String(fund.pb) : "—"} />
                  <Metric label="D/E" value={fund.debt_to_equity != null ? String(fund.debt_to_equity) : fund.de != null ? String(fund.de) : "—"} />
                  <Metric label="ROE" value={fund.roe != null ? `${typeof fund.roe === "number" && Math.abs(fund.roe as number) < 5 ? ((fund.roe as number) * 100).toFixed(1) : fund.roe}%` : "—"} />
                  <Metric label="Margin" value={fund.net_margin != null ? `${fund.net_margin}%` : fund.margin != null ? `${typeof fund.margin === "number" && Math.abs(fund.margin as number) < 1 ? ((fund.margin as number) * 100).toFixed(1) : fund.margin}%` : "—"} />
                  <Metric label="Rev Growth" value={fund.rev_growth != null ? `${typeof fund.rev_growth === "number" ? ((fund.rev_growth as number) * 100).toFixed(1) : fund.rev_growth}%` : "—"} />
                  <Metric label="Short %" value={fund.short_pct != null ? `${typeof fund.short_pct === "number" ? ((fund.short_pct as number) * 100).toFixed(1) : fund.short_pct}%` : "—"} />
                  <Metric label="Div Yield" value={fund.div_yield != null ? `${typeof fund.div_yield === "number" ? ((fund.div_yield as number) * 100).toFixed(2) : fund.div_yield}%` : "—"} />
                  <Metric label="Fwd P/E" value={fund.forward_pe != null ? String(fund.forward_pe) : "—"} />
                </div>
              </div>
            </div>
          )}

          {/* ═══ Tab 1: AI Analysis ═══ */}
          {tab === 1 && (
            <div className="space-y-4">
              {!ai && !loadAI.isPending && (
                <div className="card text-center py-8">
                  <button onClick={() => loadAI.mutate()} className="px-8 py-3 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover">
                    Run 3-Model AI Analysis (Grok + Gemini + Claude)
                  </button>
                  <p className="text-xs text-text-muted mt-2">Takes ~15-30 seconds. Calls all 3 models in parallel.</p>
                </div>
              )}
              {loadAI.isPending && (
                <div className="card text-center py-8">
                  <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
                  <p className="text-sm text-text-muted mt-3">Running 3-model consensus analysis...</p>
                </div>
              )}
              {loadAI.isError && (
                <div className="card border-loss/30 bg-loss-bg text-loss text-sm">AI failed: {(loadAI.error as Error).message}</div>
              )}
              {ai && !ai.success && (
                <div className="card border-loss/30 bg-loss-bg text-loss text-sm">
                  All models failed: {ai.error || "Unknown error"}. Check API keys are configured.
                </div>
              )}
              {ai?.success && (
                <>
                  {ai.agreement && (
                    <div className="card card-compact text-sm">
                      <strong>Model Consensus:</strong>{" "}
                      <span dangerouslySetInnerHTML={{ __html: ai.agreement.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>") }} />
                    </div>
                  )}
                  {ai.summary && (
                    <div className="card">
                      <div className="metric-label mb-1">Executive Summary</div>
                      <p className="text-sm">{ai.summary}</p>
                      {ai.sentiment_pulse && <p className="text-sm text-text-muted mt-2 italic">{ai.sentiment_pulse}</p>}
                    </div>
                  )}
                  {ai.price_targets && data && (
                    <div className="card">
                      <div className="metric-label mb-1">Price Targets (12-Month)</div>
                      <div className="grid grid-cols-4 gap-3 mb-3">
                        <Metric label={`Bear (${ai.price_targets.bear_prob}%)`} value={`$${ai.price_targets.bear}`} />
                        <Metric label={`Base (${ai.price_targets.base_prob}%)`} value={`$${ai.price_targets.base}`} />
                        <Metric label={`Bull (${ai.price_targets.bull_prob}%)`} value={`$${ai.price_targets.bull}`} />
                        <Metric label="Current" value={`$${data.price.toFixed(2)}`} />
                      </div>
                      <div className="flex items-center gap-1 text-[0.6rem] font-data h-6 rounded overflow-hidden">
                        <div className="h-full flex items-center justify-center bg-loss/30 text-loss" style={{ width: `${ai.price_targets.bear_prob}%` }}>{ai.price_targets.bear_prob}%</div>
                        <div className="h-full flex items-center justify-center bg-accent/30 text-accent" style={{ width: `${ai.price_targets.base_prob}%` }}>{ai.price_targets.base_prob}%</div>
                        <div className="h-full flex items-center justify-center bg-gain/30 text-gain" style={{ width: `${ai.price_targets.bull_prob}%` }}>{ai.price_targets.bull_prob}%</div>
                      </div>
                    </div>
                  )}
                  {ai.scores && (
                    <div className="card">
                      <div className="metric-label mb-1">Dimension Scores</div>
                      <Plot
                        data={[
                          ...Object.values(ai.model_results || {}).filter((m: StockModelResult) => m.success && m.scores).map((m: StockModelResult) => ({
                            type: "scatterpolar" as const, r: [...DIMS.map(d => m.scores![d]), m.scores![DIMS[0]]],
                            theta: [...DIMS.map(d => d.charAt(0).toUpperCase() + d.slice(1)), DIMS[0].charAt(0).toUpperCase() + DIMS[0].slice(1)],
                            fill: "toself" as const, fillcolor: m.color + "15",
                            line: { color: m.color, width: 1, dash: "dot" as const },
                            name: m.model_name, opacity: 0.6,
                          })),
                          { type: "scatterpolar" as const, r: [...DIMS.map(d => ai.scores![d]), ai.scores![DIMS[0]]],
                            theta: [...DIMS.map(d => d.charAt(0).toUpperCase() + d.slice(1)), DIMS[0].charAt(0).toUpperCase() + DIMS[0].slice(1)],
                            fill: "toself" as const, fillcolor: t.accent + "25",
                            line: { color: t.accent, width: 2 }, name: "Consensus" },
                        ]}
                        layout={{
                          height: 300, margin: { l: 40, r: 40, t: 30, b: 30 },
                          paper_bgcolor: "transparent", plot_bgcolor: "transparent",
                          font: { family: "Inter", color: t.text, size: 9 },
                          polar: { radialaxis: { visible: true, range: [0, 10], gridcolor: t.grid }, angularaxis: { gridcolor: t.grid } },
                          legend: { orientation: "h", y: -0.1, font: { size: 9 } }, showlegend: true,
                        }}
                        config={{ displayModeBar: false, responsive: true }}
                        style={{ width: "100%" }}
                      />
                    </div>
                  )}
                  {ai.analysis && (
                    <div className="card space-y-3">
                      <div className="metric-label">Detailed Analysis</div>
                      {DIMS.map(dim => ai.analysis![dim] && (
                        <details key={dim} className="group">
                          <summary className="cursor-pointer text-sm font-semibold capitalize flex items-center gap-2">
                            {dim} {ai.scores && <span className={`text-xs ${SCORE_COLOR(ai.scores[dim])}`}>{ai.scores[dim]}/10</span>}
                          </summary>
                          <p className="text-sm text-text-muted mt-1 pl-4">{ai.analysis![dim]}</p>
                        </details>
                      ))}
                    </div>
                  )}
                  {(ai.risks?.length || ai.catalysts?.length) ? (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {ai.risks && ai.risks.length > 0 && (
                        <div className="card">
                          <div className="metric-label text-loss mb-2">Key Risks</div>
                          <ul className="space-y-1">{ai.risks.map((r, i) => <li key={i} className="text-sm text-text-muted">• {r}</li>)}</ul>
                        </div>
                      )}
                      {ai.catalysts && ai.catalysts.length > 0 && (
                        <div className="card">
                          <div className="metric-label text-gain mb-2">Key Catalysts</div>
                          <ul className="space-y-1">{ai.catalysts.map((c, i) => <li key={i} className="text-sm text-text-muted">• {c}</li>)}</ul>
                        </div>
                      )}
                    </div>
                  ) : null}
                </>
              )}
            </div>
          )}

          {/* ═══ Tab 2: Insiders & EDGAR ═══ */}
          {tab === 2 && (
            <div className="space-y-4">
              {data.insider_score && (
                <div className="card">
                  <div className="flex items-center gap-4">
                    <div className={`text-3xl font-bold font-data ${data.insider_score.score >= 65 ? "text-gain" : data.insider_score.score <= 40 ? "text-loss" : "text-warn"}`}>
                      {data.insider_score.score}
                    </div>
                    <div>
                      <div className="metric-label">Insider Score</div>
                      <div className="text-sm font-semibold">{data.insider_score.signal}</div>
                      <div className="text-[0.65rem] text-text-muted font-data">
                        Buys: {data.insider_score.breakdown.buys ?? 0} · Sells: {data.insider_score.breakdown.sells ?? 0}
                        {data.insider_score.breakdown.csuite_buys ? ` · C-Suite: ${data.insider_score.breakdown.csuite_buys}` : ""}
                        {data.insider_score.breakdown.cluster_buy ? " · Cluster!" : ""}
                        {data.insider_score.breakdown.large_buys ? ` · Large: ${data.insider_score.breakdown.large_buys}` : ""}
                      </div>
                    </div>
                  </div>
                </div>
              )}
              {data.insiders.length > 0 && (
                <div className="card">
                  <div className="metric-label mb-2">Recent Insider Transactions</div>
                  <div className="overflow-x-auto">
                    <table className="data-table text-xs">
                      <thead><tr><th>Name</th><th>Title</th><th>Type</th><th>Shares</th><th>Price</th><th>Date</th></tr></thead>
                      <tbody>
                        {data.insiders.slice(0, 15).map((ins, i) => (
                          <tr key={i}>
                            <td className="font-semibold">{ins.name || ins.Name || "—"}</td>
                            <td className="text-text-muted">{ins.title || ins.Title || "—"}</td>
                            <td><span className={`badge ${String(ins.transaction_type || ins.Transaction || "").toLowerCase().includes("purchase") ? "badge-gain" : "badge-loss"}`}>
                              {ins.transaction_type || ins.Transaction || "—"}</span></td>
                            <td className="font-data">{ins.shares || ins.Shares ? Number(ins.shares || ins.Shares).toLocaleString() : "—"}</td>
                            <td className="font-data">{ins.price ? `$${ins.price}` : "—"}</td>
                            <td className="font-data">{ins.filing_date || ins.Date || ins.date || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              {data.events_8k.length > 0 && (
                <div className="card">
                  <div className="metric-label mb-2">Recent 8-K Filings</div>
                  <div className="space-y-2">
                    {data.events_8k.map((ev, i) => (
                      <div key={i} className="flex items-center gap-3 p-2 rounded border border-border text-sm">
                        <span className="badge badge-info">8-K</span>
                        <div>
                          <div className="font-semibold">{ev.company}</div>
                          <div className="text-xs text-text-muted">{ev.filed}{ev.items ? ` · ${ev.items}` : ""}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {data.analyst_summary?.total && data.analyst_summary.total > 0 && (
                <div className="card">
                  <div className="metric-label mb-2">Wall Street Consensus</div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
                    <Metric label="Consensus" value={data.analyst_summary.consensus || "—"} />
                    <Metric label="Target" value={data.analyst_summary.target_mean ? `$${data.analyst_summary.target_mean}` : "—"} />
                    <Metric label="Upside" value={data.analyst_summary.upside_pct != null ? `${data.analyst_summary.upside_pct}%` : "—"} />
                    <Metric label="Analysts" value={String(data.analyst_summary.total)} />
                  </div>
                  <div className="text-xs text-text-muted font-data">
                    Buy: {data.analyst_summary.buys} · Hold: {data.analyst_summary.holds} · Sell: {data.analyst_summary.sells}
                    {data.analyst_summary.target_low && ` · Range: $${data.analyst_summary.target_low} - $${data.analyst_summary.target_high}`}
                  </div>
                </div>
              )}
              {data.stocktwits && (
                <div className="card">
                  <div className="metric-label mb-2">StockTwits Sentiment</div>
                  <div className="flex items-center gap-6">
                    <div className={`text-3xl font-bold ${data.stocktwits.bull_ratio >= 60 ? "text-gain" : data.stocktwits.bull_ratio <= 40 ? "text-loss" : "text-warn"}`}>
                      {data.stocktwits.bull_ratio}%
                    </div>
                    <div>
                      <div className="text-sm font-semibold">{data.stocktwits.signal}</div>
                      <div className="text-xs text-text-muted font-data">
                        Bullish: {data.stocktwits.bullish} · Bearish: {data.stocktwits.bearish} · Total: {data.stocktwits.messages}
                      </div>
                    </div>
                  </div>
                </div>
              )}
              {data.recommendations.length > 0 && (
                <div className="card">
                  <div className="metric-label mb-2">Recent Analyst Actions</div>
                  <div className="overflow-x-auto">
                    <table className="data-table text-xs">
                      <thead><tr><th>Firm</th><th>Rating</th><th>Target</th><th>Date</th></tr></thead>
                      <tbody>
                        {data.recommendations.slice(0, 15).map((r, i) => (
                          <tr key={i}>
                            <td className="font-semibold">{r.firm || r.publisher || "—"}</td>
                            <td><span className={`badge ${String(r.rating || r.action || "").toLowerCase().includes("buy") ? "badge-gain" : String(r.rating || r.action || "").toLowerCase().includes("sell") ? "badge-loss" : "badge-info"}`}>
                              {r.rating || r.action || "—"}</span></td>
                            <td className="font-data">{r.target_price ? `$${r.target_price}` : "—"}</td>
                            <td className="font-data">{r.date || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ═══ Tab 3: Financials (XBRL) ═══ */}
          {tab === 3 && (
            <div className="space-y-4">
              {Object.keys(data.fundamentals).length > 0 && (
                <div className="card">
                  <div className="metric-label mb-2">Key Ratios (XBRL)</div>
                  <div className="grid grid-cols-3 sm:grid-cols-6 gap-3">
                    <Metric label="Net Margin" value={fund.net_margin != null ? `${fund.net_margin}%` : "—"} />
                    <Metric label="Op Margin" value={fund.operating_margin != null ? `${fund.operating_margin}%` : "—"} />
                    <Metric label="ROE" value={fund.roe != null ? `${typeof fund.roe === "number" && Math.abs(fund.roe as number) < 5 ? ((fund.roe as number) * 100).toFixed(1) : fund.roe}%` : "—"} />
                    <Metric label="ROA" value={fund.roa != null ? `${fund.roa}%` : "—"} />
                    <Metric label="D/E" value={fund.debt_to_equity != null ? String(fund.debt_to_equity) : "—"} />
                    <Metric label="Current Ratio" value={fund.current_ratio != null ? String(fund.current_ratio) : "—"} />
                  </div>
                </div>
              )}
              {Object.entries(data.xbrl_history).map(([key, series]) => {
                if (!series || series.length === 0) return null;
                const label = key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
                const hasNeg = key === "net_income" && series.some(s => s.value < 0);
                return (
                  <div key={key} className="card">
                    <div className="metric-label mb-1">{label}</div>
                    <Plot
                      data={[{
                        x: series.map(s => s.period), y: series.map(s => key === "eps" ? s.value : s.value / 1e6),
                        type: "bar" as const, marker: { color: hasNeg ? series.map(s => s.value >= 0 ? t.gain : t.loss) : t.accent }, showlegend: false,
                      }]}
                      layout={{
                        height: 180, margin: { l: 50, r: 10, t: 5, b: 40 },
                        paper_bgcolor: "transparent", plot_bgcolor: t.plot,
                        font: { family: "Inter", color: t.text, size: 8 },
                        xaxis: { gridcolor: t.grid, tickangle: -45 }, yaxis: { title: key === "eps" ? "$/share" : "$M", gridcolor: t.grid },
                      }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%" }}
                    />
                  </div>
                );
              })}
              {Object.keys(data.xbrl_history).length === 0 && (
                <div className="text-sm text-text-muted italic">No XBRL financial history available for this ticker.</div>
              )}
            </div>
          )}

          {/* ═══ Tab 4: Model Comparison ═══ */}
          {tab === 4 && (
            <div className="space-y-4">
              {!ai && (
                <div className="card text-center py-6 text-sm text-text-muted">Run AI Analysis first to see model comparison.</div>
              )}
              {ai?.success && ai.model_results && (
                <>
                  <div className="card overflow-x-auto">
                    <table className="data-table text-xs">
                      <thead>
                        <tr>
                          <th>Dimension</th>
                          {Object.values(ai.model_results).map((m: StockModelResult) => (
                            <th key={m.model_name} style={{ borderBottom: `2px solid ${m.color}` }}>{m.model_name}</th>
                          ))}
                          <th style={{ borderBottom: `2px solid ${t.accent}` }}>Consensus</th>
                        </tr>
                      </thead>
                      <tbody>
                        {DIMS.map(dim => (
                          <tr key={dim}>
                            <td className="font-semibold capitalize">{dim}</td>
                            {Object.values(ai.model_results!).map((m: StockModelResult) => (
                              <td key={m.model_name} className={`font-data ${m.success && m.scores ? SCORE_COLOR(m.scores[dim]) : "text-text-muted"}`}>
                                {m.success && m.scores ? m.scores[dim] : "—"}</td>
                            ))}
                            <td className={`font-data font-bold ${ai.scores ? SCORE_COLOR(ai.scores[dim]) : ""}`}>{ai.scores?.[dim] ?? "—"}</td>
                          </tr>
                        ))}
                        <tr className="border-t border-border">
                          <td className="font-bold">Composite</td>
                          {Object.values(ai.model_results!).map((m: StockModelResult) => (
                            <td key={m.model_name} className="font-data font-bold">{m.success ? m.composite_score ?? "—" : "—"}</td>
                          ))}
                          <td className="font-data font-bold">{ai.composite_score ?? "—"}</td>
                        </tr>
                        <tr>
                          <td className="font-semibold">Recommendation</td>
                          {Object.values(ai.model_results!).map((m: StockModelResult) => (
                            <td key={m.model_name}>{m.success && m.recommendation
                              ? <span className="badge" style={{ background: REC_COLORS[m.recommendation] || "#888", color: "#000" }}>{m.recommendation}</span>
                              : "—"}</td>
                          ))}
                          <td><span className="badge" style={{ background: REC_COLORS[ai.recommendation || ""] || "#888", color: "#000" }}>{ai.recommendation}</span></td>
                        </tr>
                        <tr>
                          <td className="font-semibold">Confidence</td>
                          {Object.values(ai.model_results!).map((m: StockModelResult) => (
                            <td key={m.model_name} className="font-data">{m.success ? `${m.confidence}/10` : "—"}</td>
                          ))}
                          <td className="font-data font-bold">{ai.confidence}/10</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    {Object.values(ai.model_results!).map((m: StockModelResult) => (
                      <div key={m.model_name} className="card" style={{ borderLeft: `3px solid ${m.color}` }}>
                        <div className="font-semibold text-sm mb-2">{m.model_name}</div>
                        {m.success ? (
                          <>
                            <div className="grid grid-cols-2 gap-2 text-xs">
                              <div><span className="text-text-muted">Rec:</span> <span className="font-semibold">{m.recommendation}</span></div>
                              <div><span className="text-text-muted">Conf:</span> <span className="font-data">{m.confidence}/10</span></div>
                            </div>
                            {m.price_targets && (
                              <div className="text-xs text-text-muted mt-1 font-data">
                                Bear: ${m.price_targets.bear} · Base: ${m.price_targets.base} · Bull: ${m.price_targets.bull}
                              </div>
                            )}
                            {m.summary && <p className="text-xs text-text-muted mt-2 line-clamp-3">{m.summary}</p>}
                          </>
                        ) : (
                          <div className="text-xs text-loss">{m.error?.slice(0, 100) || "Failed"}</div>
                        )}
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {/* ═══ Tab 5: Peer Comparison ═══ */}
          {tab === 5 && (
            <div className="space-y-4">
              {peersQ.isPending && (
                <div className="card text-center py-8">
                  <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
                  <div className="text-xs text-text-muted mt-2">Fetching related companies…</div>
                </div>
              )}
              {peersQ.isSuccess && peersQ.data.peers.length <= 1 && (
                <div className="card text-sm text-text-muted py-6 px-5">No peer companies found for {data?.ticker}.</div>
              )}
              {peersQ.isSuccess && peersQ.data.peers.length > 1 && (
                <div className="card">
                  <div className="font-semibold text-sm mb-2">Peer Comparison — {data?.ticker}</div>
                  <div className="text-xs text-text-muted mb-3">Related companies via Polygon. Highlighted row = your ticker.</div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs font-data">
                      <thead className="border-b border-border text-text-muted">
                        <tr>
                          <th className="text-left py-1.5 px-2">Ticker</th>
                          <th className="text-right py-1.5 px-2">Price</th>
                          <th className="text-right py-1.5 px-2">Change</th>
                          <th className="text-right py-1.5 px-2">Mkt Cap</th>
                          <th className="text-right py-1.5 px-2">P/E</th>
                          <th className="text-right py-1.5 px-2">P/B</th>
                          <th className="text-right py-1.5 px-2">Rev Growth</th>
                          <th className="text-right py-1.5 px-2">Margin</th>
                        </tr>
                      </thead>
                      <tbody>
                        {peersQ.data.peers.map(row => (
                          <tr key={row.ticker}
                            className={`border-b border-border/50 hover:bg-surface-alt ${row.is_target ? "font-bold" : ""}`}
                            style={{ color: row.is_target ? t.accent : undefined }}>
                            <td className="py-1 px-2">{row.ticker}</td>
                            <td className="py-1 px-2 text-right">{row.price != null ? `$${row.price.toFixed(2)}` : "—"}</td>
                            <td className={`py-1 px-2 text-right ${row.change > 0 ? "text-gain" : row.change < 0 ? "text-loss" : ""}`}>
                              {row.change >= 0 ? "+" : ""}{row.change.toFixed(1)}%
                            </td>
                            <td className="py-1 px-2 text-right">
                              {row.market_cap != null ? fmtMktCap(row.market_cap) : "—"}
                            </td>
                            <td className="py-1 px-2 text-right">{row.pe != null ? row.pe.toFixed(1) : "—"}</td>
                            <td className="py-1 px-2 text-right">{row.pb != null ? row.pb.toFixed(1) : "—"}</td>
                            <td className={`py-1 px-2 text-right ${(row.revenue_growth ?? 0) > 0 ? "text-gain" : (row.revenue_growth ?? 0) < 0 ? "text-loss" : ""}`}>
                              {row.revenue_growth != null ? `${(row.revenue_growth * 100) >= 0 ? "+" : ""}${(row.revenue_growth * 100).toFixed(1)}%` : "—"}
                            </td>
                            <td className="py-1 px-2 text-right">
                              {row.profit_margin != null ? `${(row.profit_margin * 100).toFixed(1)}%` : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              {peersQ.isError && (
                <div className="card border-loss/30 text-sm text-loss py-4 px-5">
                  Peer comparison failed: {(peersQ.error as Error)?.message ?? "unknown error"}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
