"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import dynamic from "next/dynamic";
import {
  fetchTrackedFunds,
  fetch13FHoldings,
  fetchCongressionalTrades,
  fetchRecent13D,
  fetch8KEvents,
  fetchGuidanceHistory,
  fetchTranscriptUrls,
  fetchTranscriptGuidance,
  fetchEdgarEarningsCalendar,
  fetchAnalystEstimates,
  fetchEarningsHistory,
  fetchMacroDashboard,
  fetchFredSeriesCustom,
  fetchInsiderTransactions,
  type Holding13F,
  type Activist13D,
  type CongressionalTrade,
  type GuidanceRow,
  type EightKEvent,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT, type ChartTheme } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = [
  "13F Holdings",
  "Congressional Trades",
  "Activist Investors",
  "Company Guidance",
  "Macro & Rates",
  "8-K Events",
];

const ITEM_NAMES: Record<string, string> = {
  "1.01": "Entry into Agreement", "1.02": "Termination of Agreement",
  "2.01": "Acquisition/Disposition", "2.02": "Results of Operations",
  "2.03": "Obligation Trigger", "2.05": "Costs for Exit",
  "3.01": "Delisting", "3.02": "Unregistered Sales",
  "4.01": "Auditor Change", "4.02": "Non-Reliance on Financials",
  "5.01": "Change of Control", "5.02": "Officer Departure/Appointment",
  "5.03": "Amendments to Articles", "7.01": "Regulation FD Disclosure",
  "8.01": "Other Events", "9.01": "Financial Statements and Exhibits",
};

function fmtBn(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(digits)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(digits)}M`;
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtPctSigned(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

function shortDate(s: string | null | undefined): string {
  if (!s || s === "NaT") return "—";
  return s.slice(0, 10);
}

export default function SmartMoneyPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Smart Money Tracker</h1>
        <p className="text-text-secondary text-sm mt-1">
          Track institutional holdings, activist investors, congressional trades, company guidance, and macro indicators — all from public data sources.
        </p>
      </div>

      <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
        {TABS.map((tab, i) => (
          <button
            key={tab}
            onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
              activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab === 0 && <Holdings13FTab t={t} L={L} />}
      {activeTab === 1 && <CongressionalTab t={t} L={L} />}
      {activeTab === 2 && <ActivistTab t={t} L={L} />}
      {activeTab === 3 && <GuidanceTab t={t} L={L} />}
      {activeTab === 4 && <MacroTab t={t} L={L} />}
      {activeTab === 5 && <EightKTab t={t} L={L} />}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 1 — 13F HOLDINGS
// ══════════════════════════════════════════════════════════════

function Holdings13FTab({ t, L }: { t: ChartTheme; L: ReturnType<typeof getBaseLayout> }) {
  const { data: funds } = useQuery({
    queryKey: ["tracked-funds"],
    queryFn: fetchTrackedFunds,
    staleTime: Infinity,
  });
  const [fund, setFund] = useState<string>("");
  const load = useMutation({ mutationFn: (cik: string) => fetch13FHoldings(cik) });

  const fundName = funds?.funds.find(f => f.cik === fund)?.name ?? "";
  const top15 = useMemo(() => {
    if (!load.data) return [];
    return [...load.data.holdings]
      .filter(h => (h.value ?? 0) > 0 && h.company && h.company.trim() !== "")
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
      .slice(0, 15);
  }, [load.data]);

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="text-sm font-bold">Institutional Holdings (13F)</div>
        <div className="text-xs text-text-muted mb-3">
          Quarterly filings from funds with &gt;$100M AUM. Data from SEC EDGAR.
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <select
            value={fund}
            onChange={e => setFund(e.target.value)}
            className="px-3 py-2 border border-border rounded-lg text-sm bg-surface min-w-[240px]"
          >
            <option value="">Select fund…</option>
            {funds?.funds.map(f => (
              <option key={f.cik} value={f.cik}>{f.name}</option>
            ))}
          </select>
          <button
            onClick={() => fund && load.mutate(fund)}
            disabled={!fund || load.isPending}
            className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {load.isPending ? "Loading..." : "Load Holdings"}
          </button>
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-10">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {load.isSuccess && load.data && load.data.count === 0 && (
        <div className="card text-sm text-text-muted py-6 px-5">
          No 13F data found for this fund. It may not have filed recently, or the filing format differs.
        </div>
      )}

      {load.data && load.data.count > 0 && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Positions" value={String(load.data.count)} />
              <Metric label="Filing Date" value={load.data.filing_date ?? "—"} />
              <Metric label="Fund" value={fundName} />
            </div>
          </div>

          {top15.length > 0 && (
            <div className="card">
              <Plot
                data={[{
                  type: "bar", orientation: "h",
                  y: top15.map(h => h.company ?? ""),
                  x: top15.map(h => (h.value ?? 0) / 1e6),
                  marker: { color: t.accent },
                  text: top15.map(h => `$${((h.value ?? 0) / 1e6).toLocaleString(undefined, { maximumFractionDigits: 0 })}M`),
                  textposition: "outside",
                  hovertemplate: "%{y}<br>$%{x:,.0f}M<extra></extra>",
                }]}
                layout={{
                  ...L, height: CHART_HEIGHT.tall,
                  title: { text: `${fundName} — Top 15 Holdings by Value ($M)`, font: { size: 14, color: t.text } },
                  xaxis: { title: { text: "Value ($M)" }, gridcolor: t.grid },
                  yaxis: { gridcolor: t.grid, autorange: "reversed" },
                  margin: { l: 180, r: 80, t: 40, b: 40 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          <div className="card">
            <div className="font-semibold text-sm mb-2">All Holdings ({load.data.count})</div>
            <div className="overflow-x-auto max-h-[600px]">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                  <tr>
                    <th className="text-left py-1.5 px-2">Company</th>
                    <th className="text-left py-1.5 px-2">Class</th>
                    <th className="text-right py-1.5 px-2">Value</th>
                    <th className="text-right py-1.5 px-2">Shares</th>
                    <th className="text-left py-1.5 px-2">CUSIP</th>
                    <th className="text-left py-1.5 px-2">Type</th>
                  </tr>
                </thead>
                <tbody>
                  {load.data.holdings.map((h, i) => (
                    <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2 font-semibold">{h.company ?? "—"}</td>
                      <td className="py-1 px-2 text-text-muted">{h.class ?? "—"}</td>
                      <td className="py-1 px-2 text-right">{fmtBn(h.value)}</td>
                      <td className="py-1 px-2 text-right">{h.shares != null ? h.shares.toLocaleString() : "—"}</td>
                      <td className="py-1 px-2 text-text-muted">{h.cusip ?? "—"}</td>
                      <td className="py-1 px-2">{h.put_call ?? "Equity"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 2 — CONGRESSIONAL TRADES
// ══════════════════════════════════════════════════════════════

function CongressionalTab({ t, L }: { t: ChartTheme; L: ReturnType<typeof getBaseLayout> }) {
  const [year, setYear] = useState(2026);
  const [maxFilings, setMaxFilings] = useState(50);
  const [typeFilter, setTypeFilter] = useState<Set<string>>(new Set(["Purchase", "Sale"]));
  const [memberFilter, setMemberFilter] = useState<Set<string>>(new Set());
  const [tickerSearch, setTickerSearch] = useState("");
  const load = useMutation({
    mutationFn: () => fetchCongressionalTrades({ year, maxFilings }),
  });

  const trades = load.data?.data ?? [];

  const {
    buys, sells, topBought, topSold, topMembers, members,
  } = useMemo(() => {
    const buys = trades.filter(tr => tr.type === "Purchase");
    const sells = trades.filter(tr => tr.type === "Sale");
    const count = (arr: CongressionalTrade[], key: keyof CongressionalTrade) => {
      const m = new Map<string, number>();
      for (const row of arr) {
        const v = row[key];
        if (!v) continue;
        const s = String(v);
        m.set(s, (m.get(s) ?? 0) + 1);
      }
      return [...m.entries()].sort((a, b) => b[1] - a[1]);
    };
    const topBought = count(buys, "ticker").slice(0, 12);
    const topSold = count(sells, "ticker").slice(0, 12);
    const topMembers = count(trades, "member").slice(0, 10);
    const members = [...new Set(trades.map(tr => tr.member))].sort();
    return { buys, sells, topBought, topSold, topMembers, members };
  }, [trades]);

  const filtered = useMemo(() => {
    const searchTickers = tickerSearch
      ? tickerSearch.split(",").map(s => s.trim().toUpperCase()).filter(Boolean)
      : [];
    return trades.filter(tr => {
      if (!typeFilter.has(tr.type)) return false;
      if (memberFilter.size > 0 && !memberFilter.has(tr.member)) return false;
      if (searchTickers.length && !searchTickers.includes(tr.ticker)) return false;
      return true;
    });
  }, [trades, typeFilter, memberFilter, tickerSearch]);

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="text-sm font-bold">Congressional Stock Trades</div>
        <div className="text-xs text-text-muted mb-3">
          House members must disclose trades within 45 days under the STOCK Act. Data parsed from clerk.house.gov PTR filings.
        </div>
        <div className="flex items-end gap-3 flex-wrap">
          <div>
            <label className="metric-label">Year</label>
            <select value={year} onChange={e => setYear(parseInt(e.target.value))}
              className="mt-0.5 px-2 py-1.5 border border-border rounded text-sm bg-surface">
              {[2026, 2025, 2024].map(y => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>
          <div>
            <label className="metric-label">Filings to parse</label>
            <select value={maxFilings} onChange={e => setMaxFilings(parseInt(e.target.value))}
              className="mt-0.5 px-2 py-1.5 border border-border rounded text-sm bg-surface">
              {[25, 50, 100, 200].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <button
            onClick={() => load.mutate()}
            disabled={load.isPending}
            className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {load.isPending ? "Parsing…" : "Analyze Trades"}
          </button>
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-10">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-3">Parsing PTR PDFs — this can take 30–90 seconds.</div>
        </div>
      )}

      {load.data && trades.length === 0 && !load.isPending && (
        <div className="card text-sm text-text-muted py-6 px-5">No trade data could be parsed for {year}.</div>
      )}

      {trades.length > 0 && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Total Trades" value={trades.length.toLocaleString()} />
              <Metric label="Unique Tickers" value={String(new Set(trades.map(tr => tr.ticker).filter(Boolean)).size)} />
              <Metric
                label="Purchases"
                value={String(buys.length)}
                delta={`${((buys.length / Math.max(trades.length, 1)) * 100).toFixed(0)}%`}
                deltaType="gain"
              />
              <Metric
                label="Sales"
                value={String(sells.length)}
                delta={`${((sells.length / Math.max(trades.length, 1)) * 100).toFixed(0)}%`}
                deltaType="loss"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {topBought.length > 0 && (
              <div className="card">
                <Plot
                  data={[{
                    type: "bar", orientation: "h",
                    y: topBought.map(([tk]) => tk),
                    x: topBought.map(([, n]) => n),
                    marker: { color: t.accent },
                    text: topBought.map(([, n]) => String(n)), textposition: "outside",
                  }]}
                  layout={{
                    ...L, height: CHART_HEIGHT.normal,
                    title: { text: "Most Purchased Tickers", font: { size: 13, color: t.text } },
                    xaxis: { title: { text: "# Buy Transactions" }, gridcolor: t.grid },
                    yaxis: { gridcolor: t.grid, autorange: "reversed" },
                    margin: { l: 60, r: 40, t: 40, b: 40 },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            )}
            {topSold.length > 0 && (
              <div className="card">
                <Plot
                  data={[{
                    type: "bar", orientation: "h",
                    y: topSold.map(([tk]) => tk),
                    x: topSold.map(([, n]) => n),
                    marker: { color: t.loss },
                    text: topSold.map(([, n]) => String(n)), textposition: "outside",
                  }]}
                  layout={{
                    ...L, height: CHART_HEIGHT.normal,
                    title: { text: "Most Sold Tickers", font: { size: 13, color: t.text } },
                    xaxis: { title: { text: "# Sell Transactions" }, gridcolor: t.grid },
                    yaxis: { gridcolor: t.grid, autorange: "reversed" },
                    margin: { l: 60, r: 40, t: 40, b: 40 },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            )}
          </div>

          {topMembers.length > 0 && (
            <div className="card">
              <Plot
                data={[{
                  type: "bar", orientation: "h",
                  y: topMembers.map(([m]) => m),
                  x: topMembers.map(([, n]) => n),
                  marker: { color: t.spot },
                  text: topMembers.map(([, n]) => String(n)), textposition: "outside",
                }]}
                layout={{
                  ...L, height: CHART_HEIGHT.normal,
                  title: { text: "Most Active Members (# Trades)", font: { size: 13, color: t.text } },
                  xaxis: { title: { text: "# Trades" }, gridcolor: t.grid },
                  yaxis: { gridcolor: t.grid, autorange: "reversed" },
                  margin: { l: 200, r: 40, t: 40, b: 40 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          <div className="card">
            <div className="font-semibold text-sm mb-2">Trade Details</div>
            <div className="flex flex-wrap items-end gap-3 mb-3">
              <div>
                <label className="metric-label">Type</label>
                <div className="flex gap-1 mt-0.5">
                  {["Purchase", "Sale"].map(kind => (
                    <button key={kind}
                      onClick={() => setTypeFilter(prev => {
                        const n = new Set(prev);
                        if (n.has(kind)) n.delete(kind); else n.add(kind);
                        return n;
                      })}
                      className={`px-2 py-1 text-xs rounded border ${
                        typeFilter.has(kind)
                          ? "bg-accent text-white border-accent"
                          : "border-border text-text-muted hover:bg-surface-alt"
                      }`}>
                      {kind}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex-1 min-w-[220px]">
                <label className="metric-label">Member filter ({memberFilter.size})</label>
                <select
                  multiple
                  value={[...memberFilter]}
                  onChange={e => {
                    const selected = new Set(Array.from(e.target.selectedOptions).map(o => o.value));
                    setMemberFilter(selected);
                  }}
                  className="mt-0.5 w-full h-[80px] px-2 py-1 border border-border rounded text-xs bg-surface font-data"
                >
                  {members.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
              <div className="flex-1 min-w-[200px]">
                <label className="metric-label">Ticker search</label>
                <input
                  type="text"
                  value={tickerSearch}
                  onChange={e => setTickerSearch(e.target.value.toUpperCase())}
                  placeholder="AAPL, TSLA"
                  className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
                />
              </div>
            </div>
            <div className="overflow-x-auto max-h-[500px]">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                  <tr>
                    <th className="text-left py-1.5 px-2">Member</th>
                    <th className="text-left py-1.5 px-2">State</th>
                    <th className="text-left py-1.5 px-2">Ticker</th>
                    <th className="text-left py-1.5 px-2">Type</th>
                    <th className="text-left py-1.5 px-2">Trade Date</th>
                    <th className="text-left py-1.5 px-2">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.slice(0, 500).map((tr, i) => (
                    <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2 font-semibold">{tr.member ?? "—"}</td>
                      <td className="py-1 px-2 text-text-muted">{tr.state ?? ""}</td>
                      <td className="py-1 px-2">{tr.ticker}</td>
                      <td className="py-1 px-2">
                        <span className={tr.type === "Purchase" ? "text-gain" : tr.type === "Sale" ? "text-loss" : "text-text-muted"}>
                          {tr.type}
                        </span>
                      </td>
                      <td className="py-1 px-2">{tr.date && tr.date !== "NaT" ? tr.date.slice(0, 10) : "—"}</td>
                      <td className="py-1 px-2">{tr.amount}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filtered.length > 500 && (
                <div className="text-xs text-text-muted p-2">Showing first 500 of {filtered.length.toLocaleString()} filtered rows.</div>
              )}
            </div>
          </div>
        </>
      )}

      <div className="text-xs text-text-muted px-3 py-2 border border-border rounded">
        <b>Note:</b> Senate trades are filed separately via the{" "}
        <a className="text-accent hover:underline" href="https://efdsearch.senate.gov/search/" target="_blank" rel="noreferrer">
          Senate eFD portal
        </a>
        . House data shown here is parsed directly from official PTR filings.
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 3 — ACTIVIST INVESTORS (13D)
// ══════════════════════════════════════════════════════════════

function ActivistTab({ t, L }: { t: ChartTheme; L: ReturnType<typeof getBaseLayout> }) {
  const [days, setDays] = useState(90);
  const [tickerSearch, setTickerSearch] = useState("");
  const [showNew, setShowNew] = useState(true);
  const [showAmd, setShowAmd] = useState(true);
  const [activistFilter, setActivistFilter] = useState<Set<string>>(new Set());

  const load = useMutation({ mutationFn: (d: number) => fetchRecent13D(d) });

  const filings: Activist13D[] = useMemo(() => {
    if (!load.data) return [];
    const base = load.data.data;
    if (!tickerSearch.trim()) return base;
    const searches = tickerSearch.split(",").map(s => s.trim().toUpperCase()).filter(Boolean);
    return base.filter(f => searches.includes(f.ticker));
  }, [load.data, tickerSearch]);

  const newFilings = filings.filter(f => f.is_new);
  const amendments = filings.filter(f => !f.is_new);

  const topActivists = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of filings) m.set(f.activist, (m.get(f.activist) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);
  }, [filings]);

  const activists = useMemo(() => [...new Set(filings.map(f => f.activist))].sort(), [filings]);

  const timeline = useMemo(() => {
    const m = new Map<string, { new: number; amd: number }>();
    for (const f of filings) {
      const d = new Date(f.filed);
      if (!Number.isFinite(d.getTime())) continue;
      const day = d.getUTCDay();
      const monday = new Date(d);
      monday.setUTCDate(d.getUTCDate() - (day === 0 ? 6 : day - 1));
      const key = monday.toISOString().slice(0, 10);
      const rec = m.get(key) ?? { new: 0, amd: 0 };
      if (f.is_new) rec.new++; else rec.amd++;
      m.set(key, rec);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [filings]);

  const displayed = filings.filter(f => {
    if (f.is_new && !showNew) return false;
    if (!f.is_new && !showAmd) return false;
    if (activistFilter.size > 0 && !activistFilter.has(f.activist)) return false;
    return true;
  });

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="text-sm font-bold">Activist Investor Positions (13D Filings)</div>
        <div className="text-xs text-text-muted mb-3">
          Filed when someone acquires &gt;5% of a company with intent to influence. Often precedes major price moves.
        </div>
        <div className="flex items-end gap-3 flex-wrap">
          <div className="min-w-[200px]">
            <label className="metric-label">Lookback: {days} days</label>
            <input type="range" min={30} max={365} value={days}
              onChange={e => setDays(parseInt(e.target.value))}
              className="w-full mt-1" />
          </div>
          <div className="flex-1 min-w-[200px]">
            <label className="metric-label">Search ticker</label>
            <input
              type="text"
              value={tickerSearch}
              onChange={e => setTickerSearch(e.target.value.toUpperCase())}
              placeholder="e.g. CVNA, PAYC"
              className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
            />
          </div>
          <button
            onClick={() => load.mutate(days)}
            disabled={load.isPending}
            className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {load.isPending ? "Searching…" : "Search 13D"}
          </button>
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-10">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {load.data && filings.length === 0 && !load.isPending && (
        <div className="card text-sm text-text-muted py-6 px-5">No recent 13D filings found.</div>
      )}

      {filings.length > 0 && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Total Filings" value={String(filings.length)} />
              <Metric label="New Positions" value={String(newFilings.length)} deltaType="gain" />
              <Metric label="Amendments" value={String(amendments.length)} />
              <Metric label="Unique Targets" value={String(new Set(filings.map(f => f.target)).size)} />
            </div>
          </div>

          {newFilings.length > 0 && (
            <div className="card">
              <div className="font-semibold text-sm mb-2">New Activist Positions (Initial 13D)</div>
              <div className="space-y-2">
                {newFilings.map((row, i) => (
                  <div key={i}
                    className="p-2.5 rounded border border-border"
                    style={{ borderLeft: `3px solid ${t.accent}`, background: "rgba(88,166,255,0.04)" }}>
                    <div className="flex flex-wrap items-baseline gap-2">
                      {row.ticker && (
                        <span className="px-1.5 py-0.5 rounded text-xs font-bold font-data"
                          style={{ background: t.spot, color: "#000" }}>
                          {row.ticker}
                        </span>
                      )}
                      <span className="font-semibold text-sm">{row.target.slice(0, 70)}</span>
                    </div>
                    <div className="text-xs text-text-muted mt-1">
                      <span>Activist: </span>
                      <span className="font-semibold" style={{ color: t.spot }}>{row.activist.slice(0, 60)}</span>
                      <span className="ml-2">Filed {shortDate(row.filed)}</span>
                      {row.url && (
                        <a href={row.url} target="_blank" rel="noreferrer"
                          className="ml-2 text-accent hover:underline">Filing →</a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {topActivists.length > 1 && (
            <div className="card">
              <Plot
                data={[{
                  type: "bar", orientation: "h",
                  y: topActivists.map(([a]) => a),
                  x: topActivists.map(([, n]) => n),
                  marker: { color: t.spot },
                  text: topActivists.map(([, n]) => String(n)), textposition: "outside",
                }]}
                layout={{
                  ...L, height: CHART_HEIGHT.normal,
                  title: { text: "Most Active Filers", font: { size: 13, color: t.text } },
                  xaxis: { title: { text: "# 13D Filings" }, gridcolor: t.grid },
                  yaxis: { gridcolor: t.grid, autorange: "reversed" },
                  margin: { l: 260, r: 40, t: 40, b: 40 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          {timeline.length > 5 && (
            <div className="card">
              <Plot
                data={[
                  { type: "bar", name: "New 13D", x: timeline.map(([d]) => d), y: timeline.map(([, v]) => v.new), marker: { color: t.accent } },
                  { type: "bar", name: "Amendment", x: timeline.map(([d]) => d), y: timeline.map(([, v]) => v.amd), marker: { color: t.muted } },
                ]}
                layout={{
                  ...L, height: CHART_HEIGHT.compact + 40, barmode: "stack",
                  title: { text: "Filing Activity by Week", font: { size: 13, color: t.text } },
                  yaxis: { title: { text: "Filings" }, gridcolor: t.grid },
                  xaxis: { gridcolor: t.grid },
                  legend: { orientation: "h", y: -0.18 },
                  margin: { l: 50, r: 20, t: 40, b: 50 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          <div className="card">
            <div className="font-semibold text-sm mb-2">All Filings</div>
            <div className="flex flex-wrap items-end gap-3 mb-3">
              <div>
                <label className="metric-label">Filing type</label>
                <div className="flex gap-1 mt-0.5">
                  <button onClick={() => setShowNew(v => !v)}
                    className={`px-2 py-1 text-xs rounded border ${showNew ? "bg-accent text-white border-accent" : "border-border text-text-muted hover:bg-surface-alt"}`}>
                    New 13D
                  </button>
                  <button onClick={() => setShowAmd(v => !v)}
                    className={`px-2 py-1 text-xs rounded border ${showAmd ? "bg-accent text-white border-accent" : "border-border text-text-muted hover:bg-surface-alt"}`}>
                    Amendment
                  </button>
                </div>
              </div>
              <div className="flex-1 min-w-[220px]">
                <label className="metric-label">Activist ({activistFilter.size})</label>
                <select
                  multiple
                  value={[...activistFilter]}
                  onChange={e => {
                    const sel = new Set(Array.from(e.target.selectedOptions).map(o => o.value));
                    setActivistFilter(sel);
                  }}
                  className="mt-0.5 w-full h-[80px] px-2 py-1 border border-border rounded text-xs bg-surface font-data"
                >
                  {activists.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
              </div>
            </div>
            <div className="overflow-x-auto max-h-[450px]">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                  <tr>
                    <th className="text-left py-1.5 px-2">Filed</th>
                    <th className="text-left py-1.5 px-2">Form</th>
                    <th className="text-left py-1.5 px-2">Ticker</th>
                    <th className="text-left py-1.5 px-2">Target</th>
                    <th className="text-left py-1.5 px-2">Activist</th>
                    <th className="text-left py-1.5 px-2">Filing</th>
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((row, i) => (
                    <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2">{shortDate(row.filed)}</td>
                      <td className="py-1 px-2">{row.form}</td>
                      <td className="py-1 px-2 font-bold">{row.ticker}</td>
                      <td className="py-1 px-2">{row.target}</td>
                      <td className="py-1 px-2">{row.activist}</td>
                      <td className="py-1 px-2">
                        {row.url ? (
                          <a href={row.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                            View
                          </a>
                        ) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 4 — COMPANY GUIDANCE (+ WALL STREET CONSENSUS + EARNINGS CAL)
// ══════════════════════════════════════════════════════════════

function GuidanceTab({ t, L }: { t: ChartTheme; L: ReturnType<typeof getBaseLayout> }) {
  const [ticker, setTicker] = useState("NVDA");
  const [quarters, setQuarters] = useState(6);
  const [transcriptUrls, setTranscriptUrls] = useState("");
  const [calDays, setCalDays] = useState(7);
  const [loadedTicker, setLoadedTicker] = useState<string | null>(null);

  // Main guidance loader — triggers both 8-K + transcript flows
  const load = useMutation({
    mutationFn: async () => {
      const tk = ticker.trim().toUpperCase();
      if (!tk) throw new Error("Enter a ticker");
      const [pressRes, discovered] = await Promise.all([
        fetchGuidanceHistory(tk, quarters),
        transcriptUrls.trim()
          ? Promise.resolve({ urls: transcriptUrls.split("\n").map(s => s.trim()).filter(u => u.startsWith("http")) })
          : fetchTranscriptUrls(tk, 4).then(r => ({ urls: r.urls })),
      ]);
      let callRows: GuidanceRow[] = [];
      if (discovered.urls.length > 0) {
        try {
          const res = await fetchTranscriptGuidance(tk, discovered.urls);
          callRows = res.data;
        } catch (e) {
          console.warn("transcript guidance failed", e);
        }
      }
      return {
        ticker: tk,
        press: pressRes.data.map(r => ({ ...r, source: "8-K Press Release" as const })),
        call: callRows.map(r => ({ ...r, source: "Earnings Call" as const })),
        discoveredUrls: discovered.urls,
      };
    },
    onSuccess: d => setLoadedTicker(d.ticker),
  });

  // Consensus + earnings history + insider for the loaded ticker
  const analystQ = useQuery({
    queryKey: ["analyst-estimates", loadedTicker],
    queryFn: () => fetchAnalystEstimates(loadedTicker!),
    enabled: !!loadedTicker,
    staleTime: 30 * 60 * 1000,
  });
  const earningsHistQ = useQuery({
    queryKey: ["earnings-history", loadedTicker],
    queryFn: () => fetchEarningsHistory(loadedTicker!),
    enabled: !!loadedTicker,
    staleTime: 30 * 60 * 1000,
  });
  const insiderQ = useQuery({
    queryKey: ["insider-txn", loadedTicker],
    queryFn: () => fetchInsiderTransactions(loadedTicker!),
    enabled: !!loadedTicker,
    staleTime: 30 * 60 * 1000,
  });

  // Earnings calendar (standalone section)
  const calQ = useQuery({
    queryKey: ["earnings-cal", calDays],
    queryFn: () => fetchEdgarEarningsCalendar(calDays),
    staleTime: 10 * 60 * 1000,
  });

  const combined = useMemo(() => {
    if (!load.data) return [];
    const all = [...load.data.press, ...load.data.call];
    return all.sort((a, b) => (a.filed ?? "").localeCompare(b.filed ?? ""));
  }, [load.data]);

  const revTrend = combined.filter(r => r.revenue != null);
  const gmRows = combined.filter(r => r.gross_margin != null);
  const opexRows = combined.filter(r => r.opex != null);

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="text-sm font-bold">Company Guidance Tracker</div>
        <div className="text-xs text-text-muted mb-3">
          Forward guidance from SEC 8-K press releases and Motley Fool earnings call transcripts.
        </div>
        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-[200px]">
            <label className="metric-label">Ticker</label>
            <input
              value={ticker}
              onChange={e => setTicker(e.target.value.toUpperCase())}
              placeholder="NVDA, AMZN, AAPL"
              className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
            />
          </div>
          <div>
            <label className="metric-label">Quarters</label>
            <select value={quarters} onChange={e => setQuarters(parseInt(e.target.value))}
              className="mt-0.5 px-2 py-1.5 border border-border rounded text-sm bg-surface">
              {[4, 6, 8, 10].map(q => <option key={q} value={q}>{q}</option>)}
            </select>
          </div>
          <button
            onClick={() => load.mutate()}
            disabled={load.isPending}
            className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {load.isPending ? "Loading…" : "Load Guidance"}
          </button>
        </div>
        <div className="mt-3">
          <label className="metric-label">
            Motley Fool transcript URLs (optional — one per line; auto-discovered if blank)
          </label>
          <textarea
            value={transcriptUrls}
            onChange={e => setTranscriptUrls(e.target.value)}
            rows={2}
            placeholder="https://www.fool.com/earnings/call-transcripts/…"
            className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-xs bg-surface font-data"
          />
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-10">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-3">Parsing 8-K filings + earnings transcripts…</div>
        </div>
      )}

      {load.isError && (
        <div className="card text-sm text-loss py-4 px-5">
          {(load.error as Error)?.message ?? "Guidance load failed."}
        </div>
      )}

      {load.data && combined.length === 0 && (
        <div className="card text-sm text-text-muted py-6 px-5">
          No guidance found for {load.data.ticker} from 8-K press releases.
          {load.data.discoveredUrls.length === 0 && (
            <> To add earnings call data, paste transcript URLs above.</>
          )}
        </div>
      )}

      {combined.length > 0 && load.data && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Ticker" value={load.data.ticker} />
              <Metric label="From 8-K" value={String(load.data.press.length)} />
              <Metric label="From Earnings Calls" value={String(load.data.call.length)} />
            </div>
          </div>

          {revTrend.length > 0 && (
            <div className="card">
              <Plot
                data={(["8-K Press Release", "Earnings Call"] as const).flatMap((src, si) => {
                  const sub = revTrend.filter(r => (r as GuidanceRow & { source: string }).source === src);
                  if (sub.length === 0) return [];
                  const color = si === 0 ? t.accent : t.spot;
                  const xs = sub.map(r => r.quarter ?? shortDate(r.filed));
                  return [
                    {
                      x: xs, y: sub.map(r => (r.revenue ?? 0) / 1e9),
                      type: "scatter" as const, mode: "lines+markers" as const,
                      name: `Revenue (${src})`,
                      line: { color, width: 3, dash: si === 0 ? "solid" as const : "dash" as const },
                      marker: { size: 10 },
                    },
                    ...(sub.some(r => r.revenue_high != null) ? [{
                      x: xs, y: sub.map(r => (r.revenue_high ?? 0) / 1e9),
                      type: "scatter" as const, mode: "markers" as const,
                      name: `Revenue High (${src})`,
                      marker: { size: 7, color, symbol: "diamond" as const },
                    }] : []),
                  ];
                })}
                layout={{
                  ...L, height: CHART_HEIGHT.normal,
                  title: { text: `${load.data.ticker} — Revenue Guidance Trend ($B)`, font: { size: 14, color: t.text } },
                  yaxis: { title: { text: "Revenue ($B)" }, gridcolor: t.grid },
                  xaxis: { gridcolor: t.grid },
                  legend: { orientation: "h", y: -0.18 },
                  margin: { l: 60, r: 20, t: 40, b: 60 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          {(gmRows.length > 0 || opexRows.length > 0) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {gmRows.length > 0 && (
                <div className="card">
                  <Plot
                    data={[{
                      type: "bar",
                      x: gmRows.map(r => r.quarter ?? shortDate(r.filed)),
                      y: gmRows.map(r => r.gross_margin ?? 0),
                      marker: {
                        color: gmRows.map(r => (r as GuidanceRow & { source: string }).source === "8-K Press Release" ? t.accent : t.spot),
                      },
                      text: gmRows.map(r => `${(r.gross_margin ?? 0).toFixed(1)}%`),
                      textposition: "outside",
                    }]}
                    layout={{
                      ...L, height: CHART_HEIGHT.compact + 40,
                      title: { text: "Gross Margin Guidance (%)", font: { size: 13, color: t.text } },
                      yaxis: { title: { text: "%" }, gridcolor: t.grid },
                      xaxis: { gridcolor: t.grid },
                      margin: { l: 50, r: 20, t: 40, b: 40 },
                    }}
                    config={{ displayModeBar: false, responsive: true }}
                    style={{ width: "100%" }}
                  />
                </div>
              )}
              {opexRows.length > 0 && (
                <div className="card">
                  <Plot
                    data={[{
                      type: "bar",
                      x: opexRows.map(r => r.quarter ?? shortDate(r.filed)),
                      y: opexRows.map(r => (r.opex ?? 0) / 1e9),
                      marker: { color: t.loss },
                    }]}
                    layout={{
                      ...L, height: CHART_HEIGHT.compact + 40,
                      title: { text: "Operating Expenses Guidance ($B)", font: { size: 13, color: t.text } },
                      yaxis: { title: { text: "OpEx ($B)" }, gridcolor: t.grid },
                      xaxis: { gridcolor: t.grid },
                      margin: { l: 50, r: 20, t: 40, b: 40 },
                    }}
                    config={{ displayModeBar: false, responsive: true }}
                    style={{ width: "100%" }}
                  />
                </div>
              )}
            </div>
          )}

          <div className="card">
            <div className="font-semibold text-sm mb-2">Guidance History</div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted">
                  <tr>
                    {["Filed", "Source", "Quarter", "Revenue", "Gross Margin", "EPS", "OpEx"].map(h => (
                      <th key={h} className="text-left py-1.5 px-2">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {combined.map((r, i) => {
                    const rowWithSrc = r as GuidanceRow & { source: string };
                    const rev = r.revenue != null
                      ? (r.revenue_high != null && r.revenue_high !== r.revenue
                        ? `${fmtBn(r.revenue)} – ${fmtBn(r.revenue_high)}`
                        : fmtBn(r.revenue))
                      : (r.revenue_growth_low != null
                        ? `+${r.revenue_growth_low.toFixed(0)}% – +${(r.revenue_growth_high ?? 0).toFixed(0)}% YoY`
                        : "");
                    return (
                      <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                        <td className="py-1 px-2">{shortDate(r.filed)}</td>
                        <td className="py-1 px-2">{rowWithSrc.source}</td>
                        <td className="py-1 px-2">{r.quarter ?? ""}</td>
                        <td className="py-1 px-2">{rev}</td>
                        <td className="py-1 px-2">{r.gross_margin != null ? `${r.gross_margin.toFixed(1)}%` : ""}</td>
                        <td className="py-1 px-2">{r.eps != null ? `$${r.eps.toFixed(2)}` : ""}</td>
                        <td className="py-1 px-2">{r.opex != null ? fmtBn(r.opex) : ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <details className="card">
            <summary className="cursor-pointer text-sm font-semibold">Raw Outlook Text</summary>
            <div className="mt-3 space-y-3">
              {combined.map((r, i) => {
                if (!r.outlook) return null;
                const src = (r as GuidanceRow & { source: string }).source === "8-K Press Release" ? "8-K" : "Call";
                return (
                  <div key={i} className="border-b border-border/50 pb-3 last:border-0">
                    <div className="text-xs font-semibold mb-1">
                      [{src}] {r.quarter ?? shortDate(r.filed)}
                    </div>
                    <div className="text-xs text-text-muted whitespace-pre-wrap">{r.outlook.slice(0, 600)}</div>
                  </div>
                );
              })}
            </div>
          </details>
        </>
      )}

      {/* Wall Street consensus section */}
      {loadedTicker && (
        <>
          <div className="card card-compact">
            <div className="text-sm font-bold">Wall Street Consensus — {loadedTicker}</div>
            <div className="text-xs text-text-muted">
              Analyst estimates, price targets, earnings surprises, and insider activity via Yahoo Finance.
            </div>
          </div>

          {analystQ.data?.data && Object.keys(analystQ.data.data).length > 0 && (() => {
            const d = analystQ.data.data;
            const price = d.current_price;
            const target = d.price_target_mean;
            const upside = price && target ? ((target - price) / price) * 100 : null;
            const rating = (d.recommendation ?? "").replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()) || "N/A";
            return (
              <>
                <div className="card card-compact">
                  <div className="flex flex-wrap gap-6">
                    <Metric label="Price" value={price != null ? `$${price.toFixed(2)}` : "—"} />
                    <Metric
                      label="Target (Mean)"
                      value={target != null ? `$${target.toFixed(0)}` : "—"}
                      delta={upside != null ? fmtPctSigned(upside) : undefined}
                      deltaType={upside != null ? (upside > 0 ? "gain" : "loss") : undefined}
                    />
                    <Metric label="Recommendation" value={rating} />
                    <Metric label="Forward P/E" value={d.forward_pe != null ? d.forward_pe.toFixed(1) : "—"} />
                    <Metric
                      label="Short % Float"
                      value={d.short_pct_float != null ? `${(d.short_pct_float * 100).toFixed(1)}%` : "—"}
                    />
                  </div>
                </div>
                <div className="card">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                    <div>
                      <div className="font-semibold text-sm mb-1">Revenue Estimates</div>
                      <ul className="text-xs space-y-1">
                        {d.rev_est_current_q != null && (
                          <li>Current Quarter: <b>{fmtBn(d.rev_est_current_q)}</b></li>
                        )}
                        {d.rev_est_current_y != null && (
                          <li>
                            Current Year: <b>{fmtBn(d.rev_est_current_y)}</b>
                            {d.rev_growth_current_y != null && (
                              <span className="text-text-muted"> ({(d.rev_growth_current_y * 100).toFixed(0)}% YoY)</span>
                            )}
                          </li>
                        )}
                      </ul>
                    </div>
                    <div>
                      <div className="font-semibold text-sm mb-1">EPS Estimates</div>
                      <ul className="text-xs space-y-1">
                        {d.eps_est_current_q != null && <li>Current Quarter: <b>${d.eps_est_current_q.toFixed(2)}</b></li>}
                        {d.eps_est_current_y != null && <li>Current Year: <b>${d.eps_est_current_y.toFixed(2)}</b></li>}
                        {d.eps_est_next_y != null && <li>Next Year: <b>${d.eps_est_next_y.toFixed(2)}</b></li>}
                      </ul>
                    </div>
                  </div>
                </div>
              </>
            );
          })()}

          {(() => {
            // Drop rows where yfinance didn't return a surprise — plotting
            // them as 0% produces a misleading neutral bar that looks like
            // a real reading.
            const surpRows = (earningsHistQ.data?.data ?? []).filter(r => r.surprise_pct != null);
            if (surpRows.length === 0) return null;
            return (
              <div className="card">
                <Plot
                  data={[{
                    type: "bar",
                    x: surpRows.map(r => String(r.quarter)),
                    y: surpRows.map(r => (r.surprise_pct as number) * 100),
                    marker: {
                      color: surpRows.map(r => (r.surprise_pct as number) >= 0 ? t.accent : t.loss),
                    },
                    text: surpRows.map(r => fmtPctSigned((r.surprise_pct as number) * 100)),
                    textposition: "outside",
                  }]}
                  layout={{
                    ...L, height: CHART_HEIGHT.compact + 40,
                    title: { text: "Earnings Surprise History (% Beat/Miss)", font: { size: 13, color: t.text } },
                    yaxis: { title: { text: "Surprise %" }, gridcolor: t.grid },
                    xaxis: { gridcolor: t.grid },
                    margin: { l: 50, r: 20, t: 40, b: 40 },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            );
          })()}

          {insiderQ.data && insiderQ.data.data.length > 0 && (
            <div className="card">
              <div className="font-semibold text-sm mb-2">Recent Insider Transactions</div>
              <div className="overflow-x-auto max-h-[420px]">
                <table className="w-full text-xs font-data">
                  <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                    <tr>
                      {["Date", "Insider", "Position", "Transaction", "Shares", "Value", "Text"].map(h => (
                        <th key={h} className="text-left py-1.5 px-2">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {insiderQ.data.data.slice(0, 15).map((row, i) => {
                      const r = row as Record<string, unknown>;
                      return (
                        <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                          <td className="py-1 px-2">{String(r["Start Date"] ?? "—").slice(0, 10)}</td>
                          <td className="py-1 px-2 font-semibold">{(r.Insider as string) ?? "—"}</td>
                          <td className="py-1 px-2">{(r.Position as string) ?? "—"}</td>
                          <td className="py-1 px-2">{(r.Transaction as string) ?? "—"}</td>
                          <td className="py-1 px-2 text-right">{r.Shares != null ? Number(r.Shares).toLocaleString() : "—"}</td>
                          <td className="py-1 px-2 text-right">{r.Value != null ? fmtBn(Number(r.Value)) : "—"}</td>
                          <td className="py-1 px-2 text-text-muted">{(r.Text as string) ?? ""}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {/* Earnings calendar standalone */}
      <div className="card">
        <div className="flex items-baseline justify-between flex-wrap gap-2 mb-2">
          <div className="font-semibold text-sm">Recent Earnings Releases</div>
          <select
            value={calDays}
            onChange={e => setCalDays(parseInt(e.target.value))}
            className="px-2 py-1 border border-border rounded text-xs bg-surface"
          >
            {[3, 7, 14, 30].map(d => <option key={d} value={d}>Last {d} days</option>)}
          </select>
        </div>
        {calQ.isPending ? (
          <div className="text-xs text-text-muted">Loading…</div>
        ) : calQ.data && calQ.data.count > 0 ? (
          <>
            <div className="text-xs text-text-muted mb-2">{calQ.data.count} earnings releases in the last {calDays} days.</div>
            <div className="overflow-x-auto max-h-[350px]">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                  <tr>
                    <th className="text-left py-1.5 px-2">Filed</th>
                    <th className="text-left py-1.5 px-2">Ticker</th>
                    <th className="text-left py-1.5 px-2">Company</th>
                  </tr>
                </thead>
                <tbody>
                  {calQ.data.data.map((row, i) => (
                    <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2">{shortDate(row.filed)}</td>
                      <td className="py-1 px-2 font-bold">{row.ticker}</td>
                      <td className="py-1 px-2">{row.company}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <div className="text-xs text-text-muted">No earnings releases found in the last {calDays} days.</div>
        )}
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 5 — MACRO & RATES
// ══════════════════════════════════════════════════════════════

function MacroTab({ t, L }: { t: ChartTheme; L: ReturnType<typeof getBaseLayout> }) {
  const [loaded, setLoaded] = useState(false);
  const [customSeries, setCustomSeries] = useState("CPIAUCSL");
  const macroQ = useQuery({
    queryKey: ["macro-dashboard"],
    queryFn: fetchMacroDashboard,
    enabled: loaded,
    staleTime: 10 * 60 * 1000,
  });
  const customQ = useMutation({ mutationFn: (id: string) => fetchFredSeriesCustom(id.toUpperCase(), 252) });

  const data = macroQ.data;
  const latest = data?.latest ?? {};
  const spread = latest["T10Y2Y"];

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="text-sm font-bold">Macro & Rates Dashboard</div>
        <div className="text-xs text-text-muted mb-3">
          Key economic indicators from FRED (Federal Reserve Economic Data). Requires a free FRED API key set as FRED_API_KEY.
        </div>
        <button
          onClick={() => setLoaded(true)}
          disabled={macroQ.isFetching}
          className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
        >
          {macroQ.isFetching ? "Loading…" : loaded ? "Refresh Macro Data" : "Load Macro Data"}
        </button>
      </div>

      {loaded && macroQ.isPending && (
        <div className="card text-center py-10">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {data && Object.keys(data.series).length === 0 && (
        <div className="card text-sm text-text-muted py-6 px-5">
          <b>FRED API key not configured.</b> Get a free key at{" "}
          <a className="text-accent hover:underline" href="https://fred.stlouisfed.org/docs/api/api_key.html" target="_blank" rel="noreferrer">
            fred.stlouisfed.org
          </a>{" "}
          and set FRED_API_KEY.
        </div>
      )}

      {data && Object.keys(data.series).length > 0 && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Fed Funds Rate" value={latest["DFF"] != null ? `${latest["DFF"].toFixed(2)}%` : "—"} />
              <Metric label="10Y Treasury" value={latest["DGS10"] != null ? `${latest["DGS10"].toFixed(2)}%` : "—"} />
              <Metric label="2Y Treasury" value={latest["DGS2"] != null ? `${latest["DGS2"].toFixed(2)}%` : "—"} />
              <Metric
                label="Yield Curve (10Y-2Y)"
                value={spread != null ? `${spread.toFixed(2)}%` : "—"}
                delta={spread != null && spread < 0 ? "Inverted" : undefined}
                deltaType={spread != null && spread < 0 ? "loss" : undefined}
              />
              <Metric label="Unemployment" value={latest["UNRATE"] != null ? `${latest["UNRATE"].toFixed(1)}%` : "—"} />
            </div>
          </div>

          {data.series["T10Y2Y"] && (
            <div className="card">
              <Plot
                data={[{
                  x: data.series["T10Y2Y"].map(p => p.date),
                  y: data.series["T10Y2Y"].map(p => p.value),
                  type: "scatter", mode: "lines",
                  name: "10Y-2Y Spread",
                  line: { color: t.accent, width: 2 },
                  fill: "tozeroy",
                  fillcolor: "rgba(88,166,255,0.08)",
                }]}
                layout={{
                  ...L, height: CHART_HEIGHT.compact + 40,
                  title: { text: "Yield Curve (10Y - 2Y Treasury Spread)", font: { size: 14, color: t.text } },
                  yaxis: { title: { text: "Spread (%)" }, gridcolor: t.grid },
                  xaxis: { gridcolor: t.grid },
                  margin: { l: 50, r: 20, t: 40, b: 40 },
                  shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 0, y1: 0, line: { color: t.loss, dash: "dash" } }],
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {data.series["DCOILWTICO"] && (
              <div className="card">
                <Plot
                  data={[{
                    x: data.series["DCOILWTICO"].map(p => p.date),
                    y: data.series["DCOILWTICO"].map(p => p.value),
                    type: "scatter", mode: "lines",
                    line: { color: t.spot, width: 2 },
                  }]}
                  layout={{
                    ...L, height: CHART_HEIGHT.compact + 40,
                    title: { text: "WTI Crude Oil ($/bbl)", font: { size: 13, color: t.text } },
                    yaxis: { title: { text: "$/barrel" }, gridcolor: t.grid },
                    xaxis: { gridcolor: t.grid },
                    margin: { l: 50, r: 20, t: 40, b: 40 },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            )}
            {data.series["DHHNGSP"] && (
              <div className="card">
                <Plot
                  data={[{
                    x: data.series["DHHNGSP"].map(p => p.date),
                    y: data.series["DHHNGSP"].map(p => p.value),
                    type: "scatter", mode: "lines",
                    line: { color: t.accent, width: 2 },
                  }]}
                  layout={{
                    ...L, height: CHART_HEIGHT.compact + 40,
                    title: { text: "Henry Hub Natural Gas ($/MMBtu)", font: { size: 13, color: t.text } },
                    yaxis: { title: { text: "$/MMBtu" }, gridcolor: t.grid },
                    xaxis: { gridcolor: t.grid },
                    margin: { l: 50, r: 20, t: 40, b: 40 },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            )}
          </div>

          {data.series["DFF"] && (
            <div className="card">
              <Plot
                data={[{
                  x: data.series["DFF"].map(p => p.date),
                  y: data.series["DFF"].map(p => p.value),
                  type: "scatter", mode: "lines",
                  line: { color: t.loss, width: 2 },
                }]}
                layout={{
                  ...L, height: CHART_HEIGHT.compact + 40,
                  title: { text: "Federal Funds Effective Rate (%)", font: { size: 13, color: t.text } },
                  yaxis: { title: { text: "%" }, gridcolor: t.grid },
                  xaxis: { gridcolor: t.grid },
                  margin: { l: 50, r: 20, t: 40, b: 40 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          <details className="card">
            <summary className="cursor-pointer text-sm font-semibold">Explore FRED Series</summary>
            <div className="mt-3 flex items-end gap-3 flex-wrap">
              <div className="flex-1 min-w-[200px]">
                <label className="metric-label">FRED Series ID</label>
                <input
                  value={customSeries}
                  onChange={e => setCustomSeries(e.target.value.toUpperCase())}
                  placeholder="CPIAUCSL, GDP, UNRATE"
                  className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
                />
              </div>
              <button
                onClick={() => customSeries && customQ.mutate(customSeries)}
                disabled={customQ.isPending}
                className="px-4 py-2 bg-accent text-white text-xs font-semibold rounded hover:bg-accent-hover disabled:opacity-50"
              >
                {customQ.isPending ? "Fetching…" : "Fetch"}
              </button>
            </div>
            {customQ.data && customQ.data.data.length > 0 && (
              <div className="mt-3">
                <Plot
                  data={[{
                    x: customQ.data.data.map(p => p.date),
                    y: customQ.data.data.map(p => p.value),
                    type: "scatter", mode: "lines",
                    line: { color: t.accent, width: 2 },
                  }]}
                  layout={{
                    ...L, height: CHART_HEIGHT.compact + 40,
                    title: { text: data.labels[customQ.data.series_id] ?? customQ.data.series_id, font: { size: 13, color: t.text } },
                    yaxis: { gridcolor: t.grid },
                    xaxis: { gridcolor: t.grid },
                    margin: { l: 50, r: 20, t: 40, b: 40 },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            )}
            {customQ.isSuccess && customQ.data && customQ.data.data.length === 0 && (
              <div className="text-xs text-text-muted mt-3">No data returned. Check the series ID.</div>
            )}
          </details>
        </>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 6 — 8-K MATERIAL EVENTS
// ══════════════════════════════════════════════════════════════

function EightKTab({ t, L: _L }: { t: ChartTheme; L: ReturnType<typeof getBaseLayout> }) {
  const [ticker, setTicker] = useState("");
  const [days, setDays] = useState(30);
  const load = useMutation({ mutationFn: () => fetch8KEvents(ticker.toUpperCase(), days) });
  const events: EightKEvent[] = load.data?.data ?? [];

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="text-sm font-bold">Material Events (8-K Filings)</div>
        <div className="text-xs text-text-muted mb-3">
          Major corporate events: earnings, M&amp;A, leadership changes, contract awards. Filed within days of the event.
        </div>
        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-[200px]">
            <label className="metric-label">Ticker</label>
            <input
              value={ticker}
              onChange={e => setTicker(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === "Enter" && ticker && load.mutate()}
              placeholder="AAPL"
              className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
            />
          </div>
          <div className="min-w-[180px]">
            <label className="metric-label">Lookback: {days} days</label>
            <input type="range" min={7} max={365} value={days}
              onChange={e => setDays(parseInt(e.target.value))}
              className="w-full mt-1" />
          </div>
          <button
            onClick={() => ticker && load.mutate()}
            disabled={!ticker || load.isPending}
            className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {load.isPending ? "Searching…" : "Search"}
          </button>
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-10">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {load.isSuccess && events.length === 0 && (
        <div className="card text-sm text-text-muted py-6 px-5">
          No 8-K filings found for &apos;{ticker}&apos; in the last {days} days.
        </div>
      )}

      {events.length > 0 && (
        <div className="card">
          <div className="text-sm mb-3">
            Found <b>{events.length}</b> 8-K filings for <b>{ticker}</b>.
          </div>
          <div className="space-y-1.5">
            {events.map((evt, i) => {
              const itemsList = evt.items
                ? evt.items.split(",").map(s => s.trim()).filter(Boolean)
                : [];
              const desc = itemsList
                .filter(code => code !== "9.01")
                .map(code => ITEM_NAMES[code] ?? code)
                .join(", ");
              return (
                <div
                  key={i}
                  className="p-2 rounded"
                  style={{
                    borderLeft: `2px solid ${t.accent}`,
                    background: "rgba(255,255,255,0.02)",
                  }}
                >
                  <span className="text-xs text-text-muted font-data">{evt.filed}</span>
                  <span className="mx-2 text-xs font-bold" style={{ color: t.accent }}>8-K</span>
                  {desc && <span className="text-xs text-text-muted">{desc}</span>}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
