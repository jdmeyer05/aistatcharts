"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import {
  fetchGlobalFunds,
  fetch13FHoldings,
  type GlobalFund,
  type Holding13F,
  type Holdings13FResponse,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { AIInterpretation } from "@/components/ai-interpretation";
import { fmtBn } from "../_shared/utils";


interface FundLoadResult {
  fund: GlobalFund;
  data: Holdings13FResponse | null;
  error?: string;
}

interface ConsensusRow {
  company: string;
  cusip: string;
  fundCount: number;
  totalValue: number;
  funds: string[];
}

function computeConsensus(results: FundLoadResult[]): ConsensusRow[] {
  const acc = new Map<
    string,
    { company: string; cusip: string; fundCount: number; totalValue: number; funds: Set<string> }
  >();

  for (const res of results) {
    if (!res.data || res.data.count === 0) continue;
    const seenKeysThisFund = new Set<string>();
    for (const h of res.data.holdings) {
      if (!h.company || !h.company.trim()) continue;
      const key = h.cusip || h.company;
      // One fund shouldn't count multiple times for the same position.
      if (seenKeysThisFund.has(key)) {
        const rec = acc.get(key);
        if (rec) rec.totalValue += h.value ?? 0;
        continue;
      }
      seenKeysThisFund.add(key);
      const rec = acc.get(key) ?? {
        company: h.company.trim(),
        cusip: h.cusip ?? "",
        fundCount: 0,
        totalValue: 0,
        funds: new Set<string>(),
      };
      rec.fundCount += 1;
      rec.totalValue += h.value ?? 0;
      rec.funds.add(res.fund.name);
      acc.set(key, rec);
    }
  }

  return [...acc.values()]
    .map((r) => ({
      company: r.company,
      cusip: r.cusip,
      fundCount: r.fundCount,
      totalValue: r.totalValue,
      funds: [...r.funds],
    }))
    .sort((a, b) => b.fundCount - a.fundCount || b.totalValue - a.totalValue);
}

export default function GlobalSmartMoneyPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const fundsQ = useQuery({
    queryKey: ["global-funds"],
    queryFn: fetchGlobalFunds,
    // Stale for a day is enough — was Infinity, which meant a single early
    // failure would stick until the next hard reload and the batch button
    // stayed silently disabled.
    staleTime: 24 * 60 * 60_000,
    retry: 2,
  });

  const funds: GlobalFund[] = useMemo(() => fundsQ.data?.funds ?? [], [fundsQ.data]);

  // Batch load all funds' 13Fs in parallel. One slow fund used to pin the
  // whole batch at the SEC EDGAR worst-case latency (~30s+); per-fund timeout
  // caps that at 12s so the median case still ships fast (~1-2s) and only
  // the offenders fall off as "timeout after 12000ms".
  const FUND_TIMEOUT_MS = 12_000;
  const batchLoad = useMutation({
    mutationFn: async (): Promise<FundLoadResult[]> => {
      const withTimeout = <T,>(p: Promise<T>, ms: number): Promise<T> =>
        new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error(`timeout after ${ms}ms`)), ms);
          p.then(v => { clearTimeout(timer); resolve(v); })
           .catch(e => { clearTimeout(timer); reject(e); });
        });
      const settled = await Promise.allSettled(
        funds.map((f) => withTimeout(fetch13FHoldings(f.cik), FUND_TIMEOUT_MS)),
      );
      return funds.map((f, i) => {
        const s = settled[i];
        if (s.status === "fulfilled") return { fund: f, data: s.value };
        return { fund: f, data: null, error: (s.reason as Error)?.message ?? "fetch failed" };
      });
    },
  });

  // Per-fund drill-down — keyed by CIK so rapid switches don't race.
  const [drillCik, setDrillCik] = useState<string>("");
  const drillQ = useQuery({
    queryKey: ["13f-drill", drillCik],
    queryFn: () => fetch13FHoldings(drillCik),
    enabled: !!drillCik,
    staleTime: 30 * 60_000,
  });

  // ── Category counts for header ──
  const categoryStats = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const f of funds) counts[f.category] = (counts[f.category] ?? 0) + 1;
    return counts;
  }, [funds]);

  // ── Consensus + per-category breakdown ──
  const consensus = useMemo(() => {
    if (!batchLoad.data) return null;
    return computeConsensus(batchLoad.data);
  }, [batchLoad.data]);

  const [minFunds, setMinFunds] = useState(2);

  const consensusTop = useMemo(() => {
    if (!consensus) return [];
    return consensus.filter((c) => c.fundCount >= minFunds).slice(0, 40);
  }, [consensus, minFunds]);

  const byCategoryStats = useMemo(() => {
    if (!batchLoad.data) return null;
    const stats: Record<string, { funds: number; totalHoldings: number; totalValue: number; emptyFunds: number }> = {};
    for (const r of batchLoad.data) {
      const cat = r.fund.category;
      const s = stats[cat] ?? { funds: 0, totalHoldings: 0, totalValue: 0, emptyFunds: 0 };
      s.funds++;
      if (!r.data || r.data.count === 0) {
        s.emptyFunds++;
      } else {
        s.totalHoldings += r.data.count;
        s.totalValue += r.data.holdings.reduce((a, h) => a + (h.value ?? 0), 0);
      }
      stats[cat] = s;
    }
    return stats;
  }, [batchLoad.data]);

  const fundSummaries = useMemo(() => {
    if (!batchLoad.data) return [];
    return batchLoad.data
      .map((r) => ({
        fund: r.fund,
        count: r.data?.count ?? 0,
        totalValue: (r.data?.holdings ?? []).reduce((a, h) => a + (h.value ?? 0), 0),
        filingDate: r.data?.filing_date ?? null,
        error: r.error ?? null,
      }))
      .sort((a, b) => b.totalValue - a.totalValue);
  }, [batchLoad.data]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Global Smart Money</h1>
        <p className="text-text-secondary text-sm mt-1">
          Sovereign wealth funds, public pensions, and university endowments — 10+ year holders whose additions and
          removals are structural conviction signals, not quarterly noise.
        </p>
      </div>

      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          <Metric label="Tracked funds" value={fundsQ.isPending ? "…" : String(funds.length)} />
          <Metric label="Sovereign Wealth" value={String(categoryStats["Sovereign Wealth"] ?? 0)} />
          <Metric label="Public Pensions" value={String(categoryStats["Public Pension"] ?? 0)} />
          <Metric label="Endowments" value={String(categoryStats["Endowment"] ?? 0)} />
        </div>
      </div>

      {/* Funds-list fetch error (kept visible so the batch button's disabled
          state doesn't look like a silent bug) */}
      {fundsQ.isError && (
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm flex items-center justify-between gap-3">
          <span>Fund list failed to load: {(fundsQ.error as Error)?.message ?? "unknown error"}</span>
          <button
            onClick={() => fundsQ.refetch()}
            className="px-3 py-1 text-xs font-semibold rounded border border-loss/40 hover:bg-loss/10"
          >
            Retry
          </button>
        </div>
      )}

      {/* Batch load */}
      <div className="card card-compact">
        <div className="flex items-center gap-3 flex-wrap">
          <button
            onClick={() => batchLoad.mutate()}
            disabled={batchLoad.isPending || funds.length === 0}
            className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {batchLoad.isPending
              ? "Loading all funds…"
              : fundsQ.isPending
                ? "Loading fund list…"
                : "Load all funds' latest 13F"}
          </button>
          <div className="text-[11px] text-text-muted">
            {funds.length > 0
              ? `Parallel fetch of all ${funds.length} latest 13F-HR filings. ~2-4 seconds total; individual funds can be empty if SEC has nothing recent under that CIK.`
              : fundsQ.isError
                ? "Fund list failed above — retry, or refresh the page."
                : "Waiting for fund list from the API…"}
          </div>
        </div>
      </div>

      {batchLoad.isPending && (
        <div className="card text-center py-8">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-3">Fetching 13F-HR filings from SEC EDGAR…</div>
        </div>
      )}

      {batchLoad.isError && (
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm">
          Batch load failed: {(batchLoad.error as Error)?.message ?? "unknown error"}
        </div>
      )}

      {batchLoad.data && (
        <>
          {/* Category rollup */}
          {byCategoryStats && (
            <div className="card">
              <div className="text-sm font-semibold mb-3">By category</div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {(["Sovereign Wealth", "Public Pension", "Endowment"] as const).map((cat) => {
                  const s = byCategoryStats[cat];
                  if (!s) return null;
                  const color = cat === "Sovereign Wealth" ? t.accent : cat === "Public Pension" ? t.spot : t.hv60;
                  return (
                    <div key={cat} className="p-3 rounded border border-border" style={{ borderLeftWidth: 3, borderLeftColor: color }}>
                      <div className="text-xs font-bold uppercase tracking-wider" style={{ color }}>{cat}</div>
                      <div className="mt-2 flex flex-wrap gap-4 text-xs">
                        <div>
                          <div className="text-text-muted">Funds</div>
                          <div className="text-sm font-semibold">{s.funds}{s.emptyFunds > 0 && <span className="text-text-muted"> ({s.emptyFunds} empty)</span>}</div>
                        </div>
                        <div>
                          <div className="text-text-muted">Positions</div>
                          <div className="text-sm font-semibold">{s.totalHoldings.toLocaleString()}</div>
                        </div>
                        <div>
                          <div className="text-text-muted">Aggregate value</div>
                          <div className="text-sm font-semibold">{fmtBn(s.totalValue)}</div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Per-fund summary */}
          <div className="card">
            <div className="text-sm font-semibold mb-2">Per-fund summary</div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted">
                  <tr>
                    <th className="text-left py-1.5 px-2">Fund</th>
                    <th className="text-left py-1.5 px-2">Category</th>
                    <th className="text-left py-1.5 px-2">Country</th>
                    <th className="text-right py-1.5 px-2">Positions</th>
                    <th className="text-right py-1.5 px-2">Total value</th>
                    <th className="text-left py-1.5 px-2">Filing</th>
                    <th className="text-left py-1.5 px-2">Drill</th>
                  </tr>
                </thead>
                <tbody>
                  {fundSummaries.map((fs) => (
                    <tr key={fs.fund.cik} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2 font-semibold">{fs.fund.name}</td>
                      <td className="py-1 px-2 text-text-muted">{fs.fund.category}</td>
                      <td className="py-1 px-2 text-text-muted">{fs.fund.country}</td>
                      <td className="py-1 px-2 text-right">{fs.count.toLocaleString()}</td>
                      <td className="py-1 px-2 text-right">{fmtBn(fs.totalValue)}</td>
                      <td className="py-1 px-2 text-text-muted">{fs.filingDate ?? (fs.error ? <span className="text-loss">error</span> : "—")}</td>
                      <td className="py-1 px-2">
                        <button
                          onClick={() => setDrillCik(fs.fund.cik)}
                          disabled={fs.count === 0}
                          className="px-2 py-0.5 text-[10px] rounded border border-border hover:bg-accent hover:text-white hover:border-accent disabled:opacity-40"
                        >
                          View
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Consensus */}
          {consensus && (
            <div className="card">
              <div className="flex items-baseline justify-between flex-wrap gap-2 mb-2">
                <div>
                  <div className="text-sm font-semibold">Cross-fund consensus</div>
                  <div className="text-xs text-text-muted">
                    Tickers held by multiple global funds. Structural conviction names.
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-text-muted">Min funds:</span>
                  {[2, 3, 4, 5].map((n) => (
                    <button
                      key={n}
                      onClick={() => setMinFunds(n)}
                      className={`px-2 py-0.5 text-[11px] rounded ${minFunds === n ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}
                    >
                      {n}+
                    </button>
                  ))}
                </div>
              </div>
              {consensusTop.length > 0 ? (
                <>
                  <Plot
                    data={[{
                      type: "bar" as const,
                      orientation: "h" as const,
                      y: consensusTop.slice(0, 20).map((c) => c.company.slice(0, 40)),
                      x: consensusTop.slice(0, 20).map((c) => c.totalValue / 1e9),
                      marker: { color: consensusTop.slice(0, 20).map((c) => c.fundCount >= 5 ? t.gain : c.fundCount >= 3 ? t.accent : t.spot) },
                      text: consensusTop.slice(0, 20).map((c) => `${c.fundCount} funds · $${(c.totalValue / 1e9).toFixed(1)}B`),
                      textposition: "outside" as const,
                    }]}
                    layout={{
                      ...L,
                      height: CHART_HEIGHT.tall,
                      title: { text: `Top consensus names (${minFunds}+ funds)`, font: { size: 13, color: t.text } },
                      xaxis: { title: { text: "Aggregate value ($B)" }, gridcolor: t.grid },
                      yaxis: { gridcolor: t.grid, autorange: "reversed" },
                      margin: { l: 220, r: 180, t: 40, b: 40 },
                    }}
                    config={{ displayModeBar: false, responsive: true }}
                    style={{ width: "100%" }}
                  />
                  <div className="overflow-x-auto max-h-[420px] mt-3">
                    <table className="w-full text-xs font-data">
                      <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                        <tr>
                          <th className="text-left py-1.5 px-2">Company</th>
                          <th className="text-right py-1.5 px-2">Funds</th>
                          <th className="text-right py-1.5 px-2">Aggregate</th>
                          <th className="text-left py-1.5 px-2">Holders</th>
                        </tr>
                      </thead>
                      <tbody>
                        {consensusTop.map((c) => (
                          <tr key={c.cusip || c.company} className="border-b border-border/50 hover:bg-surface-alt">
                            <td className="py-1 px-2 font-semibold">{c.company}</td>
                            <td className="py-1 px-2 text-right">{c.fundCount}</td>
                            <td className="py-1 px-2 text-right">{fmtBn(c.totalValue)}</td>
                            <td className="py-1 px-2 text-text-muted text-[11px]">
                              {c.funds.slice(0, 4).join(", ")}
                              {c.funds.length > 4 ? ` + ${c.funds.length - 4} more` : ""}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : (
                <div className="text-xs text-text-muted py-4">No tickers held by {minFunds}+ global funds.</div>
              )}
            </div>
          )}

          {/* Drill-down on a specific fund */}
          {drillCik && (
            <div className="card">
              <div className="flex items-baseline justify-between mb-2">
                <div className="text-sm font-semibold">
                  Drill-down — {funds.find((f) => f.cik === drillCik)?.name ?? drillCik}
                </div>
                <button
                  onClick={() => setDrillCik("")}
                  className="text-[10px] text-text-muted hover:text-loss"
                >
                  Close
                </button>
              </div>
              {drillQ.isPending ? (
                <div className="text-xs text-text-muted py-4">Loading…</div>
              ) : drillQ.data && drillQ.data.count > 0 ? (
                <div className="overflow-x-auto max-h-[420px]">
                  <table className="w-full text-xs font-data">
                    <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                      <tr>
                        <th className="text-left py-1.5 px-2">Company</th>
                        <th className="text-right py-1.5 px-2">Value</th>
                        <th className="text-right py-1.5 px-2">Shares</th>
                        <th className="text-left py-1.5 px-2">CUSIP</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...drillQ.data.holdings]
                        .sort((a: Holding13F, b: Holding13F) => (b.value ?? 0) - (a.value ?? 0))
                        .slice(0, 50)
                        .map((h, i) => (
                          <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                            <td className="py-1 px-2 font-semibold">{h.company ?? "—"}</td>
                            <td className="py-1 px-2 text-right">{fmtBn(h.value)}</td>
                            <td className="py-1 px-2 text-right">{h.shares != null ? h.shares.toLocaleString() : "—"}</td>
                            <td className="py-1 px-2 text-text-muted">{h.cusip ?? "—"}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                  <div className="text-[11px] text-text-muted mt-2">
                    Showing top 50 of {drillQ.data.count.toLocaleString()} positions, sorted by value.
                  </div>
                </div>
              ) : (
                <div className="text-xs text-text-muted py-4">No 13F data available for this fund.</div>
              )}
            </div>
          )}

          {consensus && (
            <AIInterpretation
              page="global"
              data={{
                funds_loaded: fundSummaries.length,
                total_fund_value: fundSummaries.reduce((s, f) => s + f.totalValue, 0),
                by_category: byCategoryStats,
                largest_funds: fundSummaries.slice(0, 6).map((f) => ({
                  name: f.fund.name,
                  category: f.fund.category,
                  country: f.fund.country,
                  positions: f.count,
                  total_value: f.totalValue,
                })),
                top_consensus_3_plus_funds: consensus
                  .filter((c) => c.fundCount >= 3)
                  .slice(0, 15)
                  .map((c) => ({
                    company: c.company,
                    fund_count: c.fundCount,
                    total_value: c.totalValue,
                    holders: c.funds,
                  })),
              }}
            />
          )}
        </>
      )}

      <div className="card card-compact text-[11px] text-text-muted">
        <strong>About the data:</strong> All funds listed file Form 13F-HR with the SEC quarterly. CIKs were verified
        via SEC EDGAR full-text search. Non-US funds (Norges Bank, Temasek, GIC, ADIA) file only on their US holdings;
        their full global equity books are published separately on their own websites. CIKs and category mappings
        live in <code>src/edgar.py:GLOBAL_TRACKED_FUNDS</code>.
      </div>
    </div>
  );
}
