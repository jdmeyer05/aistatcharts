"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import {
  scanIronCondors, addPosition,
  type ICResult, type ICScanConfig, type ICStressScenario,
} from "@/lib/api";
import { getChartTheme } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { FreshnessBar } from "@/components/ui/freshness-dot";
import { Plot } from "@/components/plot";


const DEFAULT_TICKERS = [
  "SPY", "QQQ", "IWM", "DIA", "AAPL", "TSLA", "NVDA", "AMD", "AMZN", "META",
  "MSFT", "GOOGL", "NFLX", "GLD", "SMH", "XLF", "TLT", "EEM", "JPM", "BA",
];

function fmtExp(exp: string) {
  try { return new Date(exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }); }
  catch { return exp; }
}

const LIQ_COLORS: Record<string, string> = { A: "text-gain", B: "text-gain", C: "text-warn", D: "text-warn", F: "text-loss" };
const BAND_COLORS: Record<string, string> = { Optimal: "badge-gain", Normal: "badge-info", Extreme: "badge-warn", Low: "badge-loss" };
const FILL_PCT: Record<string, number> = { A: 40, B: 30, C: 20, D: 10, F: 5 };

// DGTV institutional limits
const DGTV_LIMITS = { delta: 0.30, gamma: 0.03, vega: 0.20 };

export function IronCondorContent() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");

  // ── Scan config state ──
  const [tickers, setTickers] = useState(DEFAULT_TICKERS.join(", "));
  const [dteMin, setDteMin] = useState(7);
  const [dteMax, setDteMax] = useState(90);
  const [shortDelta, setShortDelta] = useState(0.25);
  const [wingWidth, setWingWidth] = useState(10);
  const [profitTarget, setProfitTarget] = useState(50);
  const [stopMult, setStopMult] = useState(1.5);
  const [accountSize, setAccountSize] = useState(25000);
  const [maxRiskPct, setMaxRiskPct] = useState(5.0);
  const [kellyFrac, setKellyFrac] = useState(0.5);
  const [winRateBump, setWinRateBump] = useState(12);

  // ── Pre-scan filters ──
  const [ivrFilter, setIvrFilter] = useState("All");
  const [minVrp, setMinVrp] = useState(0);
  const [excludeEarnings, setExcludeEarnings] = useState(false);

  // ── Results state ──
  const [results, setResults] = useState<ICResult[]>([]);
  const [scanTime, setScanTime] = useState<Date | null>(null);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [sortBy, setSortBy] = useState("adj_score");
  const [minPop, setMinPop] = useState(40);
  const [minLiq, setMinLiq] = useState("Any");
  const [showN, setShowN] = useState("All");
  const [evOnly, setEvOnly] = useState(false);
  const [booked, setBooked] = useState<Set<string>>(new Set());
  const [detailTab, setDetailTab] = useState(0);
  const [showKelly, setShowKelly] = useState(false);
  const [showHowItWorks, setShowHowItWorks] = useState(false);

  const scan = useMutation({
    mutationFn: (config: Partial<ICScanConfig>) => scanIronCondors(config),
    onSuccess: (data) => { setResults(data.results); setScanTime(new Date()); setSelectedIdx(0); },
  });

  function handleScan() {
    scan.mutate({
      tickers: tickers.split(",").map(t => t.trim().toUpperCase()).filter(Boolean),
      dte_min: dteMin, dte_max: dteMax, short_delta: shortDelta, wing_width: wingWidth,
      profit_target_pct: profitTarget, stop_multiplier: stopMult,
      account_size: accountSize, max_risk_pct: maxRiskPct, kelly_fraction: kellyFrac,
      win_rate_bump: winRateBump,
    });
  }

  // ── Filtering ──
  const LIQ_ORDER: Record<string, number> = { A: 4, B: 3, C: 2, D: 1, F: 0 };
  let filtered = [...results];

  // Pre-scan filters (IVR, VRP, earnings)
  if (ivrFilter === "≥30") filtered = filtered.filter(r => r.ivr == null || r.ivr >= 30);
  else if (ivrFilter === "50-75") filtered = filtered.filter(r => r.ivr != null && r.ivr >= 50 && r.ivr <= 75);
  else if (ivrFilter === "≥50") filtered = filtered.filter(r => r.ivr == null || r.ivr >= 50);
  else if (ivrFilter === "≥75") filtered = filtered.filter(r => r.ivr == null || r.ivr >= 75);
  if (minVrp !== 0) filtered = filtered.filter(r => r.vrp != null && r.vrp >= minVrp);
  if (excludeEarnings) filtered = filtered.filter(r => !r.earnings_before);

  // Post-scan filters
  filtered = filtered.filter(r => r.pop >= minPop);
  if (minLiq !== "Any") {
    const minVal = { "D+": 1, "C+": 2, "B+": 3, "A": 4 }[minLiq] ?? 0;
    filtered = filtered.filter(r => (LIQ_ORDER[r.liq_grade] ?? 0) >= minVal);
  }
  if (evOnly) filtered = filtered.filter(r => r.ev_per_contract > 0);

  // Sort
  filtered.sort((a, b) => {
    if (sortBy === "liq_grade") return (LIQ_ORDER[b.liq_grade] ?? 0) - (LIQ_ORDER[a.liq_grade] ?? 0);
    if (sortBy === "managed_wr") {
      const awr = a.hist_winrate?.win_rate ?? a.managed_wr ?? 0;
      const bwr = b.hist_winrate?.win_rate ?? b.managed_wr ?? 0;
      return bwr - awr;
    }
    return ((b as any)[sortBy] ?? 0) - ((a as any)[sortBy] ?? 0);
  });
  const preTopN = filtered.length;
  if (showN === "Top 5") filtered = filtered.slice(0, 5);
  else if (showN === "Top 10") filtered = filtered.slice(0, 10);
  else if (showN === "Top 20") filtered = filtered.slice(0, 20);

  const selected = filtered[selectedIdx] || null;
  const ageMin = scanTime ? (Date.now() - scanTime.getTime()) / 60000 : null;

  // Market hours check (ET via Intl.DateTimeFormat)
  const isMarketHours = (() => {
    if (!scanTime) return true;
    const fmt = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "numeric", weekday: "short", hour12: false });
    const parts = Object.fromEntries(fmt.formatToParts(scanTime).map(p => [p.type, p.value]));
    const hour = parseInt(parts.hour, 10);
    const min = parseInt(parts.minute, 10);
    const day = parts.weekday;
    const weekend = day === "Sat" || day === "Sun";
    const etMinutes = hour * 60 + min;
    return !weekend && etMinutes >= 570 && etMinutes < 960; // 9:30=570, 16:00=960
  })();

  async function handleBook(r: ICResult) {
    try {
      await addPosition({
        ticker: r.ticker, type: "iron_condor", qty: r.contracts || 1,
        entry_price: r.fill_estimate / 100,
        details: {
          strategy: "iron_condor", short_put: r.short_put, long_put: r.long_put,
          short_call: r.short_call, long_call: r.long_call, expiration: r.expiration,
          dte: r.dte, credit: r.credit, max_risk: r.max_risk, pop: r.pop,
        },
        source_page: "next_iron_condor_scanner",
      });
      setBooked(prev => new Set(prev).add(r.ticker + r.expiration));
    } catch (e) { console.error("Book failed:", e); }
  }

  const DETAIL_TABS = ["Overview", "Management", "Compare Exps", "Greeks", "Results Table"];

  return (
    <div className="space-y-4">
      {/* How it works expander */}
      <button onClick={() => setShowHowItWorks(!showHowItWorks)}
        className="text-xs text-accent hover:underline">
        {showHowItWorks ? "▾ Hide" : "▸ How this scanner works"}
      </button>
      {showHowItWorks && (
        <div className="card text-xs text-text-muted space-y-2">
          <p><strong>What it does:</strong> Scans tickers for the best short iron condor setups using 7 quantitative signals.</p>
          <p><strong>Composite score:</strong> Credit/Risk × POP (base), multiplied by IVR band (50-75 optimal), VRP (IV−HV20), liquidity (A-F), earnings penalty, historical managed win rate, and theta efficiency.</p>
          <p><strong>Management framework:</strong> Close at 50% profit target (default). Stop at 1.5× credit. Time stop at 21 DTE. Roll untested side when short leg hits 30Δ.</p>
          <p><strong>Data:</strong> Polygon (chains, prices), yfinance (earnings), FOMC calendar (events).</p>
        </div>
      )}

      {/* Config */}
      <div className="card">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
          {([
            ["Min DTE", dteMin, setDteMin, 1, "Range: 1-90 days"],
            ["Max DTE", dteMax, setDteMax, 1, "Range: 14-180 days"],
            ["Short Delta", shortDelta, setShortDelta, 0.01, "0.16≈1σ, 0.25≈standard, 0.30≈aggressive"],
            ["Wing Width ($)", wingWidth, setWingWidth, 1, "~1/10th of underlying is optimal"],
            ["Profit Target (%)", profitTarget, setProfitTarget, 5, "50% is the standard playbook"],
          ] as [string, number, (v: number) => void, number, string][]).map(([label, val, setter, step, hint]) => (
            <div key={label}>
              <label className="metric-label">{label}</label>
              <input type="number" step={step} value={val}
                onChange={e => setter(+e.target.value)}
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
              <span className="text-[0.55rem] text-text-muted">{hint}</span>
            </div>
          ))}
        </div>

        {/* Pre-scan filters */}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-3">
          <div>
            <label className="metric-label">IVR Filter</label>
            <select value={ivrFilter} onChange={e => setIvrFilter(e.target.value)}
              className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm bg-surface">
              <option value="All">All</option>
              <option value="≥30">≥30</option>
              <option value="50-75">50-75 (Optimal)</option>
              <option value="≥50">≥50</option>
              <option value="≥75">≥75</option>
            </select>
            <span className="text-[0.55rem] text-text-muted">50-75 is the quant-optimal zone</span>
          </div>
          <div>
            <label className="metric-label">Min VRP (IV − HV20)</label>
            <input type="number" step={1} value={minVrp} onChange={e => setMinVrp(+e.target.value)}
              className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
            <span className="text-[0.55rem] text-text-muted">Positive = structural edge</span>
          </div>
          <div className="flex items-end gap-2 pb-1">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={excludeEarnings} onChange={e => setExcludeEarnings(e.target.checked)}
                className="rounded border-border" />
              Exclude earnings
            </label>
            <span className="text-[0.55rem] text-text-muted">Jump-diffusion risk</span>
          </div>
        </div>

        <textarea value={tickers} onChange={e => setTickers(e.target.value)} rows={2}
          className="w-full px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface mb-3" />

        {/* Kelly Criterion collapsible */}
        <button onClick={() => setShowKelly(!showKelly)}
          className="text-xs text-accent hover:underline mb-2">
          {showKelly ? "▾ Position Sizing (Kelly Criterion)" : "▸ Position Sizing (Kelly Criterion)"}
        </button>
        {showKelly && (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-3 p-3 rounded-lg bg-surface-alt border border-border">
            <div>
              <label className="metric-label">Account Size ($)</label>
              <input type="number" step={5000} value={accountSize} onChange={e => setAccountSize(+e.target.value)}
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
            </div>
            <div>
              <label className="metric-label">Hard Cap per Trade (%)</label>
              <input type="number" step={0.5} value={maxRiskPct} onChange={e => setMaxRiskPct(+e.target.value)}
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
              <span className="text-[0.55rem] text-text-muted">Overrides Kelly if higher</span>
            </div>
            <div>
              <label className="metric-label">Kelly Fraction</label>
              <input type="number" step={0.1} min={0.1} max={1} value={kellyFrac} onChange={e => setKellyFrac(+e.target.value)}
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
              <span className="text-[0.55rem] text-text-muted">0.5 = Half-Kelly (institutional)</span>
            </div>
            <div>
              <label className="metric-label">Stop Loss (× credit)</label>
              <input type="number" step={0.25} min={0.5} max={3} value={stopMult} onChange={e => setStopMult(+e.target.value)}
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
              <span className="text-[0.55rem] text-text-muted">2× conservative, 1.5× common</span>
            </div>
            <div>
              <label className="metric-label">Managed WR Bump (pp)</label>
              <input type="number" step={2} min={0} max={25} value={winRateBump} onChange={e => setWinRateBump(+e.target.value)}
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
              <span className="text-[0.55rem] text-text-muted">Closing at 50% adds ~10-15pp</span>
            </div>
            <div className="flex items-end">
              <p className="text-[0.6rem] text-text-muted">
                Kelly: f* = (p×b − q) / b. Win = profit at target. Loss = stop × credit. Half-Kelly is institutional standard.
              </p>
            </div>
          </div>
        )}

        <button onClick={handleScan} disabled={scan.isPending}
          className="w-full py-2.5 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover
                     disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
          {scan.isPending ? "Scanning..." : "Scan for Iron Condors"}
        </button>
        {scan.isPending && (
          <div className="mt-3 text-center">
            <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
            <p className="text-sm text-text-muted mt-2">Scanning {tickers.split(",").filter(Boolean).length} tickers...</p>
          </div>
        )}
      </div>

      {results.length > 0 && (
        <>
          {/* Market hours / stale data warnings */}
          {!isMarketHours && (
            <div className="card card-compact border-warn/30 bg-warn-bg text-warn text-sm">
              Scanned outside market hours — quotes may be stale. Re-scan after 9:30 AM ET for live data.
            </div>
          )}
          {isMarketHours && ageMin != null && ageMin > 30 && (
            <div className="card card-compact border-accent/30 text-sm text-text-muted">
              Results are {ageMin < 60 ? `${ageMin.toFixed(0)}min` : `${(ageMin / 60).toFixed(1)}hr`} old. Consider re-scanning for fresh quotes.
            </div>
          )}

          <FreshnessBar sources={[
            { label: "Chains", ageMinutes: ageMin, greenThreshold: 30, yellowThreshold: 120 },
            { label: "Prices", ageMinutes: ageMin, greenThreshold: 10, yellowThreshold: 30 },
            { label: "IVR", ageMinutes: ageMin, greenThreshold: 60, yellowThreshold: 240 },
          ]} />

          {/* Portfolio Summary Bar */}
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Setups" value={String(filtered.length)} />
              <Metric label="Total Contracts" value={String(filtered.reduce((s, r) => s + (r.contracts || 0), 0))} />
              <Metric label="Total Credit" value={`$${filtered.reduce((s, r) => s + (r.total_credit || 0), 0).toLocaleString()}`} />
              <Metric label="Total Risk" value={`$${filtered.reduce((s, r) => s + (r.total_risk || 0), 0).toLocaleString()}`} />
              <Metric label="Earnings Risk" value={`${filtered.filter(r => r.earnings_before).length}`} />
              <Metric label="Low Liquidity" value={`${filtered.filter(r => r.liq_grade === "D" || r.liq_grade === "F").length}`} />
            </div>
          </div>

          {/* Filters */}
          <div className="card card-compact">
            <div className="flex flex-wrap items-center gap-3">
              {([
                ["Sort", sortBy, setSortBy, { Score: "adj_score", POP: "pop", Credit: "credit", IVR: "ivr", VRP: "vrp", "Hist WR": "managed_wr", Liquidity: "liq_grade" }],
                ["Show", showN, setShowN, { All: "All", "Top 5": "Top 5", "Top 10": "Top 10", "Top 20": "Top 20" }],
                ["Min Liq", minLiq, setMinLiq, { Any: "Any", "D+": "D+", "C+": "C+", "B+": "B+", A: "A" }],
              ] as [string, string, (v: string) => void, Record<string, string>][]).map(([label, val, setter, opts]) => (
                <div key={label} className="flex items-center gap-1.5">
                  <span className="text-[0.65rem] text-text-muted uppercase">{label}</span>
                  <select value={val} onChange={e => setter(e.target.value)}
                    className="text-xs border border-border rounded px-1.5 py-1 bg-surface">
                    {Object.entries(opts).map(([k, v]) => (
                      <option key={k} value={v}>{k}</option>
                    ))}
                  </select>
                </div>
              ))}
              <div className="flex items-center gap-1.5">
                <span className="text-[0.65rem] text-text-muted uppercase">Min POP</span>
                <input type="number" value={minPop} onChange={e => setMinPop(+e.target.value)}
                  className="w-14 text-xs border border-border rounded px-1.5 py-1 bg-surface font-data" />
              </div>
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={evOnly} onChange={e => setEvOnly(e.target.checked)}
                  className="rounded border-border" />
                <span className="text-text-muted">EV+</span>
              </label>
              <span className="text-xs text-text-muted ml-auto">
                {filtered.length}{preTopN > filtered.length ? ` of ${preTopN}` : ""} setups
              </span>
            </div>
          </div>

          {/* Results grid + detail */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* List */}
            <div className="space-y-1 max-h-[700px] overflow-y-auto">
              {filtered.map((r, i) => (
                <button key={r.ticker + r.expiration} onClick={() => { setSelectedIdx(i); setDetailTab(0); }}
                  className={`w-full text-left card card-compact transition-colors ${
                    i === selectedIdx ? "border-accent bg-accent-light" : "hover:bg-surface-alt"}`}>
                  <div className="flex justify-between items-center">
                    <div>
                      <span className="font-bold text-sm">{r.ticker}</span>
                      <span className="text-text-muted text-[0.65rem] ml-1.5">{fmtExp(r.expiration)} · {r.dte}d</span>
                    </div>
                    <div className="text-right font-data">
                      <span className="font-semibold text-sm">${r.fill_estimate}</span>
                      <span className={`text-[0.65rem] ml-1.5 ${LIQ_COLORS[r.liq_grade] || ""}`}>{r.liq_grade}</span>
                    </div>
                  </div>
                  <div className="flex gap-2 mt-0.5 text-[0.6rem] text-text-muted font-data">
                    <span>POP {r.pop}%</span>
                    <span>IVR {r.ivr?.toFixed(0) ?? "—"}</span>
                    <span>Score {r.adj_score.toFixed(3)}</span>
                    {r.contracts > 0 && <span>{r.contracts}×</span>}
                    {r.earnings_before && <span className="text-loss">EARN</span>}
                    {r.n_synthetic > 0 && <span className="text-warn">SYN</span>}
                  </div>
                </button>
              ))}
            </div>

            {/* Detail */}
            {selected && (
              <div className="lg:col-span-2 space-y-3">
                {/* Header + badges */}
                <div className="card">
                  <div className="flex justify-between items-start mb-3">
                    <div>
                      <h2 className="text-xl font-bold">{selected.ticker}</h2>
                      <p className="text-sm text-text-muted">{fmtExp(selected.expiration)} · {selected.dte}d · Spot ${selected.spot.toFixed(2)}</p>
                    </div>
                    <span className={`badge ${BAND_COLORS[selected.ivr_band] || "badge-info"}`}>
                      IVR {selected.ivr?.toFixed(0) ?? "N/A"} · {selected.ivr_band}
                    </span>
                  </div>

                  {/* Consolidated status banner */}
                  <div className="flex items-center gap-1.5 text-[0.65rem] font-data px-3 py-1.5 rounded border border-border mb-3 flex-wrap">
                    <span>IVR <strong>{selected.ivr?.toFixed(0) ?? "N/A"}</strong></span>
                    <span className="text-text-muted">·</span>
                    <span>IV {selected.avg_iv}%</span>
                    <span className="text-text-muted">·</span>
                    <span>HV20 {selected.hv20 != null ? `${selected.hv20}%` : "N/A"}</span>
                    <span className="text-text-muted">·</span>
                    <span>VRP <strong>{selected.vrp != null ? `${selected.vrp > 0 ? "+" : ""}${selected.vrp}%` : "N/A"}</strong></span>
                    <span className="text-text-muted ml-2">|</span>
                    <span className={LIQ_COLORS[selected.liq_grade] || ""}>Liq <strong>{selected.liq_grade}</strong></span>
                    <span className="text-text-muted">·</span>
                    <span>OI {selected.min_oi.toLocaleString()}</span>
                    <span className="text-text-muted">·</span>
                    <span>BA {selected.max_ba != null ? `$${selected.max_ba.toFixed(2)}` : "N/A"}</span>
                  </div>

                  {/* Warning badges */}
                  {(selected.earnings_before || selected.n_synthetic > 0 || selected.ivr_band === "Low" ||
                    selected.ivr_band === "Extreme" || (selected.wing_pct > 0 && selected.wing_pct < 1.5) ||
                    selected.liq_grade === "D" || selected.liq_grade === "F") && (
                    <div className="flex flex-wrap gap-1.5 mb-3">
                      {selected.ivr_band === "Extreme" && <span className="badge badge-warn">IVR &gt;75 jump risk</span>}
                      {selected.ivr_band === "Low" && <span className="badge badge-loss">IVR below 30</span>}
                      {(selected.liq_grade === "D" || selected.liq_grade === "F") && (
                        <span className="badge badge-loss">Low liquidity ({selected.liq_grade})</span>
                      )}
                      {selected.wing_pct > 0 && selected.wing_pct < 1.5 && (
                        <span className="badge badge-info">Narrow wings ({selected.wing_pct.toFixed(1)}%)</span>
                      )}
                      {selected.earnings_before && <span className="badge badge-loss">Earnings in {selected.earnings_days}d</span>}
                      {selected.n_synthetic > 0 && <span className="badge badge-warn">{selected.n_synthetic} legs no live quote</span>}
                    </div>
                  )}

                  {/* Key metrics */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
                    <Metric label="Open For" value={`$${selected.fill_estimate}`} />
                    <Metric label="Max Risk" value={`$${selected.max_risk}`} />
                    <Metric label="POP" value={`${selected.pop}%`} />
                    <Metric label="Expiration" value={fmtExp(selected.expiration)} />
                  </div>

                  {/* Fill pricing with improvement % */}
                  <div className="text-[0.65rem] font-data text-text-muted mb-3">
                    Natural ${selected.natural} · <strong>Fill ${selected.fill_estimate}</strong> · Mid ${selected.mid}
                    {" "}({FILL_PCT[selected.liq_grade] ?? 15}% improve)
                  </div>

                  {/* Breakevens with % from spot */}
                  <div className="text-[0.65rem] font-data text-text-muted mb-3">
                    Breakevens: ${selected.lower_be.toFixed(0)} / ${selected.upper_be.toFixed(0)}
                    {" "}({selected.lower_be_pct}% / {selected.upper_be_pct}%)
                    {" · "}~{selected.days_to_target}d to {selected.profit_target_pct}% target
                  </div>

                  {/* Leg diagram */}
                  <div className="flex items-center justify-center gap-1 text-[0.65rem] font-data mb-3 flex-wrap">
                    <span className="px-1.5 py-0.5 rounded border border-loss text-loss">{selected.long_put.toFixed(0)}P</span>
                    <span className="text-text-muted">—</span>
                    <span className="px-1.5 py-0.5 rounded border border-warn text-warn">{selected.short_put.toFixed(0)}P</span>
                    <span className="text-text-muted">· {selected.spot.toFixed(0)} ·</span>
                    <span className="px-1.5 py-0.5 rounded border border-warn text-warn">{selected.short_call.toFixed(0)}C</span>
                    <span className="text-text-muted">—</span>
                    <span className="px-1.5 py-0.5 rounded border border-loss text-loss">{selected.long_call.toFixed(0)}C</span>
                  </div>

                  {/* Book It */}
                  <div className="flex gap-3 items-center">
                    <button onClick={() => handleBook(selected)}
                      disabled={booked.has(selected.ticker + selected.expiration)}
                      className={`flex-1 py-2 rounded-lg font-semibold text-sm transition-colors ${
                        booked.has(selected.ticker + selected.expiration)
                          ? "bg-gain/20 text-gain" : "bg-accent text-white hover:bg-accent-hover"}`}>
                      {booked.has(selected.ticker + selected.expiration)
                        ? `✓ Booked` : `Book ${selected.contracts || 1}× ${selected.ticker}`}
                    </button>
                    <div className="text-[0.65rem] text-text-muted font-data">
                      {selected.contracts || 1}× · ${selected.fill_estimate}/ct · Kelly {selected.kelly_adj?.toFixed(1)}%
                    </div>
                  </div>
                </div>

                {/* Sub-tabs */}
                <div className="card">
                  <div className="flex gap-1 mb-3 border-b border-border pb-2 overflow-x-auto">
                    {DETAIL_TABS.map((tab, i) => (
                      <button key={tab} onClick={() => setDetailTab(i)}
                        className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors whitespace-nowrap ${
                          detailTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                        {tab}
                      </button>
                    ))}
                  </div>

                  {/* ═══ Overview tab ═══ */}
                  {detailTab === 0 && (
                    <div className="space-y-4">
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                        {selected.payoff_prices?.length > 0 && (
                          <div>
                            <div className="metric-label mb-1">P&L at Expiration</div>
                            <Plot
                              data={[
                                { x: selected.payoff_prices, y: selected.payoff_pnl, type: "scatter" as const, mode: "lines" as const,
                                  fill: "tozeroy", fillcolor: t.gain + "14", line: { color: t.gain, width: 2 },
                                  hovertemplate: "$%{x:.0f}: $%{y:,.0f}<extra></extra>" },
                                { x: selected.payoff_prices, y: selected.payoff_pnl.map(v => v < 0 ? v : 0),
                                  type: "scatter" as const, mode: "lines" as const, fill: "tozeroy",
                                  fillcolor: t.loss + "1a", line: { color: "transparent", width: 0 },
                                  hoverinfo: "skip" as const, showlegend: false },
                              ]}
                              layout={{
                                height: 220, margin: { l: 40, r: 10, t: 10, b: 30 },
                                paper_bgcolor: "transparent", plot_bgcolor: t.plot,
                                font: { family: "Inter", color: t.text, size: 9 },
                                xaxis: { title: "Price", gridcolor: t.grid },
                                yaxis: { title: "P&L ($)", gridcolor: t.grid, zeroline: true, zerolinecolor: t.muted },
                                showlegend: false,
                                shapes: [
                                  // Spot price
                                  { type: "line", x0: selected.spot, x1: selected.spot, y0: 0, y1: 1, yref: "paper", line: { color: t.accent, width: 1, dash: "dash" } },
                                  // Breakevens
                                  { type: "line", x0: selected.lower_be, x1: selected.lower_be, y0: 0, y1: 1, yref: "paper", line: { color: t.loss, width: 1, dash: "dot" } },
                                  { type: "line", x0: selected.upper_be, x1: selected.upper_be, y0: 0, y1: 1, yref: "paper", line: { color: t.loss, width: 1, dash: "dot" } },
                                  // 30-delta triggers
                                  { type: "line", x0: selected.put_30d_trigger, x1: selected.put_30d_trigger, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dashdot" } },
                                  { type: "line", x0: selected.call_30d_trigger, x1: selected.call_30d_trigger, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dashdot" } },
                                  // Profit target line (horizontal)
                                  { type: "line", x0: 0, x1: 1, xref: "paper", y0: selected.target_credit, y1: selected.target_credit, line: { color: t.gain, width: 1, dash: "dash" } },
                                  // Stop loss line (horizontal)
                                  { type: "line", x0: 0, x1: 1, xref: "paper", y0: -selected.stop_loss_amt, y1: -selected.stop_loss_amt, line: { color: t.loss, width: 1, dash: "dot" } },
                                ],
                                annotations: [
                                  { x: selected.spot, y: 1, yref: "paper", text: "Spot", showarrow: false, font: { size: 8, color: t.accent } },
                                  { x: selected.lower_be, y: 0, yref: "paper", text: "BE", showarrow: false, font: { size: 7, color: t.loss }, yanchor: "top" },
                                  { x: selected.upper_be, y: 0, yref: "paper", text: "BE", showarrow: false, font: { size: 7, color: t.loss }, yanchor: "top" },
                                  { x: selected.put_30d_trigger, y: 1, yref: "paper", text: "30Δ", showarrow: false, font: { size: 7, color: t.spot } },
                                  { x: selected.call_30d_trigger, y: 1, yref: "paper", text: "30Δ", showarrow: false, font: { size: 7, color: t.spot } },
                                  { x: 1, xref: "paper", xanchor: "right", y: selected.target_credit, text: `${selected.profit_target_pct}% Target`, showarrow: false, font: { size: 7, color: t.gain } },
                                  { x: 1, xref: "paper", xanchor: "right", y: -selected.stop_loss_amt, text: `${selected.stop_multiplier}× Stop`, showarrow: false, font: { size: 7, color: t.loss } },
                                ],
                              }}
                              config={{ displayModeBar: false, responsive: true }}
                              style={{ width: "100%" }}
                            />
                          </div>
                        )}
                        {selected.decay_days?.length > 0 && (
                          <div>
                            <div className="metric-label mb-1">Theta Decay</div>
                            <Plot
                              data={[{ x: selected.decay_days, y: selected.decay_vals, type: "scatter" as const, mode: "lines" as const,
                                fill: "tozeroy", fillcolor: t.accent + "10", line: { color: t.accent, width: 2 },
                                hovertemplate: "Day %{x}: $%{y:,.0f}<extra></extra>" }]}
                              layout={{
                                height: 220, margin: { l: 40, r: 10, t: 10, b: 30 },
                                paper_bgcolor: "transparent", plot_bgcolor: t.plot,
                                font: { family: "Inter", color: t.text, size: 9 },
                                xaxis: { title: "Days", gridcolor: t.grid },
                                yaxis: { title: "Value ($)", gridcolor: t.grid },
                                showlegend: false,
                                shapes: [
                                  // Credit line
                                  { type: "line", x0: 0, x1: selected.dte, y0: selected.credit, y1: selected.credit, line: { color: t.gain, width: 1, dash: "dot" } },
                                  // Target credit line
                                  { type: "line", x0: 0, x1: selected.dte, y0: selected.target_credit, y1: selected.target_credit, line: { color: t.spot, width: 1, dash: "dash" } },
                                  // 21 DTE time stop (if applicable)
                                  ...(selected.dte > 21 ? [{
                                    type: "line" as const, x0: selected.dte - 21, x1: selected.dte - 21,
                                    y0: 0, y1: 1, yref: "paper" as const, line: { color: t.spot, width: 1, dash: "dashdot" as const },
                                  }] : []),
                                ],
                                annotations: [
                                  { x: 0, y: selected.credit, text: "Credit", showarrow: false, xanchor: "left", font: { size: 8, color: t.gain } },
                                  { x: 0, y: selected.target_credit, text: `${selected.profit_target_pct}% Target`, showarrow: false, xanchor: "left", font: { size: 8, color: t.spot } },
                                  ...(selected.dte > 21 ? [{
                                    x: selected.dte - 21, y: 1, yref: "paper" as const,
                                    text: "21 DTE", showarrow: false, font: { size: 7, color: t.spot },
                                  }] : []),
                                  // Days-to-target diamond marker
                                  ...(selected.days_to_target < selected.dte ? [{
                                    x: selected.days_to_target, y: selected.target_credit,
                                    text: `~${selected.days_to_target}d`, showarrow: true, arrowhead: 0,
                                    font: { size: 7, color: t.spot }, arrowcolor: t.spot,
                                  }] : []),
                                ],
                              }}
                              config={{ displayModeBar: false, responsive: true }}
                              style={{ width: "100%" }}
                            />
                          </div>
                        )}
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                        <Metric label="Lower BE" value={`$${selected.lower_be.toFixed(0)} (${selected.lower_be_pct}%)`} />
                        <Metric label="Upper BE" value={`$${selected.upper_be.toFixed(0)} (${selected.upper_be_pct}%)`} />
                        <Metric label="IV" value={`${selected.avg_iv}%`} />
                        <Metric label="VRP" value={selected.vrp != null ? `${selected.vrp > 0 ? "+" : ""}${selected.vrp}%` : "N/A"} />
                      </div>
                    </div>
                  )}

                  {/* ═══ Management tab ═══ */}
                  {detailTab === 1 && (
                    <div className="space-y-4">
                      {/* Take Profit / Stop Loss / Time Stop */}
                      <div className="grid grid-cols-3 gap-3">
                        <div className="card card-compact bg-surface-alt">
                          <div className="metric-label">Take Profit</div>
                          <div className="text-sm font-semibold mt-1">{selected.profit_target_pct}% of credit</div>
                          <div className="text-xs text-text-muted">${selected.target_credit}</div>
                        </div>
                        <div className="card card-compact bg-surface-alt">
                          <div className="metric-label">Stop Loss</div>
                          <div className="text-sm font-semibold mt-1">{selected.stop_multiplier}× credit</div>
                          <div className="text-xs text-text-muted">${selected.stop_loss_amt}</div>
                        </div>
                        <div className="card card-compact bg-surface-alt">
                          <div className="metric-label">Time Stop</div>
                          <div className="text-sm font-semibold mt-1">21 DTE</div>
                          <div className="text-xs text-text-muted">Roll or close</div>
                        </div>
                      </div>

                      {/* 30-delta triggers */}
                      <div className="card card-compact bg-surface-alt">
                        <div className="metric-label">30Δ Adjustment Triggers</div>
                        <div className="text-sm mt-1">
                          Put: <strong className="font-data">${selected.put_30d_trigger.toFixed(0)}</strong> ·
                          Call: <strong className="font-data">${selected.call_30d_trigger.toFixed(0)}</strong>
                          <span className="text-text-muted"> — roll untested side toward the money</span>
                        </div>
                      </div>

                      {/* Historical Backtest */}
                      {selected.hist_winrate && (
                        <div className="card card-compact border-border">
                          <div className="metric-label mb-2">Historical Backtest ({selected.hist_winrate.n_trials} simulated trades)</div>
                          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                            <Metric label="Managed WR" value={`${selected.hist_winrate.win_rate}%`} />
                            <Metric label="Exp-Only WR" value={`${selected.hist_winrate.exp_win_rate}%`} />
                            <Metric label="Early Profits" value={String(selected.hist_winrate.early_profit)} />
                            <Metric label="Stopped Out" value={String(selected.hist_winrate.stopped_out)} />
                            <Metric label="Breached @ Exp" value={String(selected.hist_winrate.breached_at_exp)} />
                          </div>
                          <p className="text-[0.6rem] text-text-muted mt-2">
                            Avg max move: {selected.hist_winrate.avg_max_move_pct}% · Median: {selected.hist_winrate.median_max_move_pct}%
                          </p>
                        </div>
                      )}
                      {!selected.hist_winrate && (
                        <div className="text-xs text-text-muted italic">No historical backtest — insufficient price history (&lt;252 days + DTE).</div>
                      )}

                      {/* Forward Event Stress Test */}
                      {selected.stress_test && selected.stress_test.length > 0 && (
                        <div className="card card-compact border-border">
                          <div className="metric-label mb-2">Forward Event Stress Test</div>
                          <div className="overflow-x-auto">
                            <table className="data-table text-xs">
                              <thead>
                                <tr>
                                  <th>Event</th><th>Date</th><th>Scenario</th>
                                  <th>Move</th><th>P&L</th><th>Survives?</th>
                                </tr>
                              </thead>
                              <tbody>
                                {selected.stress_test.map((s: ICStressScenario, i: number) => (
                                  <tr key={i}>
                                    <td className="font-semibold">{s.event}</td>
                                    <td className="font-data">{fmtExp(s.date)} ({s.days_away}d)</td>
                                    <td className="font-data">{s.scenario}</td>
                                    <td className="font-data">{s.move_pct.toFixed(1)}%</td>
                                    <td className={`font-data ${s.pnl >= 0 ? "text-gain" : "text-loss"}`}>${s.pnl}</td>
                                    <td>{s.survives
                                      ? <span className="text-gain font-semibold">OK</span>
                                      : <span className="text-loss font-semibold">STOP</span>}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                          {selected.stress_test.every(s => s.survives)
                            ? <p className="text-xs text-gain mt-2">All scenarios survive within the stop loss threshold.</p>
                            : <p className="text-xs text-loss mt-2">
                                {selected.stress_test.filter(s => !s.survives).length} scenario(s) hit the stop.
                                Consider tighter management or skipping this setup.
                              </p>
                          }
                        </div>
                      )}
                      {(!selected.stress_test || selected.stress_test.length === 0) && (
                        <div className="text-xs text-gain italic">No known events (FOMC, earnings) within the DTE window.</div>
                      )}

                      {/* Kelly / sizing metrics */}
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                        <Metric label="Managed WR" value={`${selected.managed_wr}%`} />
                        <Metric label="Kelly (full)" value={`${selected.kelly_full}%`} />
                        <Metric label="Kelly (adj)" value={`${selected.kelly_adj}%`} />
                        <Metric label="Contracts" value={String(selected.contracts)} />
                      </div>
                    </div>
                  )}

                  {/* ═══ Compare Expirations tab ═══ */}
                  {detailTab === 2 && (
                    <div className="space-y-3">
                      <p className="text-[0.65rem] text-text-muted">
                        Compare the same iron condor structure across alternative expirations. ★ marks best $/Day.
                      </p>
                      {selected.alt_expirations && selected.alt_expirations.length > 0 ? (
                        <div className="overflow-x-auto">
                          <table className="data-table text-xs">
                            <thead>
                              <tr>
                                <th>Exp</th><th>DTE</th><th>Strikes</th><th>Credit</th>
                                <th>$/Day</th><th>Risk</th><th>POP</th>
                              </tr>
                            </thead>
                            <tbody>
                              {/* Current expiration row */}
                              <tr className="bg-accent-light">
                                <td className="font-semibold">{fmtExp(selected.expiration)} ★</td>
                                <td className="font-data">{selected.dte}</td>
                                <td className="font-data">{selected.short_put.toFixed(0)}P/{selected.short_call.toFixed(0)}C</td>
                                <td className="font-data">${selected.fill_estimate}</td>
                                <td className="font-data">${(selected.fill_estimate / Math.max(selected.dte, 1)).toFixed(1)}</td>
                                <td className="font-data">${selected.max_risk}</td>
                                <td className="font-data">{selected.pop}%</td>
                              </tr>
                              {/* Alt expirations */}
                              {selected.alt_expirations.map((alt, i) => {
                                const currentPerDay = selected.fill_estimate / Math.max(selected.dte, 1);
                                const isBest = alt.credit_per_day > currentPerDay;
                                return (
                                  <tr key={i}>
                                    <td className="font-semibold">{fmtExp(alt.exp)}{isBest ? " ★" : ""}</td>
                                    <td className="font-data">{alt.dte}</td>
                                    <td className="font-data">{alt.strikes}</td>
                                    <td className="font-data">${alt.credit}</td>
                                    <td className={`font-data ${isBest ? "text-gain font-semibold" : ""}`}>${alt.credit_per_day}</td>
                                    <td className="font-data">${alt.max_risk}</td>
                                    <td className="font-data">{alt.pop}%</td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <p className="text-xs text-text-muted italic">No alternative expirations in DTE range.</p>
                      )}
                    </div>
                  )}

                  {/* ═══ Greeks tab ═══ */}
                  {detailTab === 3 && (
                    <div className="space-y-4">
                      <p className="text-[0.6rem] text-text-muted">
                        Per-contract dollar Greeks. Institutional limits: |Δ| ≤ 0.30, |Γ| ≤ 0.03, |ν| ≤ 0.20.
                      </p>
                      <div className="grid grid-cols-5 gap-3">
                        <Metric label="Δ Delta" value={`${(selected.net_delta * 100).toFixed(1)}`} />
                        <Metric label="Γ Gamma" value={`${(selected.net_gamma * 100).toFixed(2)}`} />
                        <Metric label="Θ Theta" value={`$${(selected.net_theta * 100).toFixed(1)}/d`} />
                        <Metric label="ν Vega" value={`$${(selected.net_vega * 100).toFixed(1)}/1%`} />
                        <Metric label="Θ/ν" value={`${selected.theta_vega_ratio.toFixed(2)}`} />
                      </div>

                      {/* DGTV breach warnings — position-level (delta × contracts) */}
                      {(() => {
                        const n = selected.contracts || 1;
                        const breaches: string[] = [];
                        if (Math.abs(selected.net_delta * n) > DGTV_LIMITS.delta) breaches.push(`|Δ| ${Math.abs(selected.net_delta * n).toFixed(3)} > ${DGTV_LIMITS.delta}`);
                        if (Math.abs(selected.net_gamma * n) > DGTV_LIMITS.gamma) breaches.push(`|Γ| ${Math.abs(selected.net_gamma * n).toFixed(4)} > ${DGTV_LIMITS.gamma}`);
                        if (Math.abs(selected.net_vega * n) > DGTV_LIMITS.vega) breaches.push(`|ν| ${Math.abs(selected.net_vega * n).toFixed(3)} > ${DGTV_LIMITS.vega}`);
                        return breaches.length > 0 ? (
                          <div className="card card-compact border-loss/30 bg-loss-bg text-loss text-xs">
                            <strong>DGTV breach ({n}×):</strong> {breaches.join(", ")}
                          </div>
                        ) : null;
                      })()}

                      {/* Position sizing summary */}
                      {selected.contracts > 0 && (
                        <div className="text-xs text-text-muted">
                          {selected.contracts} contracts · ${selected.total_credit} credit · ${selected.total_risk} risk
                        </div>
                      )}

                      {/* Legs table */}
                      {selected.legs && (
                        <div className="overflow-x-auto">
                          <table className="data-table">
                            <thead>
                              <tr>
                                <th>Leg</th><th>Bid</th><th>Ask</th><th>Mid</th><th>B/A</th>
                                <th>Δ</th><th>Γ</th><th>Θ</th><th>ν</th>
                                <th>OI</th><th>Vol</th><th>Quote</th>
                              </tr>
                            </thead>
                            <tbody>
                              {selected.legs.map((leg, i) => (
                                <tr key={i}>
                                  <td className="font-semibold">{leg.label}</td>
                                  <td className="font-data">${leg.bid.toFixed(2)}</td>
                                  <td className="font-data">${leg.ask.toFixed(2)}</td>
                                  <td className="font-data">${leg.mid.toFixed(2)}</td>
                                  <td className="font-data">${(leg.ask - leg.bid).toFixed(2)}</td>
                                  <td className="font-data">{leg.delta.toFixed(3)}</td>
                                  <td className="font-data">{leg.gamma.toFixed(4)}</td>
                                  <td className="font-data">{leg.theta.toFixed(3)}</td>
                                  <td className="font-data">{leg.vega.toFixed(3)}</td>
                                  <td className="font-data">{leg.oi.toLocaleString()}</td>
                                  <td className="font-data">{leg.vol.toLocaleString()}</td>
                                  <td><span className={`badge ${leg.live ? "badge-gain" : "badge-warn"}`}>
                                    {leg.live ? "Live" : "Synthetic"}</span></td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}

                      {/* Synthetic quote warning */}
                      {selected.n_synthetic > 0 && (
                        <div className="card card-compact border-warn/30 bg-warn-bg text-warn text-xs">
                          {selected.n_synthetic} leg(s) have synthetic quotes (estimated from daily close, not live bid/ask).
                          Verify in broker before trading.
                        </div>
                      )}

                      <div className="grid grid-cols-3 gap-3">
                        <Metric label="Liquidity" value={selected.liq_grade} />
                        <Metric label="Min OI" value={selected.min_oi.toLocaleString()} />
                        <Metric label="Max B/A" value={selected.max_ba != null ? `$${selected.max_ba.toFixed(2)}` : "N/A"} />
                      </div>
                    </div>
                  )}

                  {/* ═══ Results Table tab ═══ */}
                  {detailTab === 4 && (
                    <div className="overflow-x-auto">
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>Ticker</th><th>Liq</th><th>Exp</th><th>Strikes</th>
                            <th>Credit</th><th>Risk</th><th>POP</th><th>IVR</th><th>VRP</th>
                            <th>Earn</th><th>Hist WR</th><th>Target</th>
                            <th>Contracts</th><th>Score</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filtered.map((r, i) => (
                            <tr key={r.ticker + r.expiration}
                              className={`${i === selectedIdx ? "bg-accent-light" : "cursor-pointer"}`}
                              onClick={() => { setSelectedIdx(i); setDetailTab(0); }}>
                              <td className="font-semibold">{r.ticker}</td>
                              <td><span className={LIQ_COLORS[r.liq_grade] || ""}>{r.liq_grade}</span></td>
                              <td className="font-data">{fmtExp(r.expiration)} ({r.dte}d)</td>
                              <td className="font-data">{r.short_put.toFixed(0)}P/{r.short_call.toFixed(0)}C</td>
                              <td className="font-data">${r.fill_estimate}</td>
                              <td className="font-data">${r.max_risk}</td>
                              <td className="font-data">{r.pop}%</td>
                              <td className="font-data">{r.ivr?.toFixed(0) ?? "—"}</td>
                              <td className="font-data">{r.vrp != null ? `${r.vrp > 0 ? "+" : ""}${r.vrp}` : "—"}</td>
                              <td className="font-data">{r.earnings_before ? <span className="text-loss">⚠ {r.earnings_days}d</span> : r.earnings_days ? `${r.earnings_days}d` : "—"}</td>
                              <td className="font-data">{r.hist_winrate ? `${r.hist_winrate.win_rate}%` : "—"}</td>
                              <td className="font-data">${r.target_credit}</td>
                              <td className="font-data">{r.contracts}</td>
                              <td className="font-data">{r.adj_score.toFixed(3)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {scan.isError && (
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm">
          Scan failed: {(scan.error as Error).message}
        </div>
      )}
    </div>
  );
}
