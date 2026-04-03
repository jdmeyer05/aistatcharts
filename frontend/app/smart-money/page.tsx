"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchTrackedFunds, fetch13FHoldings, fetchCongressionalTrades, fetchRecent13D, fetch8KEvents } from "@/lib/api";
import { getChartTheme } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";

const TABS = ["13F Holdings", "Congressional Trades", "Activist Investors", "8-K Events"];

export default function SmartMoneyPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const [activeTab, setActiveTab] = useState(0);
  const [selectedFund, setSelectedFund] = useState("");
  const [searchTicker, setSearchTicker] = useState("AAPL");

  const { data: funds } = useQuery({ queryKey: ["tracked-funds"], queryFn: fetchTrackedFunds, staleTime: 60 * 60 * 1000 });
  const { data: congress } = useQuery({ queryKey: ["congressional-trades"], queryFn: fetchCongressionalTrades, staleTime: 30 * 60 * 1000, enabled: activeTab === 1 });
  const { data: activists } = useQuery({ queryKey: ["recent-13d"], queryFn: fetchRecent13D, staleTime: 30 * 60 * 1000, enabled: activeTab === 2 });

  const load13F = useMutation({ mutationFn: (cik: string) => fetch13FHoldings(cik) });
  const load8K = useMutation({ mutationFn: (tk: string) => fetch8KEvents(tk) });

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Smart Money Tracker</h1>
        <p className="text-text-secondary text-sm mt-1">Institutional holdings, congressional trades, activist investors, 8-K events — all from public data.</p>
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

      {/* Tab 0: 13F */}
      {activeTab === 0 && (
        <div className="card space-y-4">
          <div className="flex items-center gap-3">
            <select value={selectedFund} onChange={e => setSelectedFund(e.target.value)}
              className="px-3 py-1.5 border border-border rounded text-sm bg-surface min-w-[200px]">
              <option value="">Select Fund...</option>
              {funds?.funds.map(f => <option key={f.cik} value={f.cik}>{f.name}</option>)}
            </select>
            <button onClick={() => selectedFund && load13F.mutate(selectedFund)} disabled={!selectedFund || load13F.isPending}
              className="px-4 py-1.5 bg-accent text-white text-xs font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
              {load13F.isPending ? "Loading..." : "Load Holdings"}
            </button>
          </div>
          {load13F.data && load13F.data.holdings.length > 0 ? (
            <>
              <Metric label="Holdings" value={String(load13F.data.count)} />
              <div className="overflow-x-auto">
                <table className="data-table text-xs">
                  <thead><tr><th>Company</th><th>CUSIP</th><th>Value ($K)</th><th>Shares</th><th>Type</th></tr></thead>
                  <tbody>
                    {load13F.data.holdings.slice(0, 50).map((h, i) => (
                      <tr key={i}>
                        <td className="font-semibold">{h.nameOfIssuer as string ?? "—"}</td>
                        <td className="font-data">{h.cusip as string ?? "—"}</td>
                        <td className="font-data">${((h.value as number) ?? 0).toLocaleString()}</td>
                        <td className="font-data">{((h.shrsOrPrnAmt as number) ?? 0).toLocaleString()}</td>
                        <td>{(h.putCall as string) || "Equity"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : load13F.isSuccess ? <p className="text-sm text-text-muted">No holdings found.</p> : null}
        </div>
      )}

      {/* Tab 1: Congressional Trades */}
      {activeTab === 1 && (
        <div className="card space-y-4">
          {congress && congress.data.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Member</th><th>Ticker</th><th>Type</th><th>Amount</th><th>Date</th></tr></thead>
                <tbody>
                  {congress.data.slice(0, 50).map((tr, i) => (
                    <tr key={i}>
                      <td className="font-semibold">{tr.member as string ?? "—"}</td>
                      <td className="font-data">{tr.ticker as string ?? "—"}</td>
                      <td><span className={`badge ${(tr.type as string)?.toLowerCase().includes("purchase") ? "badge-gain" : "badge-loss"}`}>{tr.type as string}</span></td>
                      <td className="font-data">{tr.amount as string ?? "—"}</td>
                      <td>{tr.date as string ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="text-sm text-text-muted">{congress ? "No recent trades found." : "Loading..."}</p>}
        </div>
      )}

      {/* Tab 2: Activist Investors */}
      {activeTab === 2 && (
        <div className="card space-y-4">
          {activists && activists.data.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Filer</th><th>Subject</th><th>Filed</th><th>Type</th></tr></thead>
                <tbody>
                  {activists.data.slice(0, 30).map((f, i) => (
                    <tr key={i}>
                      <td className="font-semibold">{f.filer as string ?? "—"}</td>
                      <td>{f.subject as string ?? "—"}</td>
                      <td>{f.filed as string ?? "—"}</td>
                      <td className="font-data">{f.form_type as string ?? "13D"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="text-sm text-text-muted">{activists ? "No recent 13D filings." : "Loading..."}</p>}
        </div>
      )}

      {/* Tab 3: 8-K Events */}
      {activeTab === 3 && (
        <div className="card space-y-4">
          <div className="flex items-center gap-3">
            <input type="text" value={searchTicker} onChange={e => setSearchTicker(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === "Enter" && load8K.mutate(searchTicker)}
              placeholder="AAPL" className="w-32 px-3 py-1.5 border border-border rounded text-sm font-data bg-surface" />
            <button onClick={() => load8K.mutate(searchTicker)} disabled={load8K.isPending}
              className="px-4 py-1.5 bg-accent text-white text-xs font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
              {load8K.isPending ? "Searching..." : "Search 8-K"}
            </button>
          </div>
          {load8K.data && load8K.data.data.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Date</th><th>Form</th><th>Description</th></tr></thead>
                <tbody>
                  {load8K.data.data.slice(0, 30).map((f, i) => (
                    <tr key={i}>
                      <td>{f.filed as string ?? "—"}</td>
                      <td className="font-data">{f.form as string ?? "8-K"}</td>
                      <td className="text-xs">{f.description as string ?? (f.items as string) ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : load8K.isSuccess ? <p className="text-sm text-text-muted">No 8-K filings found.</p> : null}
        </div>
      )}
    </div>
  );
}
