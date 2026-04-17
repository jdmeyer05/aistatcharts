"use client";

import { useState, useMemo, useEffect } from "react";
import { useQuery, useQueries } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import {
  fetchErcotCapacity,
  fetchErcotCapacityMonths,
  type ErcotCapacityProject,
  type ErcotCapacityMonth,
} from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = [
  "By Fuel Type",
  "Timeline / COD",
  "Project Details",
  "By County",
  "Financial Security",
  "Month-over-Month",
];

const FUEL_COLORS: Record<string, string> = {
  Wind: "#00d1ff",
  Solar: "#ffdd00",
  Battery: "#ad7fff",
  Gas: "#ff9900",
};

const FUEL_ORDER = ["Wind", "Solar", "Battery", "Gas"];

function colorOf(fuel: string, fallback: string): string {
  return FUEL_COLORS[fuel] ?? fallback;
}

function fmtMW(v: number): string {
  return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function fmtMWSigned(v: number): string {
  return (v >= 0 ? "+" : "") + fmtMW(v);
}

export default function ErcotCapacityPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const [activeTab, setActiveTab] = useState(0);
  const [selectedMonth, setSelectedMonth] = useState<ErcotCapacityMonth | null>(null);
  const [plannedOnly, setPlannedOnly] = useState(false);

  // Project Details filters
  const [fuelFilter, setFuelFilter] = useState<string[]>([]);
  const [yearFilter, setYearFilter] = useState<number[]>([]);
  const [fsFilter, setFsFilter] = useState<string[]>(["Yes", "No"]);

  // Month-over-Month: load all-month data lazily when the tab first opens
  const [momEnabled, setMomEnabled] = useState(false);
  useEffect(() => { if (activeTab === 5) setMomEnabled(true); }, [activeTab]);

  // MoM project-diff selectors
  const [compareFrom, setCompareFrom] = useState<string>("");
  const [compareTo, setCompareTo] = useState<string>("");

  const monthsQ = useQuery({
    queryKey: ["ercot-capacity-months"],
    queryFn: fetchErcotCapacityMonths,
    staleTime: 24 * 60 * 60 * 1000,
  });
  const months = monthsQ.data?.months ?? [];

  // Default selected month = first available
  useEffect(() => {
    if (!selectedMonth && months.length > 0) setSelectedMonth(months[0]);
  }, [months, selectedMonth]);

  const dataQ = useQuery({
    queryKey: ["ercot-capacity", selectedMonth?.month_label, selectedMonth?.date_path, plannedOnly],
    queryFn: () => fetchErcotCapacity(selectedMonth!.month_label, selectedMonth!.date_path, plannedOnly),
    enabled: !!selectedMonth,
    staleTime: 60 * 60 * 1000,
  });
  const projects: ErcotCapacityProject[] = dataQ.data?.projects ?? [];

  // Reset filters when data changes
  useEffect(() => {
    if (projects.length === 0) return;
    const fuels = Array.from(new Set(projects.map(p => p.fuel_type)));
    const years = Array.from(new Set(projects.map(p => p.year).filter((y): y is number => y !== null)));
    setFuelFilter(fuels);
    setYearFilter(years.sort((a, b) => a - b));
    setFsFilter(["Yes", "No"]);
  }, [projects]);

  // ── Aggregates ──────────────────────────────────────────────────────────
  const agg = useMemo(() => {
    const totalMW = projects.reduce((s, p) => s + p.capacity_mw, 0);
    const byFuel: Record<string, number> = {};
    const countByFuel: Record<string, number> = {};
    const byDetail: Record<string, { mw: number; n: number }> = {};
    for (const p of projects) {
      byFuel[p.fuel_type] = (byFuel[p.fuel_type] ?? 0) + p.capacity_mw;
      countByFuel[p.fuel_type] = (countByFuel[p.fuel_type] ?? 0) + 1;
      const d = byDetail[p.fuel_detail] ?? { mw: 0, n: 0 };
      d.mw += p.capacity_mw;
      d.n += 1;
      byDetail[p.fuel_detail] = d;
    }
    const byFuelSorted = Object.entries(byFuel).sort((a, b) => b[1] - a[1]);
    return { totalMW, byFuel, byFuelSorted, countByFuel, byDetail };
  }, [projects]);

  // Tab 2 filtered project list
  const filtered = useMemo(() => {
    return projects
      .filter(p => fuelFilter.includes(p.fuel_type))
      .filter(p => p.year === null || yearFilter.includes(p.year))
      .filter(p => fsFilter.includes(p.financial_security))
      .sort((a, b) => {
        const ad = a.projected_cod ?? "9999";
        const bd = b.projected_cod ?? "9999";
        return ad.localeCompare(bd);
      });
  }, [projects, fuelFilter, yearFilter, fsFilter]);

  const availableYears = useMemo(() => {
    return Array.from(new Set(projects.map(p => p.year).filter((y): y is number => y !== null))).sort((a, b) => a - b);
  }, [projects]);

  // Tab 5 (MoM) — lazy fetch the rest of the months
  const momQueries = useQueries({
    queries: months.map(m => ({
      queryKey: ["ercot-capacity", m.month_label, m.date_path, false],
      queryFn: () => fetchErcotCapacity(m.month_label, m.date_path, false),
      enabled: momEnabled && !!m,
      staleTime: 60 * 60 * 1000,
    })),
  });
  const momLoaded = momQueries.filter(q => q.data).length;
  const momReady = momEnabled && momLoaded >= 2;

  // Build monthly totals (oldest → newest for trend)
  const momData = useMemo(() => {
    if (!momReady) return null;
    const rows: Array<{ month: string; total: number; projects: number } & Record<string, number>> = [];
    months.forEach((m, i) => {
      const q = momQueries[i];
      if (!q.data) return;
      const projs = q.data.projects;
      const row = { month: m.month_label, total: 0, projects: projs.length } as { month: string; total: number; projects: number } & Record<string, number>;
      FUEL_ORDER.forEach(f => { row[f] = 0; });
      for (const p of projs) {
        row.total += p.capacity_mw;
        row[p.fuel_type] = (row[p.fuel_type] ?? 0) + p.capacity_mw;
      }
      rows.push(row);
    });
    return rows.reverse(); // oldest first
  }, [momQueries, months, momReady]);

  // Default compareFrom/To once MoM data is ready
  useEffect(() => {
    if (!momReady || !momData || momData.length < 2) return;
    if (!compareTo) setCompareTo(momData[momData.length - 1].month);
    if (!compareFrom) setCompareFrom(momData[momData.length - 2].month);
  }, [momReady, momData, compareFrom, compareTo]);

  const monthProjectMap = useMemo(() => {
    const map: Record<string, ErcotCapacityProject[]> = {};
    months.forEach((m, i) => {
      const q = momQueries[i];
      if (q.data) map[m.month_label] = q.data.projects;
    });
    return map;
  }, [months, momQueries]);

  const diff = useMemo(() => {
    if (!compareFrom || !compareTo || compareFrom === compareTo) return null;
    const from = monthProjectMap[compareFrom];
    const to = monthProjectMap[compareTo];
    if (!from || !to) return null;
    const fromByInr = new Map(from.filter(p => p.inr).map(p => [p.inr, p] as const));
    const toByInr = new Map(to.filter(p => p.inr).map(p => [p.inr, p] as const));
    const fromInrs = new Set(fromByInr.keys());
    const toInrs = new Set(toByInr.keys());
    const added = to.filter(p => p.inr && !fromInrs.has(p.inr));
    const removed = from.filter(p => p.inr && !toInrs.has(p.inr));
    const codChanges: Array<{ project: string; fuel: string; mw: number; old_cod: string; new_cod: string }> = [];
    const capChanges: Array<{ project: string; fuel: string; old_mw: number; new_mw: number; change: number }> = [];
    for (const inr of toInrs) {
      if (!fromInrs.has(inr)) continue;
      const f = fromByInr.get(inr)!;
      const n = toByInr.get(inr)!;
      if ((f.projected_cod ?? "") !== (n.projected_cod ?? "")) {
        codChanges.push({
          project: n.project_name, fuel: n.fuel_type, mw: n.capacity_mw,
          old_cod: f.projected_cod ?? "?", new_cod: n.projected_cod ?? "?",
        });
      }
      if (Math.abs(f.capacity_mw - n.capacity_mw) > 0.01) {
        capChanges.push({
          project: n.project_name, fuel: n.fuel_type,
          old_mw: f.capacity_mw, new_mw: n.capacity_mw,
          change: n.capacity_mw - f.capacity_mw,
        });
      }
    }
    return { added, removed, codChanges, capChanges };
  }, [compareFrom, compareTo, monthProjectMap]);

  // ── Render ─────────────────────────────────────────────────────────────
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">ERCOT Capacity Pipeline</h1>
        <p className="text-text-secondary text-sm mt-1">
          Planned generation additions by fuel type from ERCOT&apos;s Interconnection Resource queue.
        </p>
      </div>

      {/* Controls */}
      <div className="card card-compact">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="metric-label">Report Month</label>
            <select
              value={selectedMonth?.month_label ?? ""}
              onChange={e => {
                const m = months.find(x => x.month_label === e.target.value);
                if (m) setSelectedMonth(m);
              }}
              className="mt-0.5 px-3 py-1.5 border border-border rounded text-sm bg-surface min-w-[180px]"
              disabled={months.length === 0}
            >
              {months.map(m => (
                <option key={`${m.date_path}-${m.month_label}`} value={m.month_label}>
                  {m.month_label.replace("_", " ")}
                </option>
              ))}
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm pb-1.5 cursor-pointer select-none">
            <input type="checkbox" checked={plannedOnly} onChange={e => setPlannedOnly(e.target.checked)} className="accent-accent" />
            <span>Planned Only (Financial Security Posted)</span>
          </label>
          {monthsQ.isLoading && <span className="text-xs text-text-muted">Discovering reports…</span>}
          {dataQ.isLoading && <span className="text-xs text-text-muted">Loading projects…</span>}
          {dataQ.isError && <span className="text-xs text-loss">Failed to load data</span>}
        </div>
      </div>

      {/* Pipeline Summary Metrics */}
      {projects.length > 0 && (
        <div className="card card-compact">
          <div className="flex flex-wrap gap-6">
            <Metric label="Total Pipeline" value={`${fmtMW(agg.totalMW)} MW`} />
            {agg.byFuelSorted.slice(0, 4).map(([fuel, mw]) => (
              <div key={fuel}>
                <Metric
                  label={fuel}
                  value={`${fmtMW(mw)} MW`}
                  delta={`${((mw / agg.totalMW) * 100).toFixed(0)}%`}
                  deltaType="neutral"
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {projects.length === 0 && !dataQ.isLoading && selectedMonth && (
        <div className="card text-center py-10 text-sm text-text-muted">
          No capacity data available for {selectedMonth.month_label.replace("_", " ")}
          {plannedOnly ? " (planned-only)" : ""}.
        </div>
      )}

      {projects.length > 0 && (
        <>
          {/* Tab bar */}
          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
            {TABS.map((tab, i) => (
              <button key={tab} onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {tab}
              </button>
            ))}
          </div>

          {/* ── Tab 0: By Fuel Type ── */}
          {activeTab === 0 && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="card">
                <div className="text-sm font-semibold mb-2">Capacity by Fuel Type</div>
                <Plot
                  data={[{
                    type: "bar" as const,
                    x: agg.byFuelSorted.map(([f]) => f),
                    y: agg.byFuelSorted.map(([, v]) => v),
                    marker: { color: agg.byFuelSorted.map(([f]) => colorOf(f, t.muted)) },
                    text: agg.byFuelSorted.map(([, v]) => `${fmtMW(v)} MW`),
                    textposition: "outside" as const, textfont: { size: 10, color: t.text },
                  }]}
                  layout={{ height: 380, ...L, yaxis: { title: "Megawatts (MW)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                />
              </div>
              <div className="card">
                <div className="text-sm font-semibold mb-2">Fuel Mix Share</div>
                <Plot
                  data={[{
                    type: "pie" as const, hole: 0.4,
                    labels: agg.byFuelSorted.map(([f]) => f),
                    values: agg.byFuelSorted.map(([, v]) => v),
                    marker: { colors: agg.byFuelSorted.map(([f]) => colorOf(f, t.muted)) },
                    textinfo: "label+percent", textfont: { size: 10 },
                  }]}
                  layout={{ height: 380, paper_bgcolor: "transparent", font: { color: t.text, size: 10 }, margin: { l: 0, r: 0, t: 10, b: 10 }, showlegend: false }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                />
              </div>
              <div className="card lg:col-span-2">
                <div className="text-sm font-semibold mb-2">Detailed Technology Breakdown</div>
                <div className="overflow-x-auto">
                  <table className="data-table text-xs">
                    <thead><tr><th>Technology</th><th className="text-right">Total MW</th><th className="text-right"># Projects</th><th className="text-right">Avg Size (MW)</th></tr></thead>
                    <tbody>
                      {Object.entries(agg.byDetail).sort((a, b) => b[1].mw - a[1].mw).map(([detail, info]) => (
                        <tr key={detail}>
                          <td className="font-semibold">{detail}</td>
                          <td className="font-data text-right">{fmtMW(info.mw)}</td>
                          <td className="font-data text-right">{info.n}</td>
                          <td className="font-data text-right">{fmtMW(info.mw / info.n)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div className="card lg:col-span-2">
                <div className="text-sm font-semibold mb-2">Number of Projects by Fuel Type</div>
                <Plot
                  data={[{
                    type: "bar" as const,
                    x: Object.entries(agg.countByFuel).sort((a, b) => b[1] - a[1]).map(([f]) => f),
                    y: Object.entries(agg.countByFuel).sort((a, b) => b[1] - a[1]).map(([, n]) => n),
                    marker: { color: Object.entries(agg.countByFuel).sort((a, b) => b[1] - a[1]).map(([f]) => colorOf(f, t.muted)) },
                    text: Object.entries(agg.countByFuel).sort((a, b) => b[1] - a[1]).map(([, n]) => String(n)),
                    textposition: "outside" as const, textfont: { size: 10, color: t.text },
                  }]}
                  layout={{ height: 320, ...L, yaxis: { title: "Number of Projects", gridcolor: t.grid } }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                />
              </div>
            </div>
          )}

          {/* ── Tab 1: Timeline / COD ── */}
          {activeTab === 1 && (() => {
            // Stacked by year per fuel
            const yearsSet = new Set<number>();
            for (const p of projects) if (p.year !== null) yearsSet.add(p.year);
            const years = Array.from(yearsSet).sort((a, b) => a - b);
            const byYearFuel: Record<string, Record<number, number>> = {};
            for (const f of FUEL_ORDER) byYearFuel[f] = {};
            for (const p of projects) {
              if (p.year === null) continue;
              const bucket = byYearFuel[p.fuel_type] ?? (byYearFuel[p.fuel_type] = {});
              bucket[p.year] = (bucket[p.year] ?? 0) + p.capacity_mw;
            }

            // Monthly (next 24mo)
            const now = new Date();
            const horizon = new Date(now); horizon.setDate(horizon.getDate() + 730);
            const monthBuckets: Record<string, Record<string, number>> = {}; // ym → fuel → mw
            for (const p of projects) {
              if (!p.projected_cod) continue;
              const cod = new Date(p.projected_cod);
              if (cod > horizon) continue;
              const ym = `${cod.getFullYear()}-${String(cod.getMonth() + 1).padStart(2, "0")}`;
              const row = monthBuckets[ym] ?? (monthBuckets[ym] = {});
              row[p.fuel_type] = (row[p.fuel_type] ?? 0) + p.capacity_mw;
            }
            const monthKeys = Object.keys(monthBuckets).sort();

            // Cumulative additions by fuel
            const sortedByCod = [...projects]
              .filter(p => p.projected_cod)
              .sort((a, b) => (a.projected_cod ?? "").localeCompare(b.projected_cod ?? ""));
            const cumByFuel: Record<string, { x: string[]; y: number[] }> = {};
            const runningTotals: Record<string, number> = {};
            for (const p of sortedByCod) {
              const key = p.fuel_type;
              const slot = cumByFuel[key] ?? (cumByFuel[key] = { x: [], y: [] });
              runningTotals[key] = (runningTotals[key] ?? 0) + p.capacity_mw;
              slot.x.push(p.projected_cod!);
              slot.y.push(runningTotals[key]);
            }

            const todayIso = new Date().toISOString().slice(0, 10);

            return (
              <div className="space-y-4">
                <div className="card">
                  <div className="text-sm font-semibold mb-2">Planned Capacity Additions by Year</div>
                  <Plot
                    data={FUEL_ORDER.filter(f => byYearFuel[f]).map(f => ({
                      type: "bar" as const, name: f,
                      x: years.map(y => String(y)),
                      y: years.map(y => byYearFuel[f][y] ?? 0),
                      marker: { color: colorOf(f, t.muted) },
                    }))}
                    layout={{ height: 420, ...L, barmode: "stack", yaxis: { title: "Megawatts (MW)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                  />
                </div>

                <div className="card">
                  <div className="text-sm font-semibold mb-2">Monthly COD Schedule (Next 24 Months)</div>
                  {monthKeys.length === 0 ? (
                    <div className="text-xs text-text-muted py-6 text-center">No projects with COD in the next 24 months.</div>
                  ) : (
                    <Plot
                      data={FUEL_ORDER.map(f => ({
                        type: "bar" as const, name: f,
                        x: monthKeys, y: monthKeys.map(k => monthBuckets[k][f] ?? 0),
                        marker: { color: colorOf(f, t.muted) },
                      }))}
                      layout={{
                        height: 420, ...L, barmode: "stack",
                        yaxis: { title: "Megawatts (MW)", gridcolor: t.grid },
                        xaxis: { gridcolor: t.grid },
                        shapes: [{ type: "line", x0: todayIso, x1: todayIso, yref: "paper", y0: 0, y1: 1, line: { color: t.text, width: 1, dash: "dot" } }],
                        hovermode: "x unified",
                      }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                    />
                  )}
                </div>

                <div className="card">
                  <div className="text-sm font-semibold mb-2">Cumulative Planned Additions</div>
                  <Plot
                    data={Object.entries(cumByFuel).map(([f, s]) => ({
                      type: "scatter" as const, mode: "lines" as const, name: f,
                      x: s.x, y: s.y,
                      line: { color: colorOf(f, t.muted), width: 2 },
                    }))}
                    layout={{
                      height: 380, ...L,
                      yaxis: { title: "Cumulative MW", gridcolor: t.grid },
                      xaxis: { gridcolor: t.grid },
                      shapes: [{ type: "line", x0: todayIso, x1: todayIso, yref: "paper", y0: 0, y1: 1, line: { color: t.text, width: 1, dash: "dot" } }],
                      hovermode: "x unified",
                    }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                  />
                </div>
              </div>
            );
          })()}

          {/* ── Tab 2: Project Details ── */}
          {activeTab === 2 && (() => {
            const totalFilteredMW = filtered.reduce((s, p) => s + p.capacity_mw, 0);
            const top20 = [...filtered].sort((a, b) => b.capacity_mw - a.capacity_mw).slice(0, 20);
            const allFuels = Array.from(new Set(projects.map(p => p.fuel_type)));
            return (
              <div className="space-y-4">
                <div className="card">
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div>
                      <div className="metric-label mb-1">Fuel Type</div>
                      <div className="flex flex-wrap gap-1">
                        {allFuels.map(f => (
                          <button key={f}
                            onClick={() => setFuelFilter(prev => prev.includes(f) ? prev.filter(x => x !== f) : [...prev, f])}
                            className={`px-2 py-1 text-xs rounded ${fuelFilter.includes(f) ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}
                            style={fuelFilter.includes(f) ? { backgroundColor: colorOf(f, t.muted) } : undefined}
                          >{f}</button>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className="metric-label mb-1">COD Year</div>
                      <div className="flex flex-wrap gap-1 max-h-20 overflow-y-auto">
                        {availableYears.map(y => (
                          <button key={y}
                            onClick={() => setYearFilter(prev => prev.includes(y) ? prev.filter(x => x !== y) : [...prev, y])}
                            className={`px-2 py-1 text-xs rounded ${yearFilter.includes(y) ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}
                          >{y}</button>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className="metric-label mb-1">Financial Security</div>
                      <div className="flex gap-1">
                        {["Yes", "No"].map(v => (
                          <button key={v}
                            onClick={() => setFsFilter(prev => prev.includes(v) ? prev.filter(x => x !== v) : [...prev, v])}
                            className={`px-2 py-1 text-xs rounded ${fsFilter.includes(v) ? (v === "Yes" ? "bg-gain text-bg" : "bg-loss text-bg") : "bg-surface-alt text-text-muted"}`}
                          >{v}</button>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div className="mt-3 text-sm">
                    <span className="font-semibold">{filtered.length}</span> projects,
                    <span className="font-semibold font-data"> {fmtMW(totalFilteredMW)}</span> MW
                  </div>
                </div>

                <div className="card">
                  <div className="text-sm font-semibold mb-2">Projects ({filtered.length})</div>
                  <div className="overflow-x-auto max-h-[500px] overflow-y-auto">
                    <table className="data-table text-xs">
                      <thead className="sticky top-0 bg-surface">
                        <tr><th>Project</th><th>Fuel</th><th>Technology</th><th className="text-right">MW</th><th>Projected COD</th><th>County</th><th>Fin. Sec.</th></tr>
                      </thead>
                      <tbody>
                        {filtered.map((p, i) => (
                          <tr key={`${p.inr}-${i}`}>
                            <td>{p.project_name}</td>
                            <td><span className="font-semibold" style={{ color: colorOf(p.fuel_type, t.text) }}>{p.fuel_type}</span></td>
                            <td>{p.fuel_detail}</td>
                            <td className="font-data text-right">{p.capacity_mw.toFixed(1)}</td>
                            <td className="font-data">{p.projected_cod ?? "—"}</td>
                            <td>{p.county}</td>
                            <td className={p.financial_security === "Yes" ? "text-gain font-semibold" : "text-loss"}>{p.financial_security || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="card">
                  <div className="text-sm font-semibold mb-2">Top 20 Largest Projects</div>
                  <Plot
                    data={[{
                      type: "bar" as const, orientation: "h" as const,
                      x: top20.map(p => p.capacity_mw),
                      y: top20.map(p => p.project_name),
                      marker: { color: top20.map(p => colorOf(p.fuel_type, t.muted)) },
                      text: top20.map(p => `${fmtMW(p.capacity_mw)} MW`),
                      textposition: "outside" as const, textfont: { size: 10, color: t.text },
                    }]}
                    layout={{
                      height: Math.max(400, top20.length * 30), ...L,
                      margin: { l: 220, r: 80, t: 10, b: 40 },
                      xaxis: { title: "Capacity (MW)", gridcolor: t.grid },
                      yaxis: { autorange: "reversed", gridcolor: t.grid },
                    }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                  />
                </div>
              </div>
            );
          })()}

          {/* ── Tab 3: By County ── */}
          {activeTab === 3 && (() => {
            const byCounty: Record<string, { mw: number; n: number }> = {};
            for (const p of projects) {
              if (!p.county) continue;
              const row = byCounty[p.county] ?? { mw: 0, n: 0 };
              row.mw += p.capacity_mw; row.n += 1;
              byCounty[p.county] = row;
            }
            const top20 = Object.entries(byCounty).sort((a, b) => b[1].mw - a[1].mw).slice(0, 20);
            const top15 = top20.slice(0, 15).map(([c]) => c);
            const countyFuelMix: Record<string, Record<string, number>> = {};
            for (const c of top15) countyFuelMix[c] = Object.fromEntries(FUEL_ORDER.map(f => [f, 0]));
            for (const p of projects) {
              if (countyFuelMix[p.county]) {
                countyFuelMix[p.county][p.fuel_type] = (countyFuelMix[p.county][p.fuel_type] ?? 0) + p.capacity_mw;
              }
            }
            return (
              <div className="space-y-4">
                <div className="card">
                  <div className="text-sm font-semibold mb-2">Top 20 Counties by Capacity</div>
                  <Plot
                    data={[{
                      type: "bar" as const, orientation: "h" as const,
                      y: top20.map(([c]) => c),
                      x: top20.map(([, v]) => v.mw),
                      marker: { color: "#00d1ff" },
                      text: top20.map(([, v]) => `${fmtMW(v.mw)} MW (${v.n} projects)`),
                      textposition: "outside" as const, textfont: { size: 10, color: t.text },
                    }]}
                    layout={{
                      height: Math.max(400, top20.length * 30), ...L,
                      margin: { l: 140, r: 120, t: 10, b: 40 },
                      xaxis: { title: "Capacity (MW)", gridcolor: t.grid },
                      yaxis: { autorange: "reversed", gridcolor: t.grid },
                    }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                  />
                </div>
                <div className="card">
                  <div className="text-sm font-semibold mb-2">County Fuel Mix (Top 15)</div>
                  <Plot
                    data={FUEL_ORDER.map(f => ({
                      type: "bar" as const, orientation: "h" as const, name: f,
                      y: top15, x: top15.map(c => countyFuelMix[c][f] ?? 0),
                      marker: { color: colorOf(f, t.muted) },
                    }))}
                    layout={{
                      height: Math.max(400, top15.length * 30), ...L, barmode: "stack",
                      margin: { l: 140, r: 60, t: 10, b: 40 },
                      xaxis: { title: "Capacity (MW)", gridcolor: t.grid },
                      yaxis: { autorange: "reversed", gridcolor: t.grid },
                    }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                  />
                </div>
              </div>
            );
          })()}

          {/* ── Tab 4: Financial Security ── */}
          {activeTab === 4 && (() => {
            const byFuelFS: Record<string, { yes: number; no: number }> = {};
            let secured = 0, unsecured = 0;
            for (const p of projects) {
              const row = byFuelFS[p.fuel_type] ?? { yes: 0, no: 0 };
              if (p.financial_security === "Yes") { row.yes += p.capacity_mw; secured += p.capacity_mw; }
              else if (p.financial_security === "No") { row.no += p.capacity_mw; unsecured += p.capacity_mw; }
              byFuelFS[p.fuel_type] = row;
            }
            const securedPct = agg.totalMW > 0 ? (secured / agg.totalMW) * 100 : 0;

            const byYearFS: Record<number, { yes: number; no: number; rate: number }> = {};
            for (const p of projects) {
              if (p.year === null) continue;
              const row = byYearFS[p.year] ?? { yes: 0, no: 0, rate: 0 };
              if (p.financial_security === "Yes") row.yes += p.capacity_mw;
              else if (p.financial_security === "No") row.no += p.capacity_mw;
              byYearFS[p.year] = row;
            }
            const years = Object.keys(byYearFS).map(Number).sort((a, b) => a - b);
            for (const y of years) {
              const r = byYearFS[y];
              const total = r.yes + r.no;
              r.rate = total > 0 ? (r.yes / total) * 100 : 0;
            }

            return (
              <div className="space-y-4">
                <div className="text-sm text-text-muted">Projects that have posted financial security are more likely to be built on schedule.</div>
                <div className="card card-compact">
                  <div className="flex flex-wrap gap-6">
                    <Metric label="Secured Pipeline" value={`${fmtMW(secured)} MW`} delta={`${securedPct.toFixed(0)}%`} deltaType="gain" />
                    <Metric label="Unsecured Pipeline" value={`${fmtMW(unsecured)} MW`} />
                    <Metric label="Security Rate" value={`${securedPct.toFixed(1)}%`} />
                  </div>
                </div>
                <div className="card">
                  <div className="text-sm font-semibold mb-2">Secured vs Unsecured by Fuel Type</div>
                  <Plot
                    data={[
                      { type: "bar" as const, name: "Secured", x: Object.keys(byFuelFS), y: Object.values(byFuelFS).map(v => v.yes), marker: { color: "#22c55e" } },
                      { type: "bar" as const, name: "Unsecured", x: Object.keys(byFuelFS), y: Object.values(byFuelFS).map(v => v.no), marker: { color: "#ef4444" } },
                    ]}
                    layout={{ height: 380, ...L, barmode: "group", yaxis: { title: "Megawatts (MW)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                  />
                </div>
                <div className="card">
                  <div className="text-sm font-semibold mb-2">Financial Security Rate by COD Year</div>
                  <Plot
                    data={[{
                      type: "bar" as const,
                      x: years.map(String),
                      y: years.map(y => byYearFS[y].rate),
                      marker: { color: years.map(y => byYearFS[y].rate > 50 ? "#22c55e" : byYearFS[y].rate > 25 ? "#eab308" : "#ef4444") },
                      text: years.map(y => `${byYearFS[y].rate.toFixed(0)}%`),
                      textposition: "outside" as const, textfont: { size: 10, color: t.text },
                    }]}
                    layout={{
                      height: 340, ...L,
                      yaxis: { title: "Security Rate (%)", gridcolor: t.grid, range: [0, 105] },
                      xaxis: { gridcolor: t.grid },
                      shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 50, y1: 50, line: { color: t.text, width: 1, dash: "dot" } }],
                    }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                  />
                </div>
              </div>
            );
          })()}

          {/* ── Tab 5: Month-over-Month ── */}
          {activeTab === 5 && (
            <div className="space-y-4">
              <div className="text-sm text-text-muted">
                Pipeline changes over time — total MW, fuel mix shifts, and individual project-level differences.
              </div>
              {!momReady && (
                <div className="card text-center py-8 text-sm text-text-muted">
                  Loading {months.length} months of data… ({momLoaded}/{months.length})
                </div>
              )}
              {momReady && momData && momData.length < 2 && (
                <div className="card text-center py-8 text-sm text-text-muted">Need at least 2 months of data for comparison.</div>
              )}
              {momReady && momData && momData.length >= 2 && (() => {
                const latest = momData[momData.length - 1];
                const prior = momData[momData.length - 2];
                const momChange = latest.total - prior.total;
                const momPct = prior.total > 0 ? (momChange / prior.total) * 100 : 0;
                return (
                  <>
                    <div className="card">
                      <div className="text-sm font-semibold mb-2">Total Pipeline MW Over Time</div>
                      <Plot
                        data={[{
                          type: "scatter" as const, mode: "lines+markers" as const,
                          x: momData.map(r => r.month.replace("_", " ")), y: momData.map(r => r.total),
                          line: { color: t.text, width: 3 }, marker: { size: 8 }, name: "Total Pipeline",
                        }]}
                        layout={{ height: 340, ...L, yaxis: { title: "Total Pipeline (MW)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                        config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                      />
                    </div>
                    <div className="card card-compact">
                      <div className="flex flex-wrap gap-6">
                        <Metric label="Latest Pipeline" value={`${fmtMW(latest.total)} MW`} />
                        <Metric label="MoM Change" value={`${fmtMWSigned(momChange)} MW`} />
                        <Metric label="MoM % Change" value={`${momPct >= 0 ? "+" : ""}${momPct.toFixed(1)}%`} />
                      </div>
                    </div>
                    <div className="card">
                      <div className="text-sm font-semibold mb-2">Fuel Mix Evolution</div>
                      <Plot
                        data={FUEL_ORDER.map(f => ({
                          type: "scatter" as const, mode: "lines" as const, name: f,
                          x: momData.map(r => r.month.replace("_", " ")),
                          y: momData.map(r => r[f] ?? 0),
                          stackgroup: "fuel",
                          line: { width: 0.5, color: colorOf(f, t.muted) },
                        }))}
                        layout={{ height: 380, ...L, yaxis: { title: "Capacity (MW)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                        config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                      />
                    </div>
                    <div className="card">
                      <div className="text-sm font-semibold mb-2">Monthly Change by Fuel Type</div>
                      <Plot
                        data={FUEL_ORDER.map(f => ({
                          type: "bar" as const, name: f,
                          x: momData.map(r => r.month.replace("_", " ")),
                          y: momData.map((r, i) => i === 0 ? 0 : (r[f] ?? 0) - (momData[i - 1][f] ?? 0)),
                          marker: { color: colorOf(f, t.muted) },
                        }))}
                        layout={{
                          height: 380, ...L, barmode: "group",
                          yaxis: { title: "MoM Change (MW)", gridcolor: t.grid },
                          xaxis: { gridcolor: t.grid },
                          shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 0, y1: 0, line: { color: t.text, width: 1 } }],
                          hovermode: "x unified",
                        }}
                        config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                      />
                    </div>

                    {/* Project-level diff */}
                    <div className="card">
                      <div className="text-sm font-semibold mb-2">Project-Level Comparison</div>
                      <div className="flex gap-3 mb-3 flex-wrap">
                        <div>
                          <label className="metric-label">From Month</label>
                          <select value={compareFrom} onChange={e => setCompareFrom(e.target.value)}
                            className="mt-0.5 px-3 py-1.5 border border-border rounded text-sm bg-surface">
                            {momData.map(r => <option key={r.month} value={r.month}>{r.month.replace("_", " ")}</option>)}
                          </select>
                        </div>
                        <div>
                          <label className="metric-label">To Month</label>
                          <select value={compareTo} onChange={e => setCompareTo(e.target.value)}
                            className="mt-0.5 px-3 py-1.5 border border-border rounded text-sm bg-surface">
                            {momData.map(r => <option key={r.month} value={r.month}>{r.month.replace("_", " ")}</option>)}
                          </select>
                        </div>
                      </div>

                      {diff && (
                        <>
                          <div className="flex flex-wrap gap-6 mb-4">
                            <Metric label="Projects Added" value={String(diff.added.length)} delta={diff.added.length > 0 ? `+${fmtMW(diff.added.reduce((s, p) => s + p.capacity_mw, 0))} MW` : "0 MW"} deltaType="gain" />
                            <Metric label="Projects Removed" value={String(diff.removed.length)} delta={diff.removed.length > 0 ? `-${fmtMW(diff.removed.reduce((s, p) => s + p.capacity_mw, 0))} MW` : "0 MW"} deltaType="loss" />
                            <Metric label="COD Changes" value={String(diff.codChanges.length)} />
                            <Metric label="Capacity Revisions" value={String(diff.capChanges.length)} />
                          </div>

                          {diff.added.length > 0 && (
                            <div className="mb-4">
                              <div className="text-xs font-semibold mb-1">New Projects ({compareFrom.replace("_", " ")} → {compareTo.replace("_", " ")})</div>
                              <div className="overflow-x-auto max-h-64 overflow-y-auto">
                                <table className="data-table text-xs">
                                  <thead className="sticky top-0 bg-surface"><tr><th>Project</th><th>Fuel</th><th className="text-right">MW</th><th>COD</th><th>County</th></tr></thead>
                                  <tbody>{diff.added.map((p, i) => (
                                    <tr key={`${p.inr}-${i}`}><td>{p.project_name}</td><td style={{ color: colorOf(p.fuel_type, t.text) }}>{p.fuel_type}</td><td className="font-data text-right">{p.capacity_mw.toFixed(1)}</td><td className="font-data">{p.projected_cod ?? "—"}</td><td>{p.county}</td></tr>
                                  ))}</tbody>
                                </table>
                              </div>
                            </div>
                          )}

                          {diff.removed.length > 0 && (
                            <div className="mb-4">
                              <div className="text-xs font-semibold mb-1">Removed Projects</div>
                              <div className="overflow-x-auto max-h-64 overflow-y-auto">
                                <table className="data-table text-xs">
                                  <thead className="sticky top-0 bg-surface"><tr><th>Project</th><th>Fuel</th><th className="text-right">MW</th><th>COD</th><th>County</th></tr></thead>
                                  <tbody>{diff.removed.map((p, i) => (
                                    <tr key={`${p.inr}-${i}`}><td>{p.project_name}</td><td style={{ color: colorOf(p.fuel_type, t.text) }}>{p.fuel_type}</td><td className="font-data text-right">{p.capacity_mw.toFixed(1)}</td><td className="font-data">{p.projected_cod ?? "—"}</td><td>{p.county}</td></tr>
                                  ))}</tbody>
                                </table>
                              </div>
                            </div>
                          )}

                          {diff.codChanges.length > 0 && (
                            <div className="mb-4">
                              <div className="text-xs font-semibold mb-1">COD Schedule Changes</div>
                              <div className="overflow-x-auto max-h-64 overflow-y-auto">
                                <table className="data-table text-xs">
                                  <thead className="sticky top-0 bg-surface"><tr><th>Project</th><th>Fuel</th><th className="text-right">MW</th><th>Old COD</th><th>New COD</th></tr></thead>
                                  <tbody>{diff.codChanges.map((c, i) => (
                                    <tr key={i}><td>{c.project}</td><td style={{ color: colorOf(c.fuel, t.text) }}>{c.fuel}</td><td className="font-data text-right">{c.mw.toFixed(1)}</td><td className="font-data">{c.old_cod}</td><td className="font-data">{c.new_cod}</td></tr>
                                  ))}</tbody>
                                </table>
                              </div>
                            </div>
                          )}

                          {diff.capChanges.length > 0 && (
                            <div className="mb-4">
                              <div className="text-xs font-semibold mb-1">Capacity Revisions</div>
                              <div className="overflow-x-auto max-h-64 overflow-y-auto">
                                <table className="data-table text-xs">
                                  <thead className="sticky top-0 bg-surface"><tr><th>Project</th><th>Fuel</th><th className="text-right">Old MW</th><th className="text-right">New MW</th><th className="text-right">Change</th></tr></thead>
                                  <tbody>{diff.capChanges.map((c, i) => (
                                    <tr key={i}>
                                      <td>{c.project}</td>
                                      <td style={{ color: colorOf(c.fuel, t.text) }}>{c.fuel}</td>
                                      <td className="font-data text-right">{c.old_mw.toFixed(1)}</td>
                                      <td className="font-data text-right">{c.new_mw.toFixed(1)}</td>
                                      <td className={`font-data text-right ${c.change > 0 ? "text-gain" : "text-loss"}`}>{(c.change >= 0 ? "+" : "") + c.change.toFixed(1)}</td>
                                    </tr>
                                  ))}</tbody>
                                </table>
                              </div>
                            </div>
                          )}

                          {diff.added.length === 0 && diff.removed.length === 0 && diff.codChanges.length === 0 && diff.capChanges.length === 0 && compareFrom !== compareTo && (
                            <div className="text-xs text-gain">No changes detected between these two months.</div>
                          )}
                        </>
                      )}
                    </div>
                  </>
                );
              })()}
            </div>
          )}
        </>
      )}
    </div>
  );
}
