"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import dynamic from "next/dynamic";
import { fetchTrackedFunds, fetch13FHoldings } from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { AIInterpretation } from "@/components/ai-interpretation";
import { fmtBn } from "../_shared/utils";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

export default function Institutional13FPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const { data: funds } = useQuery({
    queryKey: ["tracked-funds"],
    queryFn: fetchTrackedFunds,
    staleTime: Infinity,
  });
  const [fund, setFund] = useState<string>("");
  const load = useMutation({ mutationFn: (cik: string) => fetch13FHoldings(cik) });

  const fundName = funds?.funds.find((f) => f.cik === fund)?.name ?? "";
  const top15 = useMemo(() => {
    if (!load.data) return [];
    return [...load.data.holdings]
      .filter((h) => (h.value ?? 0) > 0 && h.company && h.company.trim() !== "")
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
      .slice(0, 15);
  }, [load.data]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Institutional Holdings (13F)</h1>
        <p className="text-text-secondary text-sm mt-1">
          Quarterly filings from institutional funds with &gt;$100M AUM. Data from SEC EDGAR.
        </p>
      </div>

      <div className="card card-compact">
        <div className="flex items-center gap-3 flex-wrap">
          <select
            value={fund}
            onChange={(e) => setFund(e.target.value)}
            className="px-3 py-2 border border-border rounded-lg text-sm bg-surface min-w-[240px]"
          >
            <option value="">Select fund…</option>
            {funds?.funds.map((f) => (
              <option key={f.cik} value={f.cik}>
                {f.name}
              </option>
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
                data={[
                  {
                    type: "bar" as const,
                    orientation: "h" as const,
                    y: top15.map((h) => h.company ?? ""),
                    x: top15.map((h) => (h.value ?? 0) / 1e6),
                    marker: { color: t.accent },
                    text: top15.map(
                      (h) =>
                        `$${((h.value ?? 0) / 1e6).toLocaleString(undefined, { maximumFractionDigits: 0 })}M`,
                    ),
                    textposition: "outside" as const,
                    hovertemplate: "%{y}<br>$%{x:,.0f}M<extra></extra>",
                  },
                ]}
                layout={{
                  ...L,
                  height: CHART_HEIGHT.tall,
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

          {load.data.count > 0 && (
            <AIInterpretation
              page="13f"
              subject={fundName || fund}
              data={{
                fund: fundName,
                filing_date: load.data.filing_date,
                total_positions: load.data.count,
                top_15_by_value: top15.map((h) => ({
                  company: h.company,
                  value: h.value,
                  shares: h.shares,
                  type: h.put_call ?? "Equity",
                })),
                total_portfolio_value: load.data.holdings.reduce((s, h) => s + (h.value ?? 0), 0),
                top_15_concentration: (() => {
                  const top = top15.reduce((s, h) => s + (h.value ?? 0), 0);
                  const all = load.data.holdings.reduce((s, h) => s + (h.value ?? 0), 0);
                  return all > 0 ? top / all : 0;
                })(),
              }}
            />
          )}
        </>
      )}

      <div className="card card-compact text-xs text-text-muted">
        <strong>Next:</strong> per-fund follow-the-leader portfolios, quarter-over-quarter position deltas, and
        cross-fund consensus picks coming soon.
      </div>
    </div>
  );
}
