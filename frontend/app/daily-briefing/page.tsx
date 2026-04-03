"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchDailyBriefing, fetchMorningNote, fetchNewsIntel, addPosition, type DailyBriefingResult, type NewsItem } from "@/lib/api";
import { Metric } from "@/components/ui/metric";

const DEFAULT_WATCHLIST = ["SPY","QQQ","AAPL","MSFT","NVDA","TSLA","AMD","AMZN","META","GOOGL","NFLX","GLD","SMH","XLF","TLT","JPM","BA"];
const VIX_COLORS: Record<string,string> = { Low:"text-gain", Normal:"text-gain", Elevated:"text-warn", High:"text-loss", Extreme:"text-loss" };
const LIQ_COLORS: Record<string,string> = { A:"text-gain", B:"text-gain", C:"text-warn", D:"text-warn", F:"text-loss" };

function fmtExp(exp: string) {
  try { return new Date(exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }); }
  catch { return exp; }
}

export default function DailyBriefing() {
  const [watchlist, setWatchlist] = useState(DEFAULT_WATCHLIST.join(", "));
  const [accountSize, setAccountSize] = useState(25000);
  const [data, setData] = useState<DailyBriefingResult | null>(null);
  const [aiNote, setAiNote] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [booked, setBooked] = useState<Set<string>>(new Set());
  const [newsItems, setNewsItems] = useState<NewsItem[]>([]);
  const [newsSources, setNewsSources] = useState<Record<string, number>>({});
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsError, setNewsError] = useState("");

  const scan = useMutation({
    mutationFn: () => fetchDailyBriefing(watchlist.split(",").map(t => t.trim().toUpperCase()).filter(Boolean), accountSize),
    onSuccess: (d) => { setData(d); setAiNote(""); },
  });

  const aiMutation = useMutation({
    mutationFn: () => { if (!data) throw new Error("No scan data"); return fetchMorningNote(data, newsItems); },
    onSuccess: (r) => { if (r.success) setAiNote(r.content); else setAiNote(`Error: ${r.content}`); },
  });

  async function handleBook(opp: DailyBriefingResult["opportunities"][0]) {
    try {
      await addPosition({ ticker: opp.ticker, type: opp.type, qty: opp.contracts || 1, entry_price: opp.premium / 100,
        details: { strategy: opp.label, strikes: opp.strikes, expiration: opp.expiration, dte: opp.dte, premium: opp.premium, max_risk: opp.max_risk, pop: opp.pop },
        source_page: "daily_briefing" });
      setBooked(prev => new Set(prev).add(opp.ticker + opp.strikes + opp.expiration));
    } catch (e) { console.error(e); }
  }

  const filteredOpps = data?.opportunities.filter(o => typeFilter === "all" || o.type === typeFilter) ?? [];
  const loadNews = async () => {
    const tkList = watchlist.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
    setNewsLoading(true); setNewsError("");
    try {
      const res = await fetchNewsIntel(tkList);
      if (res.success) { setNewsItems(res.items); setNewsSources(res.sources); }
      else setNewsError(res.error || "Failed");
    } catch (e) { setNewsError((e as Error).message); }
    finally { setNewsLoading(false); }
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Market Scan</h1>
        <p className="text-text-secondary text-sm mt-1">Market intelligence, trade opportunities, and news — fact-checked.</p>
      </div>

      {/* Controls */}
      <div className="card card-compact">
        <div className="flex flex-wrap gap-3 items-end mb-3">
          <div className="flex-1 min-w-[200px]">
            <label className="metric-label">Watchlist</label>
            <textarea value={watchlist} onChange={e => setWatchlist(e.target.value)} rows={2}
              className="w-full mt-1 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface" />
          </div>
          <div>
            <label className="metric-label">Account ($)</label>
            <input type="number" value={accountSize} onChange={e => setAccountSize(+e.target.value)} step={5000}
              className="w-28 mt-1 px-2 py-2 border border-border rounded-lg text-sm font-data bg-surface" />
          </div>
        </div>
        <div className="flex gap-2">
          <button onClick={() => scan.mutate()} disabled={scan.isPending}
            className="flex-1 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {scan.isPending ? "Scanning..." : "Scan Opportunities"}
          </button>
          <button onClick={loadNews} disabled={newsLoading}
            className="px-4 py-2 border border-accent text-accent font-semibold rounded-lg hover:bg-accent hover:text-white disabled:opacity-50 text-sm transition-colors">
            {newsLoading ? "Searching..." : "News Intel"}
          </button>
        </div>
        {scan.isPending && (
          <div className="flex items-center gap-2 py-2">
            <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" />
            <span className="text-xs text-text-muted">Scanning {watchlist.split(",").filter(Boolean).length} tickers...</span>
          </div>
        )}
      </div>

      {scan.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Scan failed: {(scan.error as Error).message}</div>}

      {data && (<>
        {/* ═══ Market Context ═══ */}
        <div className="card">
          <div className="metric-label mb-3">Market Context</div>
          <div className="flex flex-wrap gap-6">
            <div>
              <div className="text-xs text-text-muted">S&P 500</div>
              <div className="text-lg font-bold font-data">${data.market_context.spy.price}</div>
              <div className={`text-sm font-data ${data.market_context.spy.change_pct >= 0 ? "text-gain" : "text-loss"}`}>
                {data.market_context.spy.change_pct >= 0 ? "+" : ""}{data.market_context.spy.change_pct}%
              </div>
            </div>
            <div>
              <div className="text-xs text-text-muted">QQQ</div>
              <div className="text-lg font-bold font-data">${data.market_context.qqq.price}</div>
              <div className={`text-sm font-data ${data.market_context.qqq.change_pct >= 0 ? "text-gain" : "text-loss"}`}>
                {data.market_context.qqq.change_pct >= 0 ? "+" : ""}{data.market_context.qqq.change_pct}%
              </div>
            </div>
            <div>
              <div className="text-xs text-text-muted">VIX</div>
              <div className="text-lg font-bold font-data">{data.market_context.vix.price}</div>
              <div className={`text-sm font-semibold ${VIX_COLORS[data.market_context.vix.regime] || ""}`}>{data.market_context.vix.regime}</div>
              {data.market_context.vix.term_structure && (
                <div className="text-[0.6rem] text-text-muted font-data">
                  Term: {data.market_context.vix.term_structure} ({data.market_context.vix.term_ratio}x)
                </div>
              )}
            </div>
            {data.market_context.fomc_events.length > 0 && (
              <div className="border-l border-border pl-4">
                <div className="text-xs text-text-muted">Events</div>
                {data.market_context.fomc_events.map((ev, i) => (
                  <div key={i} className="text-sm font-data"><span className="badge badge-warn text-[0.6rem]">{ev.type}</span> {ev.days_away}d</div>
                ))}
              </div>
            )}
          </div>
          <div className="mt-3 text-xs text-text-muted border-t border-border pt-2">
            {data.market_context.vix.regime === "Low" && "Low vol — premium is thin. Favor debit spreads or wait for expansion."}
            {data.market_context.vix.regime === "Normal" && "Normal vol — balanced environment. Focus on IVR for individual names."}
            {data.market_context.vix.regime === "Elevated" && "Elevated vol — premium sellers have edge. Favor credit spreads at 50-75 IVR."}
            {data.market_context.vix.regime === "High" && "High vol — strong premium edge BUT respect jump risk. Reduce size, widen wings."}
            {data.market_context.vix.regime === "Extreme" && "Extreme vol — crisis conditions. Very small size or avoid. Consider protection."}
            {data.market_context.vix.term_structure === "Backwardation" && " VIX in backwardation — near-term fear elevated. Front-month premium is rich."}
          </div>
        </div>

        {/* ═══ Earnings This Week ═══ */}
        {data.earnings_this_week.length > 0 && (
          <div className="card card-compact border-warn/30 bg-warn-bg">
            <div className="metric-label text-warn mb-1">Earnings This Week</div>
            <div className="flex flex-wrap gap-3">
              {data.earnings_this_week.map((e, i) => (
                <span key={i} className="text-sm font-data"><strong>{e.ticker}</strong> in {e.days}d ({e.date})</span>
              ))}
            </div>
            <p className="text-xs text-text-muted mt-1">Avoid selling premium on these tickers unless you want earnings exposure.</p>
          </div>
        )}

        {/* ═══ Risk Budget ═══ */}
        <div className="card card-compact">
          <div className="flex flex-wrap gap-6 items-center">
            <Metric label="Account" value={`$${data.risk_budget.account_size.toLocaleString()}`} />
            <Metric label="Top 5 Risk" value={`$${data.risk_budget.top5_risk.toLocaleString()}`} />
            <Metric label="% Deployed" value={`${data.risk_budget.pct_of_account}%`} />
            <Metric label="Remaining" value={`$${data.risk_budget.remaining.toLocaleString()}`} />
            <span className={`badge ${data.risk_budget.verdict === "Conservative" ? "badge-gain" : data.risk_budget.verdict === "Moderate" ? "badge-info" : data.risk_budget.verdict === "Aggressive" ? "badge-warn" : "badge-loss"}`}>
              {data.risk_budget.verdict}
            </span>
          </div>
        </div>

        {/* ═══ Warnings ═══ */}
        {data.warnings.length > 0 && (
          <div className="card card-compact border-loss/30 bg-loss-bg text-sm">
            <strong className="text-loss">Risk Warnings:</strong>
            <ul className="mt-1 space-y-0.5">{data.warnings.map((w, i) => <li key={i} className="text-text-muted">• {w}</li>)}</ul>
          </div>
        )}

        {/* ═══ Sector Exposure ═══ */}
        {Object.keys(data.sector_exposure).length > 0 && (
          <div className="card card-compact">
            <div className="metric-label mb-2">Sector Exposure (Top Opportunities)</div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(data.sector_exposure).sort(([,a],[,b]) => b - a).map(([sec, count]) => (
                <span key={sec} className={`px-2 py-0.5 rounded border text-xs font-data ${count > 3 ? "border-loss text-loss" : "border-border text-text-muted"}`}>
                  {sec}: {count}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* ═══ News Intelligence (Grok + Gemini fact-check) ═══ */}
        {(newsItems.length > 0 || newsError || newsLoading) && (
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <div className="metric-label">News Intelligence</div>
              <div className="flex gap-2 text-[0.55rem] text-text-muted">
                {newsSources.polygon_news > 0 && <span className="px-1.5 py-0.5 rounded bg-surface-alt">Polygon: {newsSources.polygon_news}</span>}
                {newsSources.earnings > 0 && <span className="px-1.5 py-0.5 rounded bg-surface-alt">Earnings: {newsSources.earnings}</span>}
                {newsSources.sec_filings > 0 && <span className="px-1.5 py-0.5 rounded bg-surface-alt">SEC: {newsSources.sec_filings}</span>}
                {newsSources.x_twitter > 0 && <span className="px-1.5 py-0.5 rounded bg-surface-alt">X/Twitter: {newsSources.x_twitter}</span>}
              </div>
            </div>
            {newsLoading && <div className="flex items-center gap-2 py-3"><div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" /><span className="text-xs text-text-muted">Searching Polygon + SEC + yfinance + X/Twitter...</span></div>}
            {newsError && <div className="text-xs text-loss mb-2">{newsError}</div>}
            <div className="space-y-2">
              {newsItems.map((item, i) => {
                const srcBadge = item.source_type === "earnings_data" ? "badge-warn" : item.source_type === "sec_filing" ? "badge-info" : item.source_type === "news_api" ? "badge-info" : "badge-warn";
                const srcLabel = item.source_type === "earnings_data" ? "EARNINGS" : item.source_type === "sec_filing" ? "SEC" : item.source_type === "news_api" ? "NEWS" : "X";
                return (
                  <div key={i} className={`p-2.5 rounded border text-xs ${item.impact === "bull" ? "border-gain/30 bg-gain/5" : item.impact === "bear" ? "border-loss/30 bg-loss/5" : "border-border"}`}>
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <span className="font-bold">{item.ticker}</span>
                      <span className={`badge text-[0.5rem] ${srcBadge}`}>{srcLabel}</span>
                      <span className={`badge text-[0.5rem] ${item.impact === "bull" ? "badge-gain" : item.impact === "bear" ? "badge-loss" : "badge-info"}`}>{item.impact}</span>
                      {item.confidence === "high" && <span className="text-gain text-[0.5rem] font-bold">✓ verified</span>}
                      {item.confidence === "medium" && <span className="text-warn text-[0.5rem]">◐ unverified</span>}
                      {item.confidence === "low" && <span className="text-loss text-[0.5rem]">⚠ suspect</span>}
                      {item.cross_verified && <span className="text-gain text-[0.5rem]">⟷ cross-confirmed</span>}
                      {item.freshness === "live" && <span className="text-accent text-[0.5rem] font-bold">● live</span>}
                      {item.freshness === "stale" && <span className="text-loss text-[0.5rem]">○ stale</span>}
                    </div>
                    <div className="text-text font-medium">{item.headline}</div>
                    <div className="flex items-center gap-2 mt-1 text-text-muted">
                      <span>{item.source}</span>
                      <span>·</span>
                      <span>{item.time}</span>
                      {item.url && <a href={item.url} target="_blank" rel="noopener" className="text-accent hover:underline">→ source</a>}
                    </div>
                  </div>
                );
              })}
            </div>
            {newsItems.length === 0 && !newsError && !newsLoading && <p className="text-xs text-text-muted">No material news found. This is normal outside market hours or during quiet periods.</p>}
            {newsItems.length > 0 && (
              <p className="text-[0.55rem] text-text-muted mt-2">
                Data pipeline: Polygon News API + SEC EDGAR + yfinance earnings + Grok X/Twitter search.
                Items from X/Twitter marked as "unverified" unless corroborated by structured data sources.
              </p>
            )}
          </div>
        )}

        {/* ═══ AI Market Note ═══ */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <div className="metric-label">AI Market Note</div>
            <button onClick={() => aiMutation.mutate()} disabled={aiMutation.isPending || !data}
              className="px-4 py-1.5 bg-accent text-white text-xs font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
              {aiMutation.isPending ? "Writing..." : aiNote ? "Regenerate" : "Generate Note"}
            </button>
          </div>
          {aiMutation.isPending && (
            <div className="flex items-center gap-2 py-3">
              <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" />
              <span className="text-xs text-text-muted">Gemini analyzing scan results...</span>
            </div>
          )}
          {aiNote && (
            <div className="text-sm leading-relaxed" dangerouslySetInnerHTML={{
              __html: aiNote
                .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                .replace(/\*\*(MARKET|NEWS|TRADES|AVOID|SIZE)(.*?)\*\*/g, '<div class="mt-3 mb-1 text-xs font-bold uppercase tracking-wider text-accent">$1$2</div>')
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/\n\n/g, '</p><p class="mt-2">')
                .replace(/\n/g, "<br/>"),
            }} />
          )}
          {!aiNote && !aiMutation.isPending && (
            <p className="text-xs text-text-muted">
              {newsItems.length > 0 ? "Ready — will incorporate news intel + scan results." : "Tip: run News Intel first for a more complete note."}
              {" "}Grounded only in your data — no hallucination.
            </p>
          )}
        </div>

        {/* ═══ Watchlist Pulse ═══ */}
        <div className="card">
          <div className="metric-label mb-2">Watchlist ({data.watchlist.length})</div>
          <div className="flex flex-wrap gap-2">
            {data.watchlist.map(w => (
              <div key={w.ticker} className={`flex items-center gap-1.5 px-2 py-1 rounded border text-xs font-data ${w.earnings ? "border-warn" : "border-border"}`}>
                <span className="font-semibold">{w.ticker}</span>
                <span className={w.change_pct >= 0 ? "text-gain" : "text-loss"}>{w.change_pct >= 0 ? "+" : ""}{w.change_pct}%</span>
                {w.earnings && <span className="text-warn text-[0.55rem]">E{w.earnings.days}d</span>}
              </div>
            ))}
          </div>
        </div>

        {/* ═══ Top Opportunities ═══ */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <div className="metric-label">Top Opportunities ({filteredOpps.length})</div>
            <div className="flex items-center gap-2">
              <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}
                className="text-xs border border-border rounded px-2 py-1 bg-surface">
                <option value="all">All Types</option>
                <option value="vertical">Verticals Only</option>
                <option value="condor">Condors Only</option>
              </select>
              <span className="text-xs text-text-muted font-data">
                {data.scan_stats.spreads_found} spreads · {data.scan_stats.condors_found} condors
              </span>
            </div>
          </div>

          {filteredOpps.length === 0 && <p className="text-sm text-text-muted py-4 text-center">No setups found. Try expanding watchlist or waiting for market hours.</p>}

          <div className="space-y-2">
            {filteredOpps.map((opp, i) => {
              const key = opp.ticker + opp.strikes + opp.expiration;
              const isBooked = booked.has(key);
              return (
                <div key={key + i} className={`border rounded-lg p-3 ${i === 0 ? "border-accent bg-accent-light" : "border-border"}`}>
                  <div className="flex justify-between items-start">
                    <div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-bold text-sm">{opp.ticker}</span>
                        <span className={`badge ${opp.type === "condor" ? "badge-info" : opp.label.includes("Bull") ? "badge-gain" : "badge-loss"}`}>{opp.label}</span>
                        <span className={`text-[0.6rem] ${LIQ_COLORS[opp.liq_grade] || ""}`}>{opp.liq_grade}</span>
                        <span className="text-[0.55rem] text-text-muted">{opp.sector}</span>
                        {opp.earnings_before && <span className="badge badge-loss text-[0.55rem]">EARN</span>}
                        {opp.inside_exp_move && <span className="badge badge-loss text-[0.55rem]">INSIDE EM</span>}
                        {i === 0 && <span className="badge badge-gain text-[0.55rem]">TOP PICK</span>}
                      </div>
                      <div className="text-xs text-text-muted font-data mt-1">
                        {opp.strikes} · {fmtExp(opp.expiration)} ({opp.dte}d) · IVR {opp.ivr?.toFixed(0) ?? "N/A"} ({opp.ivr_band})
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-lg font-bold font-data">{opp.score.toFixed(3)}</div>
                      <div className="text-[0.6rem] text-text-muted">score</div>
                    </div>
                  </div>

                  <div className="grid grid-cols-4 sm:grid-cols-8 gap-2 mt-2 text-[0.65rem] font-data">
                    <div><span className="text-text-muted block">Premium</span>${opp.premium}</div>
                    <div><span className="text-text-muted block">Risk</span>${opp.max_risk}</div>
                    <div><span className="text-text-muted block">Profit</span><span className="text-gain">${opp.max_profit}</span></div>
                    <div><span className="text-text-muted block">POP</span>{opp.pop}%</div>
                    <div><span className="text-text-muted block">R:R</span>{opp.rr_ratio}x</div>
                    <div><span className="text-text-muted block">WR</span>{opp.managed_wr}%</div>
                    <div><span className="text-text-muted block">Kelly</span>{opp.kelly_adj.toFixed(1)}%</div>
                    <div><span className="text-text-muted block">Size</span>{opp.contracts}×</div>
                  </div>

                  {/* Quick book button */}
                  <div className="mt-2 flex items-center gap-2">
                    <button onClick={() => handleBook(opp)} disabled={isBooked}
                      className={`px-3 py-1 text-xs rounded font-semibold transition-colors ${isBooked ? "bg-gain/20 text-gain" : "bg-accent/80 text-white hover:bg-accent"}`}>
                      {isBooked ? "✓ Booked" : `Book ${opp.contracts || 1}×`}
                    </button>
                    {isBooked && <span className="text-[0.6rem] text-gain">Added to Position Book</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* ═══ Timestamp ═══ */}
        <div className="text-xs text-text-muted text-center">
          Scanned at {new Date(data.market_context.timestamp).toLocaleTimeString()} ·
          Not financial advice — verify all setups before trading.
        </div>
      </>)}
    </div>
  );
}
