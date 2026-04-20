"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import {
  fetchPortfolioImpact,
  fetchGbmProjection,
  fetchRegimeTrackRecord,
  fetchGrokLatest,
  fetchFredBatch,
  type PortfolioImpactResponse,
  type GbmResponse,
  type RegimeTrackResponse,
  type GrokLatestResponse,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, heatmapTrace, heatmapHeight } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";


const TABS = [
  "Macro Portfolio Scenarios",
  "Fed & Macro Drivers",
  "Historical Stress Tests",
  "Custom What-If",
  "Bull / Base / Bear",
  "Event-Driven",
  "Model Diagnostics",
  "Regime Track Record",
];

const HISTORICAL_SCENARIOS: Record<string, Record<string, number>> = {
  "2008 Financial Crisis (Sep-Nov 2008)": { SPY: -0.46, QQQ: -0.49, TLT: 0.33, USO: -0.68, GLD: 0.05, _default: -0.40 },
  "COVID Crash (Feb-Mar 2020)": { SPY: -0.34, QQQ: -0.28, TLT: 0.21, USO: -0.80, GLD: -0.03, _default: -0.30 },
  "2022 Rate Shock (Jan-Sep 2022)": { SPY: -0.25, QQQ: -0.33, TLT: -0.31, USO: 0.28, GLD: -0.09, _default: -0.20 },
  "Dot-Com Bust (Mar 2000-Oct 2002)": { SPY: -0.49, QQQ: -0.83, TLT: 0.20, USO: 0.0, GLD: 0.06, _default: -0.40 },
  "2011 Euro Debt Crisis (May-Oct)": { SPY: -0.19, QQQ: -0.16, TLT: 0.28, USO: -0.20, GLD: 0.08, _default: -0.15 },
  "2015-16 China/Oil Selloff": { SPY: -0.13, QQQ: -0.13, TLT: 0.05, USO: -0.55, GLD: 0.04, _default: -0.10 },
  "2018 Q4 Selloff (Oct-Dec)": { SPY: -0.20, QQQ: -0.23, TLT: 0.06, USO: -0.40, GLD: 0.08, _default: -0.15 },
  "Oil Crash (Jun 2014-Feb 2016)": { SPY: -0.03, QQQ: 0.02, TLT: 0.13, USO: -0.77, GLD: 0.03, _default: -0.05 },
};

const WHATIF_PRESETS: Record<string, Record<string, number>> = {
  "Risk-Off Flight to Safety": { SPY: -15, QQQ: -20, TLT: 10, USO: -25, GLD: 8 },
  "Inflation Surge": { SPY: -8, QQQ: -12, TLT: -15, USO: 30, GLD: 15 },
  "Rate Cut Rally": { SPY: 12, QQQ: 18, TLT: 8, USO: 5, GLD: 5 },
  "Dollar Collapse": { SPY: -5, QQQ: -5, TLT: -10, USO: 20, GLD: 25 },
  "Stagflation": { SPY: -15, QQQ: -18, TLT: -8, USO: 15, GLD: 12 },
};

const CATALYST_PRESETS: Record<string, Array<{ name: string; prob: number; move: number }>> = {
  "— Custom —": [],
  "FOMC Rate Decision": [
    { name: "Dovish Cut (-50bp)", prob: 15, move: 3.0 },
    { name: "Standard Cut (-25bp)", prob: 35, move: 1.5 },
    { name: "Hold (No Change)", prob: 35, move: -0.5 },
    { name: "Hawkish Hold", prob: 15, move: -2.5 },
  ],
  "Earnings Report": [
    { name: "Blowout Beat", prob: 15, move: 8.0 },
    { name: "Modest Beat", prob: 35, move: 3.0 },
    { name: "In-Line", prob: 20, move: -1.0 },
    { name: "Modest Miss", prob: 20, move: -5.0 },
    { name: "Bad Miss + Guide Down", prob: 10, move: -12.0 },
  ],
  "CPI / Inflation Print": [
    { name: "Below Consensus (Dovish)", prob: 25, move: 2.0 },
    { name: "In-Line", prob: 40, move: 0.0 },
    { name: "Hot Print (Hawkish)", prob: 25, move: -2.5 },
    { name: "Shock Upside", prob: 10, move: -5.0 },
  ],
  "Geopolitical Escalation": [
    { name: "De-escalation", prob: 20, move: 3.0 },
    { name: "Status Quo", prob: 40, move: 0.0 },
    { name: "Minor Escalation", prob: 25, move: -3.0 },
    { name: "Major Escalation", prob: 15, move: -8.0 },
  ],
};

const REGIME_COLORS: Record<string, string> = {
  "Stagflation": "#ff4444",
  "Recession": "#ff8c00",
  "Soft Landing": "#00cc66",
  "Financial Crisis": "#ff0066",
  "Re-Acceleration": "#00d1ff",
  "Goldilocks": "#aa66ff",
};

function fmtDollar(v: number, signed = true): string {
  const prefix = signed && v >= 0 ? "+" : v < 0 ? "-" : "";
  return `${prefix}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
function fmtPct(v: number, digits = 1): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export default function ScenarioAnalysisPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const [tickerInput, setTickerInput] = useState("SPY,QQQ,TLT,USO,GLD");
  const [portfolioValue, setPortfolioValue] = useState(100_000);
  const [lookback, setLookback] = useState(756);
  const [horizonLabel, setHorizonLabel] = useState<"3 Months" | "6 Months" | "12 Months">("12 Months");
  const [activeTab, setActiveTab] = useState(0);
  const [userProbs, setUserProbs] = useState<Record<string, number>>({});

  const horizonDays = { "3 Months": 63, "6 Months": 126, "12 Months": 252 }[horizonLabel];

  const tickers = useMemo(() => tickerInput.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean), [tickerInput]);

  const impact = useMutation({
    mutationFn: () => fetchPortfolioImpact({
      tickers,
      portfolio_value: portfolioValue,
      lookback,
      horizon_days: horizonDays,
      user_probs: Object.keys(userProbs).length > 0 ? userProbs : undefined,
    }),
    onSuccess: (d) => {
      if (!d.error && d.regimes) {
        // seed user_probs with base probs if not yet set
        setUserProbs((prev) => {
          if (Object.keys(prev).length > 0) return prev;
          const init: Record<string, number> = {};
          d.regimes.forEach((r) => (init[r.name] = r.base_probability));
          return init;
        });
      }
    },
  });

  const data = impact.data && !impact.data.error ? impact.data : null;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Scenario Analysis Engine</h1>
        <p className="text-text-secondary text-sm mt-1">
          Stress test portfolios against historical shocks, custom what-if scenarios, bull/bear projections, and event-driven catalysts.
        </p>
      </div>

      {/* Controls */}
      <div className="card card-compact">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[260px]">
            <label className="metric-label">Tickers (comma-sep)</label>
            <input
              value={tickerInput}
              onChange={(e) => setTickerInput(e.target.value)}
              className="mt-0.5 w-full px-3 py-1.5 border border-border rounded text-sm bg-surface font-data"
            />
          </div>
          <div>
            <label className="metric-label">Portfolio Value</label>
            <input
              type="number"
              value={portfolioValue}
              onChange={(e) => setPortfolioValue(Number(e.target.value))}
              step={10_000}
              className="mt-0.5 w-32 px-3 py-1.5 border border-border rounded text-sm bg-surface font-data"
            />
          </div>
          <div>
            <label className="metric-label">Lookback (days)</label>
            <input
              type="number"
              value={lookback}
              onChange={(e) => setLookback(Number(e.target.value))}
              min={252}
              max={2520}
              step={126}
              className="mt-0.5 w-24 px-3 py-1.5 border border-border rounded text-sm bg-surface font-data"
            />
          </div>
          <div>
            <label className="metric-label">Horizon</label>
            <div className="flex gap-1 mt-0.5">
              {(["3 Months", "6 Months", "12 Months"] as const).map((h) => (
                <button
                  key={h}
                  onClick={() => setHorizonLabel(h)}
                  className={`px-2.5 py-1.5 text-xs rounded ${horizonLabel === h ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}
                >
                  {h}
                </button>
              ))}
            </div>
          </div>
          <button
            onClick={() => impact.mutate()}
            disabled={impact.isPending || tickers.length < 1}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {impact.isPending ? "Running…" : "Run Analysis"}
          </button>
        </div>
      </div>

      {impact.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-2">Fetching prices, fitting factor model, running Monte Carlo…</div>
        </div>
      )}

      {impact.data?.error && <div className="card border-loss text-loss text-sm">{impact.data.error}</div>}

      {!data && !impact.isPending && !impact.data?.error && (
        <div className="card text-center py-10 text-text-muted text-sm">
          <div className="font-semibold text-text mb-1">Scenario engine is idle</div>
          Set your portfolio and horizon, then click <span className="text-accent font-semibold">Run Analysis</span> to fit factor betas,
          score 6 macro regimes, and run a 10k-path Monte Carlo with VaR/CVaR.
        </div>
      )}

      {data && (
        <>
          <SummaryBar data={data} t={t} />

          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
            {TABS.map((tab, i) => (
              <button
                key={tab}
                onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md whitespace-nowrap transition-colors ${
                  activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"
                }`}
              >
                {tab}
              </button>
            ))}
          </div>

          {activeTab === 0 && <MacroScenariosTab data={data} t={t} L={L} userProbs={userProbs} setUserProbs={setUserProbs} onRerun={() => impact.mutate()} isPending={impact.isPending} />}
          {activeTab === 1 && <FedMacroTab t={t} L={L} />}
          {activeTab === 2 && <HistoricalStressTab data={data} t={t} L={L} />}
          {activeTab === 3 && <WhatIfTab data={data} t={t} L={L} />}
          {activeTab === 4 && <BullBaseBearTab data={data} t={t} L={L} />}
          {activeTab === 5 && <EventDrivenTab data={data} t={t} L={L} />}
          {activeTab === 6 && <DiagnosticsTab data={data} t={t} L={L} />}
          {activeTab === 7 && <TrackRecordTab t={t} L={L} />}
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// SUMMARY BAR
// ═══════════════════════════════════════════════════════════════

function SummaryBar({ data, t }: { data: PortfolioImpactResponse; t: ReturnType<typeof getChartTheme> }) {
  const best = [...data.regime_results].sort((a, b) => b.pnl - a.pnl)[0];
  const worst = [...data.regime_results].sort((a, b) => a.pnl - b.pnl)[0];
  return (
    <div className="card card-compact flex flex-wrap items-center gap-6">
      <div>
        <div className="metric-label">Portfolio</div>
        <div className="text-sm font-data">{data.n_assets} assets · ${data.portfolio_value.toLocaleString()}</div>
        {data.failed.length > 0 && <div className="text-xs text-loss">Failed: {data.failed.join(", ")}</div>}
      </div>
      <Metric
        label="EV P&L"
        value={fmtDollar(data.ev_pnl)}
        delta={`${((data.ev_pnl / data.portfolio_value) * 100).toFixed(1)}%`}
        deltaType={data.ev_pnl >= 0 ? "gain" : "loss"}
      />
      <Metric
        label={`Best: ${best?.regime ?? "—"}`}
        value={fmtDollar(best?.pnl ?? 0)}
        delta={best ? fmtPct(best.pnl_pct) : ""}
        deltaType="gain"
      />
      <Metric
        label={`Worst: ${worst?.regime ?? "—"}`}
        value={fmtDollar(worst?.pnl ?? 0)}
        delta={worst ? fmtPct(worst.pnl_pct) : ""}
        deltaType="loss"
      />
      <div>
        <div className="metric-label">80% CI (EV)</div>
        <div className="text-sm font-data">{fmtDollar(data.ev_lo)} to {fmtDollar(data.ev_hi)}</div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 0: MACRO PORTFOLIO SCENARIOS
// ═══════════════════════════════════════════════════════════════

function MacroScenariosTab({
  data, t, L, userProbs, setUserProbs, onRerun, isPending,
}: {
  data: PortfolioImpactResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
  userProbs: Record<string, number>;
  setUserProbs: (p: Record<string, number>) => void;
  onRerun: () => void;
  isPending: boolean;
}) {
  const mc = data.monte_carlo;
  const regimes = data.regimes;
  const regimeResults = data.regime_results;
  const totalProb = Object.values(userProbs).reduce((s, v) => s + v, 0);

  return (
    <div className="space-y-4">
      {/* Regime definitions */}
      <div className="card">
        <div className="text-sm font-semibold mb-2">Macro regime definitions</div>
        <div className="text-xs text-text-muted mb-3">Probabilities calibrated to current conditions (as of early 2026).</div>
        <div className="space-y-2">
          {regimes.map((r) => (
            <details key={r.name} className="border border-border rounded px-3 py-2">
              <summary className="text-sm font-semibold cursor-pointer" style={{ color: REGIME_COLORS[r.name] ?? t.accent }}>
                {r.name} — {r.base_probability}%
              </summary>
              <div className="mt-2 text-xs">
                <div><span className="font-semibold">Scenario:</span> {r.description}</div>
                <div className="mt-1"><span className="font-semibold">Rationale:</span> {r.rationale}</div>
              </div>
            </details>
          ))}
        </div>
      </div>

      {/* User probability inputs */}
      <div className="card card-compact">
        <div className="flex items-center gap-3 mb-3">
          <span className="text-sm font-semibold">Adjust regime probabilities</span>
          {Math.abs(totalProb - 100) > 1 && (
            <span className="text-xs text-spot">Sum: {totalProb}% (should be ~100)</span>
          )}
          <button
            onClick={onRerun}
            disabled={isPending}
            className="ml-auto px-3 py-1 bg-accent text-white font-semibold rounded text-xs hover:bg-accent-hover disabled:opacity-50"
          >
            {isPending ? "Recomputing…" : "Re-run with these probs"}
          </button>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
          {regimes.map((r) => (
            <div key={r.name}>
              <label className="metric-label" style={{ color: REGIME_COLORS[r.name] ?? t.text }}>{r.name}</label>
              <input
                type="number"
                value={userProbs[r.name] ?? r.base_probability}
                onChange={(e) => setUserProbs({ ...userProbs, [r.name]: Number(e.target.value) })}
                min={0}
                max={100}
                step={5}
                className="mt-0.5 w-full px-2 py-1 border border-border rounded text-sm bg-surface font-data"
              />
            </div>
          ))}
        </div>
      </div>

      {/* Driver shift comparison */}
      <div className="card">
        <div className="text-sm font-semibold mb-2">Economic driver shifts by regime</div>
        <div className="overflow-x-auto">
          <table className="data-table text-xs">
            <thead>
              <tr>
                <th>Driver</th><th>Category</th>
                {regimes.map((r) => <th key={r.name}>{r.name}</th>)}
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.fed_drivers).map(([sid, info]) => (
                <tr key={sid}>
                  <td className="font-semibold">{info.name}</td>
                  <td className="text-text-muted">{info.category}</td>
                  {regimes.map((r) => {
                    const move = r.driver_moves[sid] ?? 0;
                    const arrow = move > 0 ? "▲" : move < 0 ? "▼" : "—";
                    const unit = info.unit === "%" || info.yoy ? "%" : "";
                    return (
                      <td
                        key={r.name}
                        className="font-data"
                        style={{ color: move > 0 ? t.gain : move < 0 ? t.loss : t.muted }}
                      >
                        {arrow} {move >= 0 ? "+" : ""}{move.toFixed(1)}{unit}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Regime P&L bars with error bars */}
      <div className="card">
        <div className="text-sm font-semibold mb-1">Portfolio impact by regime — {data.horizon_days}D horizon</div>
        <Plot
          data={[{
            type: "bar" as const,
            x: regimeResults.map((r) => r.regime),
            y: regimeResults.map((r) => r.pnl),
            marker: { color: regimeResults.map((r) => r.pnl >= 0 ? t.gain : t.loss) },
            error_y: {
              type: "data" as const,
              symmetric: false,
              array: regimeResults.map((r) => r.pnl_hi - r.pnl),
              arrayminus: regimeResults.map((r) => r.pnl - r.pnl_lo),
              color: t.muted,
            },
            text: regimeResults.map((r) => `${fmtDollar(r.pnl)}<br>(${(r.prob * 100).toFixed(0)}%)`),
            textposition: "outside" as const,
          }]}
          layout={{
            height: 420,
            ...L,
            yaxis: { title: "Portfolio P&L ($)", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid, tickangle: -15 },
            shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: data.ev_pnl, y1: data.ev_pnl, line: { color: t.accent, dash: "dash", width: 1 } }],
            annotations: [{ xref: "paper" as const, yref: "y" as const, x: 0.02, y: data.ev_pnl, text: `EV: ${fmtDollar(data.ev_pnl)}`, showarrow: false, font: { color: t.accent, size: 11 } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      {/* EV decomposition waterfall */}
      <div className="card">
        <div className="text-sm font-semibold mb-1">EV decomposition by regime</div>
        <div className="text-xs text-text-muted mb-2">How each regime contributes to the probability-weighted expected return.</div>
        {(() => {
          const sorted = [...regimeResults].sort((a, b) => (b.pnl * b.prob) - (a.pnl * a.prob));
          const contribs = sorted.map((r) => (r.pnl * r.prob / data.portfolio_value) * 100);
          const evPct = data.ev_pnl / data.portfolio_value * 100;
          return (
            <Plot
              data={[{
                type: "waterfall" as const,
                x: [...sorted.map((r) => r.regime), "Expected Return"],
                y: [...contribs, evPct],
                measure: [...sorted.map(() => "relative" as const), "total" as const],
                connector: { line: { color: t.muted, width: 1 } },
                increasing: { marker: { color: t.gain } },
                decreasing: { marker: { color: t.loss } },
                totals: { marker: { color: t.accent } },
                text: [...contribs.map((v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`), `${evPct >= 0 ? "+" : ""}${evPct.toFixed(1)}%`],
                textposition: "outside" as const,
              }]}
              layout={{
                height: 380,
                ...L,
                yaxis: { title: "EV contribution (%)", gridcolor: t.grid },
                xaxis: { gridcolor: t.grid },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          );
        })()}
      </div>

      {/* Per-asset table for each regime */}
      <div className="card">
        <div className="text-sm font-semibold mb-2">Per-asset regime sensitivity</div>
        <div className="space-y-2">
          {regimeResults.map((r) => (
            <details key={r.regime} className="border border-border rounded px-3 py-2">
              <summary className="text-sm font-semibold cursor-pointer" style={{ color: REGIME_COLORS[r.regime] ?? t.text }}>
                {r.regime}: {fmtDollar(r.pnl)} ({fmtPct(r.pnl_pct)})
              </summary>
              <table className="data-table text-xs mt-2">
                <thead>
                  <tr><th>Ticker</th><th>Est. Return</th><th>80% CI</th><th>Est. P&L</th><th>R²</th><th>Source</th></tr>
                </thead>
                <tbody>
                  {Object.entries(r.ticker_moves).map(([tk, est]) => (
                    <tr key={tk}>
                      <td className="font-semibold">{tk}</td>
                      <td className={`font-data ${est.point >= 0 ? "text-gain" : "text-loss"}`}>{fmtPct(est.point)}</td>
                      <td className="font-data text-text-muted">{fmtPct(est.lo)} to {fmtPct(est.hi)}</td>
                      <td className={`font-data ${est.point >= 0 ? "text-gain" : "text-loss"}`}>{fmtDollar(data.alloc_per_ticker * (est.point / 100))}</td>
                      <td className="font-data">{est.r2 > 0 ? est.r2.toFixed(2) : "—"}</td>
                      <td className="text-text-muted text-xs">{est.source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          ))}
        </div>
      </div>

      {/* Monte Carlo */}
      <div className="card">
        <div className="text-sm font-semibold mb-1">Simulated outcome distribution — {data.horizon_days}D horizon</div>
        <div className="text-xs text-text-muted mb-2">
          10,000 Monte Carlo draws: randomly select a regime (weighted by probability), draw P&L from Student-t (df=5) for fat tails.
        </div>

        <div className="flex flex-wrap gap-4 mb-3">
          <Metric label="Simulated Mean" value={fmtDollar(mc.mean)} />
          <Metric label="95% VaR" value={fmtDollar(mc.var_95)} deltaType="loss" />
          <Metric label="95% CVaR" value={fmtDollar(mc.cvar_95)} deltaType="loss" />
          <Metric label="P(Loss)" value={`${mc.prob_loss.toFixed(0)}%`} />
          <Metric label="P(Gain)" value={`${mc.prob_gain.toFixed(0)}%`} />
        </div>

        <Plot
          data={[{
            type: "bar" as const,
            x: mc.histogram.edges.slice(0, -1).map((e, i) => (e + mc.histogram.edges[i + 1]) / 2),
            y: mc.histogram.counts,
            marker: { color: t.accent, opacity: 0.7 },
          }]}
          layout={{
            height: 360,
            ...L,
            xaxis: { title: "Portfolio P&L ($)", gridcolor: t.grid },
            yaxis: { title: "Frequency", gridcolor: t.grid },
            bargap: 0.02,
            shapes: [
              { type: "line", x0: 0, x1: 0, yref: "paper", y0: 0, y1: 1, line: { color: t.text, width: 1 } },
              { type: "line", x0: mc.mean, x1: mc.mean, yref: "paper", y0: 0, y1: 1, line: { color: t.spot, dash: "dash", width: 2 } },
              { type: "line", x0: mc.var_95, x1: mc.var_95, yref: "paper", y0: 0, y1: 1, line: { color: t.loss, dash: "dash", width: 2 } },
            ],
            annotations: [
              { xref: "x" as const, yref: "paper" as const, x: 0, y: 1, text: "Breakeven", showarrow: false, font: { color: t.muted, size: 10 }, yshift: 10 },
              { xref: "x" as const, yref: "paper" as const, x: mc.mean, y: 0.95, text: `Mean ${fmtDollar(mc.mean)}`, showarrow: false, font: { color: t.spot, size: 10 } },
              { xref: "x" as const, yref: "paper" as const, x: mc.var_95, y: 0.85, text: `VaR ${fmtDollar(mc.var_95)}`, showarrow: false, font: { color: t.loss, size: 10 } },
            ],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />

        <table className="data-table text-xs mt-3">
          <thead>
            <tr><th>Percentile</th><th>P&L</th><th>Return</th><th>Portfolio Value</th></tr>
          </thead>
          <tbody>
            {[["1","1st (Catastrophic)"],["5","5th (95% VaR)"],["10","10th"],["25","25th"],["50","50th (Median)"],["75","75th"],["90","90th"],["95","95th"],["99","99th"]].map(([p, lbl]) => {
              const v = mc.percentiles[p] ?? 0;
              return (
                <tr key={p}>
                  <td className="font-semibold">{lbl}</td>
                  <td className={`font-data ${v >= 0 ? "text-gain" : "text-loss"}`}>{fmtDollar(v)}</td>
                  <td className={`font-data ${v >= 0 ? "text-gain" : "text-loss"}`}>{((v / data.portfolio_value) * 100).toFixed(1)}%</td>
                  <td className="font-data">${(data.portfolio_value + v).toLocaleString()}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 1: FED & MACRO DRIVERS (inline scorecard + link)
// ═══════════════════════════════════════════════════════════════

const MACRO_KEY_SERIES = ["PCEPILFE", "UNRATE", "FEDFUNDS", "T10Y2Y", "PAYEMS"];

function FedMacroTab({ t, L }: { t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const q = useQuery({
    queryKey: ["scenario-fred-scorecard"],
    queryFn: () => fetchFredBatch(MACRO_KEY_SERIES, 14),
    staleTime: 10 * 60_000,
  });

  const latest = (sid: string): { value?: number; prev?: number; date?: string; prev13?: number } => {
    const records = (q.data?.[sid] as Array<Record<string, unknown>> | undefined) ?? [];
    if (records.length === 0) return {};
    const r = records[records.length - 1];
    const rp = records.length > 1 ? records[records.length - 2] : undefined;
    const r13 = records.length >= 13 ? records[records.length - 13] : undefined;
    return {
      value: Number(r.value ?? 0),
      prev: rp ? Number(rp.value ?? 0) : undefined,
      prev13: r13 ? Number(r13.value ?? 0) : undefined,
      date: String(r.date ?? ""),
    };
  };

  const pce = latest("PCEPILFE");
  const pceYoy = (pce.value && pce.prev13) ? ((pce.value / pce.prev13) - 1) * 100 : undefined;
  const ur = latest("UNRATE");
  const ff = latest("FEDFUNDS");
  const spread = latest("T10Y2Y");
  const nfp = latest("PAYEMS");
  const nfpChange = (nfp.value !== undefined && nfp.prev !== undefined) ? nfp.value - nfp.prev : undefined;

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="text-sm font-semibold mb-2">Fed dual mandate scorecard</div>
        <div className="text-xs text-text-muted mb-3">Maximum employment and price stability (2% inflation target).</div>
        {q.isPending && <div className="text-xs text-text-muted">Loading FRED data…</div>}
        {q.error && <div className="text-xs text-loss">Failed to load FRED data.</div>}
        {q.data && (
          <div className="flex flex-wrap gap-6">
            <Metric label="Core PCE YoY" value={pceYoy !== undefined ? `${pceYoy.toFixed(1)}%` : "—"} />
            <Metric label="Unemployment" value={ur.value !== undefined ? `${ur.value.toFixed(1)}%` : "—"} />
            <Metric label="Fed Funds Rate" value={ff.value !== undefined ? `${ff.value.toFixed(2)}%` : "—"} />
            <Metric
              label="2s10s Spread"
              value={spread.value !== undefined ? `${spread.value.toFixed(2)}%` : "—"}
              delta={spread.value !== undefined ? (spread.value < 0 ? "Inverted" : "Normal") : undefined}
              deltaType={spread.value !== undefined && spread.value < 0 ? "loss" : "gain"}
            />
            <Metric
              label="NFP Change (MoM)"
              value={nfpChange !== undefined ? `${nfpChange >= 0 ? "+" : ""}${nfpChange.toFixed(0)}K jobs` : "—"}
              deltaType={nfpChange !== undefined && nfpChange >= 0 ? "gain" : "loss"}
            />
          </div>
        )}
      </div>

      <div className="card text-sm">
        <div className="font-semibold mb-2">Full Fed & Macro Drivers page</div>
        <div className="text-text-muted text-xs mb-3">
          Detailed signal matrix, sparklines, dot plot, SEP projections, StockTwits sentiment, Polymarket odds, and reaction function live on the dedicated page.
        </div>
        <a
          href="/fed-macro"
          className="inline-block px-4 py-2 bg-accent text-white rounded-lg text-xs font-semibold hover:bg-accent-hover"
        >
          Open Fed & Macro Drivers →
        </a>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 2: HISTORICAL STRESS TESTS (client-side)
// ═══════════════════════════════════════════════════════════════

function HistoricalStressTab({ data, t, L }: {
  data: PortfolioImpactResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const [selected, setSelected] = useState<string[]>(Object.keys(HISTORICAL_SCENARIOS).slice(0, 3));

  const results = useMemo(() => {
    return selected.map((name) => {
      const shocks = HISTORICAL_SCENARIOS[name];
      const alloc = data.portfolio_value / data.n_assets;
      const ticker_impacts: Record<string, number> = {};
      let pnl = 0;
      for (const tk of data.tickers) {
        const shock = shocks[tk] ?? shocks["_default"];
        const impact = alloc * shock;
        pnl += impact;
        ticker_impacts[tk] = shock * 100;
      }
      return { scenario: name, pnl, pnl_pct: (pnl / data.portfolio_value) * 100, ticker_impacts };
    });
  }, [selected, data]);

  const worst = results.reduce<typeof results[0] | null>((a, b) => !a || b.pnl < a.pnl ? b : a, null);
  const best = results.reduce<typeof results[0] | null>((a, b) => !a || b.pnl > a.pnl ? b : a, null);
  const avg = results.length > 0 ? results.reduce((s, r) => s + r.pnl, 0) / results.length : 0;

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="text-xs text-text-muted mb-2">Select crisis scenarios to replay.</div>
        <div className="flex flex-wrap gap-2">
          {Object.keys(HISTORICAL_SCENARIOS).map((name) => {
            const on = selected.includes(name);
            return (
              <button
                key={name}
                onClick={() => setSelected((prev) => on ? prev.filter((p) => p !== name) : [...prev, name])}
                className={`px-2.5 py-1 text-xs font-semibold rounded border ${
                  on ? "bg-accent text-white border-accent" : "bg-surface text-text-muted border-border"
                }`}
              >
                {name}
              </button>
            );
          })}
        </div>
      </div>

      {results.length > 0 && (
        <>
          <div className="card card-compact flex flex-wrap gap-6">
            <Metric label="Worst Case" value={fmtDollar(worst?.pnl ?? 0)} delta={fmtPct(worst?.pnl_pct ?? 0)} deltaType="loss" />
            <Metric label="Best Case" value={fmtDollar(best?.pnl ?? 0)} delta={fmtPct(best?.pnl_pct ?? 0)} deltaType={(best?.pnl ?? 0) >= 0 ? "gain" : "loss"} />
            <Metric label="Average" value={fmtDollar(avg)} delta={`${((avg / data.portfolio_value) * 100).toFixed(1)}%`} deltaType={avg >= 0 ? "gain" : "loss"} />
          </div>

          <div className="card">
            <Plot
              data={[{
                type: "bar" as const,
                x: results.map((r) => r.scenario),
                y: results.map((r) => r.pnl),
                marker: { color: results.map((r) => r.pnl >= 0 ? t.gain : t.loss) },
                text: results.map((r) => fmtDollar(r.pnl)),
                textposition: "outside" as const,
              }]}
              layout={{
                height: 420,
                ...L,
                yaxis: { title: "Portfolio P&L ($)", gridcolor: t.grid },
                xaxis: { gridcolor: t.grid, tickangle: -30 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-2">Per-asset stress impact (%)</div>
            <Plot
              data={[{
                ...heatmapTrace(t, "divergent", { colorbarTitle: "Shock %" }),
                z: results.map((r) => data.tickers.map((tk) => r.ticker_impacts[tk] ?? 0)),
                x: data.tickers,
                y: results.map((r) => r.scenario),
                zmid: 0,
                text: results.map((r) => data.tickers.map((tk) => `${(r.ticker_impacts[tk] ?? 0).toFixed(1)}%`)),
              }]}
              layout={{ height: heatmapHeight(results.length), ...L }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 3: WHAT-IF
// ═══════════════════════════════════════════════════════════════

function WhatIfTab({ data, t, L }: {
  data: PortfolioImpactResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const [shocks, setShocks] = useState<Record<string, number>>(Object.fromEntries(data.tickers.map((tk) => [tk, 0])));
  const [preset, setPreset] = useState<string>("");

  const applyPreset = (name: string) => {
    setPreset(name);
    const p = WHATIF_PRESETS[name];
    if (!p) return;
    const next = { ...shocks };
    data.tickers.forEach((tk) => {
      if (tk in p) next[tk] = p[tk];
    });
    setShocks(next);
  };

  const alloc = data.portfolio_value / data.n_assets;
  const impacts = Object.fromEntries(data.tickers.map((tk) => [tk, alloc * ((shocks[tk] ?? 0) / 100)]));
  const totalPnl = Object.values(impacts).reduce((s, v) => s + v, 0);
  const newVal = data.portfolio_value + totalPnl;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card card-compact">
          <div className="text-sm font-semibold mb-2">Define asset shocks (%)</div>
          <div className="space-y-2">
            {data.tickers.map((tk) => (
              <div key={tk} className="flex items-center gap-3">
                <span className="font-semibold text-xs w-12">{tk}</span>
                <input
                  type="range"
                  value={shocks[tk] ?? 0}
                  onChange={(e) => setShocks({ ...shocks, [tk]: Number(e.target.value) })}
                  min={-80}
                  max={80}
                  step={1}
                  className="flex-1 accent-accent"
                />
                <span className={`font-data text-xs w-12 text-right ${(shocks[tk] ?? 0) >= 0 ? "text-gain" : "text-loss"}`}>
                  {fmtPct(shocks[tk] ?? 0, 0)}
                </span>
              </div>
            ))}
          </div>
          <hr className="border-border my-3" />
          <label className="metric-label">Macro preset</label>
          <select
            value={preset}
            onChange={(e) => applyPreset(e.target.value)}
            className="mt-0.5 w-full px-3 py-1.5 border border-border rounded text-sm bg-surface"
          >
            <option value="">— None —</option>
            {Object.keys(WHATIF_PRESETS).map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </div>

        <div className="lg:col-span-2 space-y-4">
          <div className="card card-compact flex flex-wrap gap-6">
            <Metric label="Current value" value={`$${data.portfolio_value.toLocaleString()}`} />
            <Metric
              label="Scenario value"
              value={`$${newVal.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
              delta={`${totalPnl >= 0 ? "+" : ""}${((totalPnl / data.portfolio_value) * 100).toFixed(1)}%`}
              deltaType={totalPnl >= 0 ? "gain" : "loss"}
            />
            <Metric
              label="Total P&L"
              value={fmtDollar(totalPnl)}
              deltaType={totalPnl >= 0 ? "gain" : "loss"}
            />
          </div>

          <div className="card">
            <Plot
              data={[
                { type: "bar" as const, x: data.tickers, y: data.tickers.map(() => alloc), name: "Before", marker: { color: t.muted } },
                { type: "bar" as const, x: data.tickers, y: data.tickers.map((tk) => alloc + (impacts[tk] ?? 0)), name: "After", marker: { color: data.tickers.map((tk) => (impacts[tk] ?? 0) >= 0 ? t.gain : t.loss) } },
              ]}
              layout={{ height: 340, ...L, barmode: "group" as const, yaxis: { title: "Allocation ($)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, legend: { orientation: "h", y: -0.2 } }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          <div className="card">
            <Plot
              data={[{
                type: "waterfall" as const,
                x: [...data.tickers, "Total"],
                y: [...data.tickers.map((tk) => impacts[tk] ?? 0), totalPnl],
                measure: [...data.tickers.map(() => "relative" as const), "total" as const],
                connector: { line: { color: t.muted, width: 1 } },
                increasing: { marker: { color: t.gain } },
                decreasing: { marker: { color: t.loss } },
                totals: { marker: { color: t.accent } },
                text: [...data.tickers.map((tk) => fmtDollar(impacts[tk] ?? 0)), fmtDollar(totalPnl)],
                textposition: "outside" as const,
              }]}
              layout={{ height: 340, ...L, yaxis: { title: "P&L contribution ($)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 4: BULL / BASE / BEAR (GBM via /gbm-projection)
// ═══════════════════════════════════════════════════════════════

function BullBaseBearTab({ data, t, L }: {
  data: PortfolioImpactResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const [ticker, setTicker] = useState<string>(data.tickers[0] ?? "SPY");
  const [projDays, setProjDays] = useState(252);
  const [numPaths, setNumPaths] = useState(500);
  const [bull, setBull] = useState(25);
  const [base, setBase] = useState(10);
  const [bear, setBear] = useState(-20);

  const run = useMutation({
    mutationFn: () => fetchGbmProjection({
      ticker, lookback: 756, proj_days: projDays, num_paths: numPaths,
      bull_ret: bull, base_ret: base, bear_ret: bear,
    }),
  });

  const gbm: GbmResponse | null = run.data && !run.data.error ? run.data : null;

  const SCENARIO_COLORS: Record<string, string> = { Bull: t.gain, Base: t.accent, Bear: t.loss };

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
          <div>
            <label className="metric-label">Ticker</label>
            <select value={ticker} onChange={(e) => setTicker(e.target.value)} className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface">
              {data.tickers.map((tk) => <option key={tk} value={tk}>{tk}</option>)}
            </select>
          </div>
          <div>
            <label className="metric-label">Horizon (days)</label>
            <input type="number" value={projDays} onChange={(e) => setProjDays(Number(e.target.value))} min={30} max={504} step={21} className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data" />
          </div>
          <div>
            <label className="metric-label">Paths</label>
            <select value={numPaths} onChange={(e) => setNumPaths(Number(e.target.value))} className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data">
              <option value={100}>100</option>
              <option value={500}>500</option>
              <option value={1000}>1000</option>
              <option value={2000}>2000</option>
            </select>
          </div>
          <div>
            <label className="metric-label">Bull (% ann.)</label>
            <input type="number" value={bull} onChange={(e) => setBull(Number(e.target.value))} step={5} className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data" />
          </div>
          <div>
            <label className="metric-label">Base (% ann.)</label>
            <input type="number" value={base} onChange={(e) => setBase(Number(e.target.value))} step={5} className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data" />
          </div>
          <div>
            <label className="metric-label">Bear (% ann.)</label>
            <input type="number" value={bear} onChange={(e) => setBear(Number(e.target.value))} step={5} className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data" />
          </div>
        </div>
        <button
          onClick={() => run.mutate()}
          disabled={run.isPending}
          className="mt-3 px-5 py-2 bg-accent text-white rounded-lg text-xs font-semibold hover:bg-accent-hover disabled:opacity-50"
        >
          {run.isPending ? "Simulating…" : "Simulate GBM paths"}
        </button>
      </div>

      {gbm && (
        <>
          <div className="card card-compact flex flex-wrap gap-6">
            <Metric label="Spot" value={`$${gbm.spot.toFixed(2)}`} />
            <Metric label="Historical vol (ann.)" value={`${(gbm.hist_vol * 100).toFixed(1)}%`} />
            <Metric label="Projection days" value={`${projDays}D`} />
          </div>

          <div className="card">
            <Plot
              data={[
                {
                  x: gbm.history.dates.map((_, i) => -(gbm.history.dates.length - 1 - i)),
                  y: gbm.history.closes,
                  type: "scatter" as const,
                  mode: "lines" as const,
                  name: "History",
                  line: { color: t.text, width: 2 },
                },
                ...Object.entries(gbm.scenarios).flatMap(([name, s]) => {
                  const color = SCENARIO_COLORS[name] ?? t.muted;
                  const x = s.mean_path.map((_, i) => i);
                  return [
                    {
                      x: [...x, ...x.slice().reverse()],
                      y: [...s.p90_path, ...s.p10_path.slice().reverse()],
                      type: "scatter" as const,
                      mode: "lines" as const,
                      fill: "toself" as const,
                      fillcolor: color + "20",
                      line: { width: 0 },
                      showlegend: false,
                      hoverinfo: "skip" as const,
                      name: `${name} CI`,
                    },
                    {
                      x,
                      y: s.mean_path,
                      type: "scatter" as const,
                      mode: "lines" as const,
                      name: `${name} (${s.annual_ret >= 0 ? "+" : ""}${s.annual_ret.toFixed(0)}%)`,
                      line: { color, width: 2, dash: "dash" as const },
                    },
                  ];
                }),
              ]}
              layout={{
                height: 500,
                ...L,
                xaxis: { title: "Trading days (0 = now)", gridcolor: t.grid },
                yaxis: { title: "Price ($)", gridcolor: t.grid },
                legend: { orientation: "h", y: -0.15 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-2">Terminal price distribution</div>
            <table className="data-table text-xs">
              <thead>
                <tr><th>Scenario</th><th>Median</th><th>Mean</th><th>10th Pct</th><th>90th Pct</th><th>P(Profit)</th></tr>
              </thead>
              <tbody>
                {Object.entries(gbm.scenarios).map(([name, s]) => (
                  <tr key={name}>
                    <td className="font-semibold" style={{ color: SCENARIO_COLORS[name] ?? t.text }}>{name}</td>
                    <td className="font-data">${s.median_terminal.toFixed(2)}</td>
                    <td className="font-data">${s.mean_terminal.toFixed(2)}</td>
                    <td className="font-data">${s.p10_terminal.toFixed(2)}</td>
                    <td className="font-data">${s.p90_terminal.toFixed(2)}</td>
                    <td className="font-data">{s.prob_profit.toFixed(0)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 5: EVENT-DRIVEN CATALYSTS (client-side)
// ═══════════════════════════════════════════════════════════════

function EventDrivenTab({ data, t, L }: {
  data: PortfolioImpactResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const [ticker, setTicker] = useState(data.tickers[0] ?? "SPY");
  const [preset, setPreset] = useState<string>("FOMC Rate Decision");
  const [outcomes, setOutcomes] = useState(CATALYST_PRESETS[preset]);

  const applyPreset = (name: string) => {
    setPreset(name);
    setOutcomes(CATALYST_PRESETS[name] ?? []);
  };

  const alloc = data.portfolio_value / data.n_assets;
  const totalProb = outcomes.reduce((s, o) => s + o.prob, 0);
  const evMove = outcomes.reduce((s, o) => s + (o.prob / 100) * (o.move / 100), 0);
  const evPnl = alloc * evMove;

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="metric-label">Ticker</label>
            <select value={ticker} onChange={(e) => setTicker(e.target.value)} className="mt-0.5 px-3 py-1.5 border border-border rounded text-sm bg-surface">
              {data.tickers.map((tk) => <option key={tk} value={tk}>{tk}</option>)}
            </select>
          </div>
          <div>
            <label className="metric-label">Catalyst preset</label>
            <select value={preset} onChange={(e) => applyPreset(e.target.value)} className="mt-0.5 px-3 py-1.5 border border-border rounded text-sm bg-surface">
              {Object.keys(CATALYST_PRESETS).map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Outcome branches</div>
        <table className="data-table text-xs">
          <thead>
            <tr><th>Label</th><th>Probability (%)</th><th>Expected Move (%)</th></tr>
          </thead>
          <tbody>
            {outcomes.map((o, i) => (
              <tr key={i}>
                <td>
                  <input
                    value={o.name}
                    onChange={(e) => setOutcomes(outcomes.map((x, j) => j === i ? { ...x, name: e.target.value } : x))}
                    className="w-full px-2 py-1 border border-border rounded text-xs bg-surface"
                  />
                </td>
                <td>
                  <input
                    type="number"
                    value={o.prob}
                    onChange={(e) => setOutcomes(outcomes.map((x, j) => j === i ? { ...x, prob: Number(e.target.value) } : x))}
                    step={5}
                    className="w-full px-2 py-1 border border-border rounded text-xs bg-surface font-data"
                  />
                </td>
                <td>
                  <input
                    type="number"
                    value={o.move}
                    onChange={(e) => setOutcomes(outcomes.map((x, j) => j === i ? { ...x, move: Number(e.target.value) } : x))}
                    step={0.5}
                    className="w-full px-2 py-1 border border-border rounded text-xs bg-surface font-data"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {Math.abs(totalProb - 100) > 1 && (
          <div className="mt-2 text-xs text-spot">Probabilities sum to {totalProb}% — should be ~100.</div>
        )}
        <div className="mt-2 flex gap-2">
          <button
            onClick={() => setOutcomes([...outcomes, { name: `Outcome ${outcomes.length + 1}`, prob: 0, move: 0 }])}
            className="px-3 py-1 text-xs rounded bg-surface-alt border border-border"
          >
            + Add outcome
          </button>
          {outcomes.length > 2 && (
            <button
              onClick={() => setOutcomes(outcomes.slice(0, -1))}
              className="px-3 py-1 text-xs rounded bg-surface-alt border border-border"
            >
              − Remove last
            </button>
          )}
        </div>
      </div>

      {outcomes.length > 0 && totalProb > 0 && (
        <>
          <div className="card card-compact flex flex-wrap gap-6">
            <Metric label="Expected Move" value={fmtPct(evMove * 100, 2)} deltaType={evMove >= 0 ? "gain" : "loss"} />
            <Metric label="Expected P&L (position)" value={fmtDollar(evPnl)} deltaType={evPnl >= 0 ? "gain" : "loss"} />
            <Metric label={`${ticker} alloc`} value={fmtDollar(alloc, false)} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="card">
              <div className="text-sm font-semibold mb-1">Outcome distribution</div>
              <Plot
                data={[{
                  type: "pie" as const,
                  labels: outcomes.map((o) => o.name),
                  values: outcomes.map((o) => o.prob),
                  hole: 0.4,
                  textinfo: "label+percent" as const,
                }]}
                layout={{ height: 360, ...L, showlegend: false }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>

            <div className="card">
              <div className="text-sm font-semibold mb-1">P&L by outcome</div>
              <Plot
                data={[{
                  type: "bar" as const,
                  x: outcomes.map((o) => o.name),
                  y: outcomes.map((o) => alloc * (o.move / 100)),
                  marker: { color: outcomes.map((o) => o.move >= 0 ? t.gain : t.loss) },
                  text: outcomes.map((o) => fmtDollar(alloc * (o.move / 100))),
                  textposition: "outside" as const,
                }]}
                layout={{ height: 360, ...L, yaxis: { title: "Position P&L ($)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid, tickangle: -20 } }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-2">Price outcome ladder</div>
            <table className="data-table text-xs">
              <thead>
                <tr><th>Outcome</th><th>Probability</th><th>Move</th><th>Position P&L</th></tr>
              </thead>
              <tbody>
                {[...outcomes].sort((a, b) => b.move - a.move).map((o, i) => (
                  <tr key={i}>
                    <td className="font-semibold">{o.name}</td>
                    <td className="font-data">{o.prob.toFixed(0)}%</td>
                    <td className={`font-data ${o.move >= 0 ? "text-gain" : "text-loss"}`}>{fmtPct(o.move, 1)}</td>
                    <td className={`font-data ${o.move >= 0 ? "text-gain" : "text-loss"}`}>{fmtDollar(alloc * (o.move / 100))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 6: MODEL DIAGNOSTICS
// ═══════════════════════════════════════════════════════════════

function DiagnosticsTab({ data, t, L }: {
  data: PortfolioImpactResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const diags = data.factor_diagnostics;
  const weak = diags.filter((d) => d.r2 > 0 && d.r2 < 0.05).map((d) => d.ticker);
  const unstable = diags.filter((d) => d.beta_stability > 0 && d.beta_stability < 0.3).map((d) => d.ticker);

  const factorNames = data.factor_series.concat(["VIX_HY"]);
  const betaHeatZ = diags.map((d) => factorNames.map((f) => (d.betas?.[f] ?? 0) * 10000));
  const friendlyFactorNames = factorNames.map((f) =>
    f.replace("BAMLH0A0HYM2", "HY").replace("DCOILWTICO", "Oil").replace("VIXCLS", "VIX")
      .replace("DGS10", "10Y").replace("T5YIE", "5Y BE").replace("DTWEXBGS", "USD").replace("VIX_HY", "VIX×HY")
  );

  return (
    <div className="space-y-4">
      <div className="card card-compact flex flex-wrap gap-6">
        <Metric label="Tickers modeled" value={`${diags.filter((d) => d.r2 > 0).length}/${data.n_assets}`} />
        <Metric label="Avg R²" value={data.avg_r2.toFixed(2)} />
        <Metric label="Avg beta stability" value={data.avg_stability.toFixed(2)} />
        <Metric label="Factors" value="7" />
      </div>

      {(weak.length > 0 || unstable.length > 0) && (
        <div className="card card-compact space-y-2 text-xs">
          {weak.length > 0 && (
            <div className="text-spot">
              <span className="font-semibold">Low R²</span> (factor model explains &lt;5% of variance): {weak.join(", ")}
            </div>
          )}
          {unstable.length > 0 && (
            <div className="text-spot">
              <span className="font-semibold">Unstable betas</span>: {unstable.join(", ")}
            </div>
          )}
        </div>
      )}

      {data.concentration.warnings.length > 0 && (
        <div className="card card-compact text-xs text-spot">
          <div className="font-semibold mb-1">Sector concentration</div>
          <ul className="list-disc list-inside space-y-0.5">
            {data.concentration.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}

      <div className="card">
        <div className="text-sm font-semibold mb-2">Per-ticker diagnostics</div>
        <table className="data-table text-xs">
          <thead>
            <tr><th>Ticker</th><th>R²</th><th>Beta stability</th><th>Observations</th><th>Residual std</th><th>Stressed std</th><th>Sector</th></tr>
          </thead>
          <tbody>
            {diags.map((d) => (
              <tr key={d.ticker}>
                <td className="font-semibold">{d.ticker}</td>
                <td className="font-data">{d.r2.toFixed(3)}</td>
                <td className="font-data">{d.beta_stability.toFixed(2)}</td>
                <td className="font-data">{d.n_obs}</td>
                <td className="font-data">{(d.residual_std * 100).toFixed(3)}%</td>
                <td className="font-data">{(d.stressed_residual_std * 100).toFixed(3)}%</td>
                <td className="text-text-muted">{d.sector}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Factor beta profiles (bps per unit change)</div>
        <Plot
          data={[{
            ...heatmapTrace(t, "correlation", { colorbarTitle: "Sensitivity (bps)" }),
            z: betaHeatZ,
            x: friendlyFactorNames,
            y: diags.map((d) => d.ticker),
            zmid: 0,
            text: betaHeatZ.map((row) => row.map((v) => v === 0 ? "" : v.toFixed(1))),
          }]}
          layout={{ height: heatmapHeight(diags.length), ...L }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">R² + beta stability</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Plot
            data={[{
              type: "bar" as const,
              x: diags.map((d) => d.ticker),
              y: diags.map((d) => d.r2),
              marker: { color: diags.map((d) => d.r2 >= 0.1 ? t.gain : d.r2 >= 0.05 ? t.spot : t.loss) },
              text: diags.map((d) => d.r2.toFixed(3)),
              textposition: "outside" as const,
            }]}
            layout={{ height: 280, ...L, yaxis: { title: "R²", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, title: { text: "R² (model fit)", font: { size: 12 } } }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
          <Plot
            data={[{
              type: "bar" as const,
              x: diags.map((d) => d.ticker),
              y: diags.map((d) => d.beta_stability),
              marker: { color: diags.map((d) => d.beta_stability >= 0.5 ? t.gain : d.beta_stability >= 0.3 ? t.spot : t.loss) },
              text: diags.map((d) => d.beta_stability.toFixed(2)),
              textposition: "outside" as const,
            }]}
            layout={{ height: 280, ...L, yaxis: { title: "Beta stability", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, title: { text: "Beta stability", font: { size: 12 } } }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
        <div className="text-xs text-text-muted mt-2">R²: green ≥ 0.10, yellow ≥ 0.05, red &lt; 0.05. Stability: green ≥ 0.50, yellow ≥ 0.30, red &lt; 0.30.</div>
      </div>

      {data.correlation.normal && data.correlation.stressed && (
        <div className="card">
          <div className="text-sm font-semibold mb-2">Correlation: normal vs stressed periods</div>
          <div className="text-xs text-text-muted mb-3">Higher stressed correlations = diversification breaks down when you need it most.</div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <Plot
              data={[{
                ...heatmapTrace(t, "divergent"),
                z: data.correlation.normal,
                x: data.correlation.normal_methods,
                y: data.correlation.normal_methods,
                zmid: 0, zmin: -1, zmax: 1,
                text: data.correlation.normal.map((row) => row.map((v) => v.toFixed(2))),
              }]}
              layout={{ height: 320, ...L, title: { text: "Normal periods", font: { size: 12 } } }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
            <Plot
              data={[{
                ...heatmapTrace(t, "divergent"),
                z: data.correlation.stressed,
                x: data.correlation.stressed_methods,
                y: data.correlation.stressed_methods,
                zmid: 0, zmin: -1, zmax: 1,
                text: data.correlation.stressed.map((row) => row.map((v) => v.toFixed(2))),
              }]}
              layout={{ height: 320, ...L, title: { text: "Stressed periods (high VIX)", font: { size: 12 } } }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 7: REGIME TRACK RECORD
// ═══════════════════════════════════════════════════════════════

function TrackRecordTab({ t, L }: { t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const trackQ = useQuery({ queryKey: ["scenario-track"], queryFn: fetchRegimeTrackRecord, staleTime: 30 * 60_000 });
  const grokQ = useQuery({ queryKey: ["scenario-grok"], queryFn: fetchGrokLatest, staleTime: 30 * 60_000 });

  const track: RegimeTrackResponse | undefined = trackQ.data;
  const grok: GrokLatestResponse | undefined = grokQ.data;

  return (
    <div className="space-y-4">
      <div className="card card-compact text-xs">
        <div className="font-semibold text-sm mb-1">Regime prediction track record</div>
        <div className="text-text-muted">
          Each time Grok assigns regime probabilities, this tab compares the top regime&apos;s expected market direction to what SPY actually did over the following 30 days.
        </div>
      </div>

      {grok?.available && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">Latest Grok analysis</div>
          <div className="text-xs text-text-muted mb-3">Cached — last updated {grok.timestamp}</div>
          <div className="flex flex-wrap gap-4 mb-3">
            {(grok.regimes ?? []).map((r) => (
              <div key={r.name} className="border border-border rounded px-3 py-2">
                <div className="metric-label" style={{ color: REGIME_COLORS[r.name] ?? t.text }}>{r.name}</div>
                <div className="font-data text-lg font-bold">{r.probability}%</div>
              </div>
            ))}
          </div>
          {grok.sentiment_summary && (
            <div className="text-xs bg-surface-alt border border-border rounded p-2">
              <span className="font-semibold">Sentiment pulse:</span> {grok.sentiment_summary}
            </div>
          )}
          {grok.change_summary && (
            <div className="text-xs bg-surface-alt border border-border rounded p-2 mt-2">
              <span className="font-semibold">Change from prior:</span> {grok.change_summary}
            </div>
          )}
        </div>
      )}

      {trackQ.isPending && <div className="card text-xs text-text-muted">Evaluating historical predictions…</div>}
      {track?.error && <div className="card border-loss text-loss text-sm">{track.error}</div>}

      {track && (
        <div className="card card-compact flex flex-wrap gap-6">
          <Metric label="History entries" value={String(track.history_count)} />
          <Metric label="Regime calls evaluated" value={String(track.evaluations_count)} />
          <Metric label="Directional predictions" value={String(track.directional_count)} />
          <Metric
            label="30-day accuracy"
            value={track.accuracy !== null ? `${(track.accuracy * 100).toFixed(0)}%` : "—"}
            deltaType={track.accuracy !== null ? (track.accuracy > 0.55 ? "gain" : track.accuracy > 0.50 ? "neutral" : "loss") : "neutral"}
          />
        </div>
      )}

      {track && track.evaluations.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-2">Evaluation log</div>
          <table className="data-table text-xs">
            <thead>
              <tr><th>Date</th><th>Top regime</th><th>Probability</th><th>Expected</th><th>SPY 30d</th><th>Actual</th><th>Correct</th></tr>
            </thead>
            <tbody>
              {track.evaluations.map((e, i) => (
                <tr key={i}>
                  <td className="font-data">{e.date}</td>
                  <td className="font-semibold" style={{ color: REGIME_COLORS[e.top_regime] ?? t.text }}>{e.top_regime}</td>
                  <td className="font-data">{e.probability}%</td>
                  <td className={`font-semibold ${e.expected === "Bullish" ? "text-gain" : e.expected === "Bearish" ? "text-loss" : "text-text-muted"}`}>{e.expected}</td>
                  <td className={`font-data ${e.spy_30d >= 0 ? "text-gain" : "text-loss"}`}>{e.spy_30d >= 0 ? "+" : ""}{e.spy_30d.toFixed(1)}%</td>
                  <td className={`font-semibold ${e.actual === "Bullish" ? "text-gain" : "text-loss"}`}>{e.actual}</td>
                  <td className={`font-semibold ${e.correct === true ? "text-gain" : e.correct === false ? "text-loss" : "text-text-muted"}`}>{e.correct === true ? "Yes" : e.correct === false ? "No" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {track && track.evaluations.length === 0 && (
        <div className="card card-compact text-xs text-text-muted">
          {track.history_count < 2
            ? "Need at least 2 regime analyses to evaluate accuracy. Grok runs hourly in the Streamlit app."
            : "No evaluable predictions yet (need predictions older than 30 days with SPY data)."}
        </div>
      )}
    </div>
  );
}
