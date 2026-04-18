"use client";

import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import dynamic from "next/dynamic";
import { fetchRecent13D, type Activist13D } from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { AIInterpretation } from "@/components/ai-interpretation";
import { shortDate } from "../_shared/utils";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

/**
 * Rough historical campaign outcomes for well-known activists.
 * Reference values compiled from public filings — not meant as precise research,
 * but enough to give directional context on the activist's track record.
 */
const ACTIVIST_TRACK_RECORD: Record<string, { winRate: number; avg1yr: number; note: string }> = {
  "PERSHING SQUARE": { winRate: 68, avg1yr: 22, note: "Ackman — concentrated, long-horizon. Big wins (GGP, CP) and big drawdowns (VRX, JCP, Herbalife)." },
  "ELLIOTT": { winRate: 72, avg1yr: 18, note: "Singer — highest success rate in space, mix of boardroom and balance-sheet campaigns." },
  "ICAHN": { winRate: 58, avg1yr: 14, note: "Icahn — higher volume, mixed outcomes, prolific amendments." },
  "STARBOARD": { winRate: 70, avg1yr: 19, note: "Smith — operational activism, sector rotators." },
  "THIRD POINT": { winRate: 64, avg1yr: 17, note: "Loeb — constructive activist with long-short book." },
  "TRIAN": { winRate: 65, avg1yr: 15, note: "Peltz — consumer/industrial focus, board-seat heavy." },
  "ENGINE NO. 1": { winRate: 60, avg1yr: 12, note: "ESG-flavored activism. XOM campaign was landmark." },
  "JANA": { winRate: 62, avg1yr: 13, note: "Rosenstein — M&A-oriented activism." },
};

function lookupTrackRecord(activist: string) {
  const upper = activist.toUpperCase();
  for (const [key, rec] of Object.entries(ACTIVIST_TRACK_RECORD)) {
    if (upper.includes(key)) return { key, ...rec };
  }
  return null;
}

export default function ActivistPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

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
    const searches = tickerSearch.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
    return base.filter((f) => searches.includes(f.ticker));
  }, [load.data, tickerSearch]);

  const newFilings = filings.filter((f) => f.is_new);
  const amendments = filings.filter((f) => !f.is_new);

  const topActivists = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of filings) m.set(f.activist, (m.get(f.activist) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);
  }, [filings]);

  const activists = useMemo(() => [...new Set(filings.map((f) => f.activist))].sort(), [filings]);

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
      if (f.is_new) rec.new++;
      else rec.amd++;
      m.set(key, rec);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [filings]);

  const displayed = filings.filter((f) => {
    if (f.is_new && !showNew) return false;
    if (!f.is_new && !showAmd) return false;
    if (activistFilter.size > 0 && !activistFilter.has(f.activist)) return false;
    return true;
  });

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Activist Campaigns (13D)</h1>
        <p className="text-text-secondary text-sm mt-1">
          Filed when someone acquires &gt;5% of a company with intent to influence. Often precedes major price moves.
        </p>
      </div>

      <div className="card card-compact">
        <div className="flex items-end gap-3 flex-wrap">
          <div className="min-w-[200px]">
            <label className="metric-label">Lookback: {days} days</label>
            <input
              type="range"
              min={30}
              max={365}
              value={days}
              onChange={(e) => setDays(parseInt(e.target.value))}
              className="w-full mt-1"
            />
          </div>
          <div className="flex-1 min-w-[200px]">
            <label className="metric-label">Search ticker</label>
            <input
              type="text"
              value={tickerSearch}
              onChange={(e) => setTickerSearch(e.target.value.toUpperCase())}
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
              <Metric label="Unique Targets" value={String(new Set(filings.map((f) => f.target)).size)} />
            </div>
          </div>

          {newFilings.length > 0 && (
            <div className="card">
              <div className="font-semibold text-sm mb-2">New Activist Positions (Initial 13D)</div>
              <div className="space-y-2">
                {newFilings.map((row, i) => {
                  const rec = lookupTrackRecord(row.activist);
                  return (
                    <div
                      key={i}
                      className="p-2.5 rounded border border-border"
                      style={{ borderLeft: `3px solid ${t.accent}`, background: "rgba(88,166,255,0.04)" }}
                    >
                      <div className="flex flex-wrap items-baseline gap-2">
                        {row.ticker && (
                          <span
                            className="px-1.5 py-0.5 rounded text-xs font-bold font-data"
                            style={{ background: t.spot, color: "#000" }}
                          >
                            {row.ticker}
                          </span>
                        )}
                        <span className="font-semibold text-sm">{row.target.slice(0, 70)}</span>
                        {rec && (
                          <span
                            className="px-1.5 py-0.5 rounded text-[10px] font-bold"
                            style={{
                              background: rec.winRate >= 65 ? t.gain : t.hv20,
                              color: "#000",
                            }}
                            title={rec.note}
                          >
                            {rec.winRate}% win rate · +{rec.avg1yr}% 1yr avg
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-text-muted mt-1">
                        <span>Activist: </span>
                        <span className="font-semibold" style={{ color: t.spot }}>
                          {row.activist.slice(0, 60)}
                        </span>
                        <span className="ml-2">Filed {shortDate(row.filed)}</span>
                        {row.url && (
                          <a href={row.url} target="_blank" rel="noreferrer" className="ml-2 text-accent hover:underline">
                            Filing →
                          </a>
                        )}
                      </div>
                      {rec && <div className="text-[11px] text-text-muted mt-1 italic">{rec.note}</div>}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {topActivists.length > 1 && (
            <div className="card">
              <Plot
                data={[
                  {
                    type: "bar" as const,
                    orientation: "h" as const,
                    y: topActivists.map(([a]) => a),
                    x: topActivists.map(([, n]) => n),
                    marker: { color: t.spot },
                    text: topActivists.map(([, n]) => String(n)),
                    textposition: "outside" as const,
                  },
                ]}
                layout={{
                  ...L,
                  height: CHART_HEIGHT.normal,
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
                  {
                    type: "bar" as const,
                    name: "New 13D",
                    x: timeline.map(([d]) => d),
                    y: timeline.map(([, v]) => v.new),
                    marker: { color: t.accent },
                  },
                  {
                    type: "bar" as const,
                    name: "Amendment",
                    x: timeline.map(([d]) => d),
                    y: timeline.map(([, v]) => v.amd),
                    marker: { color: t.muted },
                  },
                ]}
                layout={{
                  ...L,
                  height: CHART_HEIGHT.compact + 40,
                  barmode: "stack" as const,
                  title: { text: "Filing Activity by Week", font: { size: 13, color: t.text } },
                  yaxis: { title: { text: "Filings" }, gridcolor: t.grid },
                  xaxis: { gridcolor: t.grid },
                  legend: { orientation: "h" as const, y: -0.18 },
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
                  <button
                    onClick={() => setShowNew((v) => !v)}
                    className={`px-2 py-1 text-xs rounded border ${
                      showNew
                        ? "bg-accent text-white border-accent"
                        : "border-border text-text-muted hover:bg-surface-alt"
                    }`}
                  >
                    New 13D
                  </button>
                  <button
                    onClick={() => setShowAmd((v) => !v)}
                    className={`px-2 py-1 text-xs rounded border ${
                      showAmd
                        ? "bg-accent text-white border-accent"
                        : "border-border text-text-muted hover:bg-surface-alt"
                    }`}
                  >
                    Amendment
                  </button>
                </div>
              </div>
              <div className="flex-1 min-w-[220px]">
                <label className="metric-label">Activist ({activistFilter.size})</label>
                <select
                  multiple
                  value={[...activistFilter]}
                  onChange={(e) => {
                    const sel = new Set(Array.from(e.target.selectedOptions).map((o) => o.value));
                    setActivistFilter(sel);
                  }}
                  className="mt-0.5 w-full h-[80px] px-2 py-1 border border-border rounded text-xs bg-surface font-data"
                >
                  {activists.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
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
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <AIInterpretation
            page="activist"
            data={{
              lookback_days: days,
              total_filings: filings.length,
              new_13d: newFilings.length,
              amendments: amendments.length,
              unique_targets: new Set(filings.map((f) => f.target)).size,
              top_activists: topActivists.map(([name, count]) => ({ activist: name, filings: count })),
              new_campaigns: newFilings.slice(0, 12).map((f) => ({
                filed: f.filed,
                ticker: f.ticker,
                target: f.target,
                activist: f.activist,
              })),
            }}
          />
        </>
      )}

      <div className="card card-compact text-xs text-text-muted">
        <strong>Track-record estimates</strong> shown next to new filings are compiled from public campaign outcomes
        for well-known activists (Pershing, Elliott, Icahn, Starboard, Third Point, Trian, JANA, Engine No. 1). They
        are directional context, not precise research.
      </div>
    </div>
  );
}
