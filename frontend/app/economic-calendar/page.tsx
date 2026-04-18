"use client";

import { useState, useMemo, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import {
  fetchEconCalendarReleases,
  fetchEarningsCalendar,
  fetchTreasuryAuctions,
  fetchFredBatch,
  type EconEvent,
  type EarningsEntry,
  type TreasuryAuction,
} from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = [
  "Week at a Glance",
  "Economic Releases",
  "Inflation",
  "Labor Market",
  "Macro Dashboard",
  "Earnings",
  "Treasury Auctions",
  "Surprise Tracker",
];

const BIG_CAP = new Set([
  "AAPL","MSFT","GOOGL","GOOG","AMZN","NVDA","META","TSLA","BRK.B","UNH",
  "JNJ","V","XOM","JPM","WMT","MA","PG","LLY","HD","CVX",
  "MRK","ABBV","KO","PEP","AVGO","COST","TMO","MCD","CSCO","ACN",
  "ABT","DHR","NEE","LIN","WFC","TXN","PM","AMD","UNP","CRM",
  "MS","GS","BA","CAT","HON","IBM","GE","NFLX","DIS","NKE",
  "INTC","QCOM","AMAT","SBUX","LOW","INTU","ADP","SYK","BLK","CI",
]);

function isoToday() { return new Date().toISOString().slice(0, 10); }
function addDays(days: number) {
  const d = new Date(); d.setDate(d.getDate() + days); return d.toISOString().slice(0, 10);
}
function daysBetween(iso: string) {
  const a = new Date(iso + "T00:00:00");
  const b = new Date(isoToday() + "T00:00:00");
  return Math.round((a.getTime() - b.getTime()) / 86400000);
}
function formatDay(iso: string) {
  return new Date(iso + "T12:00:00").toLocaleDateString("en-US", { weekday: "short" });
}
function formatDate(iso: string) {
  return new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
function isWeekday(iso: string) {
  const d = new Date(iso + "T12:00:00").getDay();
  return d >= 1 && d <= 5;
}
function countdownLabel(days: number) {
  if (days < 0) return `${-days}d ago`;
  if (days === 0) return "TODAY";
  if (days === 1) return "Tomorrow";
  return `in ${days}d`;
}

type FredRow = { date: string; value: number };
function parseFredBatch(raw: Record<string, Record<string, unknown>[]> | undefined): Record<string, FredRow[]> {
  if (!raw) return {};
  const out: Record<string, FredRow[]> = {};
  for (const [k, rows] of Object.entries(raw)) {
    out[k] = rows.map(r => ({
      date: String(r.date ?? ""),
      value: Number(r.value ?? NaN),
    })).filter(r => Number.isFinite(r.value));
  }
  return out;
}

export default function EconomicCalendar() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);

  // ── Core queries ─────────────────────────────────────────────────
  const eventsQ = useQuery({
    queryKey: ["econ-releases"],
    queryFn: fetchEconCalendarReleases,
    staleTime: 30 * 60 * 1000,
  });
  const events = eventsQ.data?.events ?? [];

  const earningsFrom = isoToday();
  const earningsTo = addDays(30);
  const earningsQ = useQuery({
    queryKey: ["earnings", earningsFrom, earningsTo],
    queryFn: () => fetchEarningsCalendar(earningsFrom, earningsTo),
    staleTime: 30 * 60 * 1000,
  });
  const earnings: EarningsEntry[] = earningsQ.data?.earnings ?? [];

  const auctionsQ = useQuery({
    queryKey: ["treasury-auctions"],
    queryFn: fetchTreasuryAuctions,
    staleTime: 60 * 60 * 1000,
  });
  const auctions: TreasuryAuction[] = auctionsQ.data?.auctions ?? [];

  // ── Inflation (tab 2) ───────────────────────────────────────────
  const inflationQ = useQuery({
    queryKey: ["inflation"],
    queryFn: () => fetchFredBatch(
      ["CPIAUCSL", "CPILFESL", "PCEPI", "PCEPILFE", "PPIFIS", "CUUR0000SA0", "CUUR0000SA0L1E", "CUUR0000SAF1", "CUUR0000SEHE01", "CUUR0000SETB01"],
      24
    ).then(parseFredBatch),
    staleTime: 30 * 60 * 1000,
    enabled: activeTab === 2,
  });

  // ── Labor (tab 3) ──────────────────────────────────────────────
  const laborQ = useQuery({
    queryKey: ["labor"],
    queryFn: () => fetchFredBatch(
      ["PAYEMS", "UNRATE", "ICSA", "CES0500000003", "JTSJOL"],
      120
    ).then(parseFredBatch),
    staleTime: 30 * 60 * 1000,
    enabled: activeTab === 3,
  });

  // ── Macro Dashboard (tab 4) ─────────────────────────────────────
  const macroQ = useQuery({
    queryKey: ["macro-dashboard"],
    queryFn: () => fetchFredBatch(
      ["FEDFUNDS", "UNRATE", "CPIAUCSL", "GDP", "T10Y2Y", "PAYEMS", "RSAFS", "INDPRO", "HOUST", "UMCSENT", "DTWEXBGS"],
      60
    ).then(parseFredBatch),
    staleTime: 30 * 60 * 1000,
    enabled: activeTab === 4,
  });

  // ── Surprise Tracker (tab 7) ────────────────────────────────────
  const surpriseQ = useQuery({
    queryKey: ["surprise"],
    queryFn: () => fetchFredBatch(
      ["PAYEMS", "UNRATE", "CPIAUCSL", "RSAFS", "INDPRO", "UMCSENT", "HOUST"],
      26
    ).then(parseFredBatch),
    staleTime: 30 * 60 * 1000,
    enabled: activeTab === 7,
  });

  // ── Today + hero ────────────────────────────────────────────────
  const today = isoToday();
  const todayEvents = events.filter(e => e.date === today);
  const thisWeekEnd = useMemo(() => {
    const d = new Date(today + "T00:00:00");
    const daysTil = 6 - d.getDay(); // toward Saturday
    d.setDate(d.getDate() + daysTil);
    return d.toISOString().slice(0, 10);
  }, [today]);

  const upcoming = useMemo(() => events.filter(e => e.date >= today).slice(0, 1), [events, today]);

  // ── Render ─────────────────────────────────────────────────────
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Economic Calendar</h1>
        <p className="text-text-secondary text-sm mt-1">
          Upcoming macro releases, earnings, Treasury auctions, and inflation data.
        </p>
      </div>

      {/* Hero */}
      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          <Metric label="Today" value={todayEvents.length > 0 ? `${todayEvents.length} event${todayEvents.length > 1 ? "s" : ""}` : "No events"} />
          <Metric label="This Week" value={String(events.filter(e => e.date >= today && e.date <= thisWeekEnd).length)} />
          <Metric label="Next Event" value={upcoming[0]?.event ?? "—"} delta={upcoming[0] ? countdownLabel(daysBetween(upcoming[0].date)) : undefined} />
          <Metric label="Earnings this month" value={String(earnings.length)} />
          <Metric label="Upcoming Auctions" value={String(auctions.length)} />
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
        {TABS.map((tab, i) => (
          <button key={tab} onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
            {tab}
          </button>
        ))}
      </div>

      {/* Tab 0: Week at a Glance */}
      {activeTab === 0 && <WeekAtGlance events={events} earnings={earnings} auctions={auctions} thisWeekEnd={thisWeekEnd} today={today} t={t} L={L} />}

      {/* Tab 1: Economic Releases */}
      {activeTab === 1 && <EconReleases events={events} today={today} t={t} L={L} />}

      {/* Tab 2: Inflation */}
      {activeTab === 2 && <InflationDashboard inf={inflationQ.data} isLoading={inflationQ.isLoading} t={t} L={L} />}

      {/* Tab 3: Labor */}
      {activeTab === 3 && <LaborDashboard labor={laborQ.data} isLoading={laborQ.isLoading} t={t} L={L} />}

      {/* Tab 4: Macro Dashboard */}
      {activeTab === 4 && <MacroDashboard macro={macroQ.data} isLoading={macroQ.isLoading} t={t} L={L} />}

      {/* Tab 5: Earnings */}
      {activeTab === 5 && <EarningsTab earnings={earnings} isLoading={earningsQ.isLoading} t={t} />}

      {/* Tab 6: Treasury Auctions */}
      {activeTab === 6 && <AuctionsTab auctions={auctions} isLoading={auctionsQ.isLoading} t={t} L={L} today={today} />}

      {/* Tab 7: Surprise Tracker */}
      {activeTab === 7 && <SurpriseTracker series={surpriseQ.data} isLoading={surpriseQ.isLoading} t={t} L={L} />}

      {eventsQ.isLoading && events.length === 0 && (
        <div className="card text-center py-8"><div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" /></div>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Tab 0: Week at a Glance
// ───────────────────────────────────────────────────────────────────
type WeekRow = { date: string; day: string; dateLabel: string; event: string; type: "Macro" | "Earnings" | "Auction"; impact: "High" | "Medium" | "Low"; countdown: string };

function WeekAtGlance({ events, earnings, auctions, thisWeekEnd, today, t, L: _L }: { events: EconEvent[]; earnings: EarningsEntry[]; auctions: TreasuryAuction[]; thisWeekEnd: string; today: string; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const rows: WeekRow[] = [];
  for (const e of events) {
    if (e.date < today || e.date > thisWeekEnd || !isWeekday(e.date)) continue;
    rows.push({
      date: e.date, day: formatDay(e.date), dateLabel: formatDate(e.date),
      event: e.event, type: "Macro",
      impact: (e.impact === "High" || e.impact === "Medium" || e.impact === "Low") ? e.impact : "Medium",
      countdown: countdownLabel(daysBetween(e.date)),
    });
  }
  const bigEarn = earnings.filter(e => BIG_CAP.has(e.symbol));
  const earnList = bigEarn.length > 0 ? bigEarn : [...earnings].filter(e => (e.revenueEstimate ?? 0) > 0).sort((a, b) => (b.revenueEstimate ?? 0) - (a.revenueEstimate ?? 0)).slice(0, 10);
  for (const e of earnList) {
    if (e.date < today || e.date > thisWeekEnd || !isWeekday(e.date)) continue;
    rows.push({
      date: e.date, day: formatDay(e.date), dateLabel: formatDate(e.date),
      event: `${e.symbol} Earnings`, type: "Earnings",
      impact: BIG_CAP.has(e.symbol) ? "High" : "Medium",
      countdown: countdownLabel(daysBetween(e.date)),
    });
  }
  for (const a of auctions) {
    if (!a.auction_date) continue;
    const d = a.auction_date.slice(0, 10);
    if (d < today || d > thisWeekEnd || !isWeekday(d)) continue;
    rows.push({
      date: d, day: formatDay(d), dateLabel: formatDate(d),
      event: `Treasury ${a.security_type} ${a.security_term}`, type: "Auction",
      impact: "Low",
      countdown: countdownLabel(daysBetween(d)),
    });
  }
  rows.sort((a, b) => a.date.localeCompare(b.date));

  const typeClass = (ty: string) => ty === "Macro" ? "text-loss" : ty === "Earnings" ? "text-accent" : "text-warn";
  const impactClass = (im: string) => im === "High" ? "text-loss font-semibold" : im === "Medium" ? "text-warn font-semibold" : "text-text-muted";
  const cdClass = (cd: string) => cd === "TODAY" ? "text-loss font-bold" : cd === "Tomorrow" ? "text-warn font-bold" : "text-text-muted";

  if (rows.length === 0) {
    return <div className="card text-center py-8 text-sm text-text-muted">No events this week.</div>;
  }

  // Day buckets
  const start = new Date(today + "T00:00:00");
  const end = new Date(thisWeekEnd + "T00:00:00");
  const days: string[] = [];
  for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
    const iso = d.toISOString().slice(0, 10);
    if (isWeekday(iso)) days.push(iso);
  }

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-2">This Week — All Events</div>
        <div className="overflow-x-auto max-h-[500px] overflow-y-auto">
          <table className="data-table text-xs">
            <thead className="sticky top-0 bg-surface"><tr><th>Day</th><th>Date</th><th>Event</th><th>Type</th><th>Impact</th><th>When</th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td className="text-text-muted">{r.day}</td>
                  <td>{r.dateLabel}</td>
                  <td className="font-semibold">{r.event}</td>
                  <td className={typeClass(r.type)}>{r.type}</td>
                  <td className={impactClass(r.impact)}>{r.impact}</td>
                  <td className={cdClass(r.countdown)}>{r.countdown}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-3">Week View</div>
        <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${days.length}, minmax(0, 1fr))` }}>
          {days.map(d => {
            const dayRows = rows.filter(r => r.date === d);
            const isToday = d === today;
            return (
              <div key={d} className={`rounded-lg border p-2 ${isToday ? "border-accent bg-accent/5" : "border-border"}`}>
                <div className={`text-xs font-bold ${isToday ? "text-accent" : ""}`}>{formatDay(d)} {formatDate(d)}</div>
                {dayRows.length === 0 ? (
                  <div className="text-[10px] text-text-muted mt-1">No events</div>
                ) : (
                  <ul className="space-y-1 mt-1.5">
                    {dayRows.map((r, i) => (
                      <li key={i} className="text-[11px]">
                        {r.impact === "High" && <span className="text-loss mr-1">●</span>}
                        <span className={typeClass(r.type) + " font-semibold"}>
                          {r.type === "Macro" ? "📊" : r.type === "Earnings" ? "💰" : "🏛️"}
                        </span>
                        <span className="ml-1">{r.event}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Tab 1: Economic Releases
// ───────────────────────────────────────────────────────────────────
function EconReleases({ events, today, t, L }: { events: EconEvent[]; today: string; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const [impactFilter, setImpactFilter] = useState<string[]>(["High", "Medium"]);
  const categories = useMemo(() => Array.from(new Set(events.map(e => e.category))), [events]);
  const [catFilter, setCatFilter] = useState<string[]>([]);
  useEffect(() => { if (catFilter.length === 0 && categories.length > 0) setCatFilter(categories); }, [categories, catFilter.length]);

  const filtered = events.filter(e => impactFilter.includes(e.impact) && catFilter.includes(e.category) && isWeekday(e.date));

  // Next 7-day timeline
  const sevenDaysOut = addDays(7);
  const next7 = filtered.filter(e => e.date >= today && e.date <= sevenDaysOut);
  const days: string[] = [];
  for (let i = 0; i < 8; i++) {
    const d = addDays(i);
    if (isWeekday(d)) days.push(d);
  }

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <div className="metric-label mb-1">Impact</div>
            <div className="flex gap-1 flex-wrap">
              {["High", "Medium"].map(im => (
                <button key={im} onClick={() => setImpactFilter(prev => prev.includes(im) ? prev.filter(x => x !== im) : [...prev, im])}
                  className={`px-2 py-1 text-xs rounded ${impactFilter.includes(im) ? (im === "High" ? "bg-loss text-bg" : "bg-warn text-bg") : "bg-surface-alt text-text-muted"}`}>{im}</button>
              ))}
            </div>
          </div>
          <div>
            <div className="metric-label mb-1">Category</div>
            <div className="flex gap-1 flex-wrap">
              {categories.map(c => (
                <button key={c} onClick={() => setCatFilter(prev => prev.includes(c) ? prev.filter(x => x !== c) : [...prev, c])}
                  className={`px-2 py-1 text-xs rounded ${catFilter.includes(c) ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}>{c}</button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Releases ({filtered.length})</div>
        <div className="overflow-x-auto max-h-[500px] overflow-y-auto">
          <table className="data-table text-xs">
            <thead className="sticky top-0 bg-surface"><tr><th>Date</th><th>Event</th><th>Impact</th><th>Category</th><th>Countdown</th></tr></thead>
            <tbody>
              {filtered.map((e, i) => {
                const d = daysBetween(e.date);
                return (
                  <tr key={i}>
                    <td>{formatDay(e.date)}, {formatDate(e.date)}</td>
                    <td className="font-semibold">{e.event}</td>
                    <td className={e.impact === "High" ? "text-loss font-semibold" : "text-warn"}>{e.impact}</td>
                    <td>{e.category}</td>
                    <td className={d === 0 ? "text-loss font-bold" : d === 1 ? "text-warn font-bold" : "text-text-muted"}>{countdownLabel(d)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Next 7 Days Timeline</div>
        {next7.length === 0 ? (
          <div className="text-sm text-text-muted text-center py-6">No events in the next 7 days.</div>
        ) : (() => {
          // Build scatter by day index with stacking
          const counts: Record<number, number> = {};
          const xs: number[] = []; const ys: number[] = []; const texts: string[] = []; const colors: string[] = [];
          for (const e of next7) {
            const di = days.indexOf(e.date);
            if (di < 0) continue;
            const stack = counts[di] ?? 0;
            counts[di] = stack + 1;
            xs.push(di);
            ys.push(stack * 0.8 + 0.3);
            texts.push(e.event);
            colors.push(e.impact === "High" ? t.loss : t.hv20);
          }
          return (
            <Plot
              data={[{
                type: "scatter" as const, mode: "markers+text" as const,
                x: xs, y: ys, text: texts,
                marker: { size: 14, color: colors, symbol: "diamond" as const },
                textposition: "middle right" as const, textfont: { size: 10, color: t.text },
                hovertemplate: "%{text}<extra></extra>",
              }]}
              layout={{
                height: 280, ...L,
                xaxis: { tickmode: "array", tickvals: days.map((_, i) => i), ticktext: days.map(d => formatDay(d) + " " + formatDate(d)), gridcolor: t.grid },
                yaxis: { showticklabels: false, range: [0, Math.max(3, Math.max(...Object.values(counts)) + 0.5)], gridcolor: t.grid },
                showlegend: false,
              }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
            />
          );
        })()}
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Tab 2: Inflation Dashboard
// ───────────────────────────────────────────────────────────────────
function yoyArray(series: FredRow[]): { dates: string[]; yoy: number[] } {
  if (series.length < 13) return { dates: [], yoy: [] };
  const dates: string[] = []; const yoy: number[] = [];
  for (let i = 12; i < series.length; i++) {
    const base = series[i - 12].value;
    if (base > 0) {
      dates.push(series[i].date);
      yoy.push((series[i].value / base - 1) * 100);
    }
  }
  return { dates, yoy };
}

function momArray(series: FredRow[]): { dates: string[]; mom: number[] } {
  const dates: string[] = []; const mom: number[] = [];
  for (let i = 1; i < series.length; i++) {
    const base = series[i - 1].value;
    if (base > 0) {
      dates.push(series[i].date);
      mom.push((series[i].value / base - 1) * 100);
    }
  }
  return { dates, mom };
}

function InflationDashboard({ inf, isLoading, t, L }: { inf?: Record<string, FredRow[]>; isLoading: boolean; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  if (isLoading || !inf) return <Spinner />;

  const series: Array<[string, string, string]> = [
    ["CPIAUCSL", "CPI (All Items)", t.loss],
    ["CPILFESL", "Core CPI", t.hv20],
    ["PCEPI", "PCE Price Index", t.accent],
    ["PCEPILFE", "Core PCE", t.gain],
    ["PPIFIS", "PPI (Final Demand)", t.hv60],
  ];

  const latestMetrics = series.map(([sid, label, color]) => {
    const arr = inf[sid] ?? [];
    if (arr.length < 14) return { sid, label, color, yoy: 0, change: 0 };
    const latestYoY = (arr[arr.length - 1].value / arr[arr.length - 13].value - 1) * 100;
    const prevYoY = (arr[arr.length - 2].value / arr[arr.length - 14].value - 1) * 100;
    return { sid, label, color, yoy: latestYoY, change: latestYoY - prevYoY };
  });

  const cpi = inf["CPIAUCSL"] ?? [];
  const momData = momArray(cpi);

  const components: Array<[string, string, string]> = [
    ["CUUR0000SA0", "All Items", t.loss],
    ["CUUR0000SA0L1E", "Core (ex Food & Energy)", t.hv20],
    ["CUUR0000SAF1", "Food", t.accent],
    ["CUUR0000SEHE01", "Shelter", t.hv60],
    ["CUUR0000SETB01", "Gasoline", t.gain],
  ];

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          {latestMetrics.map(m => (
            <Metric key={m.sid} label={m.label} value={`${m.yoy.toFixed(1)}% YoY`} delta={`${m.change >= 0 ? "+" : ""}${m.change.toFixed(1)}%`} deltaType={m.change > 0 ? "loss" : "gain"} />
          ))}
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">YoY Inflation</div>
        <Plot
          data={series.map(([sid, label, color]) => {
            const { dates, yoy } = yoyArray(inf[sid] ?? []);
            return { type: "scatter" as const, mode: "lines" as const, name: label, x: dates, y: yoy, line: { color, width: 2 } };
          })}
          layout={{
            height: 420, ...L,
            yaxis: { title: "Year-over-Year (%)", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 2.0, y1: 2.0, line: { color: t.gain, width: 1, dash: "dash" as const } }],
            hovermode: "x unified",
          }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
        <div className="text-[11px] text-text-muted mt-1">Dashed green line = Fed 2% target.</div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Month-over-Month CPI</div>
        <Plot
          data={[{
            type: "bar" as const, x: momData.dates, y: momData.mom,
            marker: { color: momData.mom.map(v => v <= 0.2 ? t.gain : v <= 0.4 ? t.hv20 : t.loss) },
            hovertemplate: "%{x}: %{y:.2f}%<extra></extra>",
          }]}
          layout={{
            height: 300, ...L,
            yaxis: { title: "MoM Change (%)", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 0.167, y1: 0.167, line: { color: t.gain, width: 1, dash: "dot" as const } }],
            hovermode: "x unified",
          }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
        <div className="text-[11px] text-text-muted mt-1">Green &lt; 0.2% | Yellow 0.2–0.4% | Red &gt; 0.4% · dotted line = ~2% annualized target.</div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">CPI Breakdown</div>
        <Plot
          data={components.map(([sid, label, color]) => {
            const { dates, yoy } = yoyArray(inf[sid] ?? []);
            return { type: "scatter" as const, mode: "lines" as const, name: label, x: dates, y: yoy, line: { color, width: 2 } };
          })}
          layout={{
            height: 380, ...L,
            yaxis: { title: "YoY (%)", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 2.0, y1: 2.0, line: { color: t.text, width: 0.5, dash: "dash" as const } }],
            hovermode: "x unified",
          }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Tab 3: Labor Market
// ───────────────────────────────────────────────────────────────────
function LaborDashboard({ labor, isLoading, t, L }: { labor?: Record<string, FredRow[]>; isLoading: boolean; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  if (isLoading || !labor) return <Spinner />;
  const metric = (sid: string, label: string, unit: string) => {
    const arr = labor[sid] ?? [];
    if (arr.length < 2) return { label, value: "—", delta: "—" };
    const latest = arr[arr.length - 1].value;
    const prev = arr[arr.length - 2].value;
    const change = latest - prev;
    if (unit === "K") return { label, value: `${latest.toLocaleString(undefined, { maximumFractionDigits: 0 })}${unit}`, delta: `${change >= 0 ? "+" : ""}${change.toFixed(0)}` };
    if (unit === "%") return { label, value: `${latest.toFixed(1)}${unit}`, delta: `${change >= 0 ? "+" : ""}${change.toFixed(1)}${unit}` };
    return { label, value: `$${latest.toFixed(2)}`, delta: `${change >= 0 ? "+" : ""}$${change.toFixed(2)}` };
  };

  const metrics = [
    metric("PAYEMS", "Nonfarm Payrolls", "K"),
    metric("UNRATE", "Unemployment Rate", "%"),
    metric("ICSA", "Initial Jobless Claims", "K"),
    metric("CES0500000003", "Avg Hourly Earnings", "$"),
  ];

  const nfp = labor["PAYEMS"] ?? [];
  const nfpMom = [];
  const nfpDates = [];
  for (let i = 1; i < nfp.length; i++) {
    nfpDates.push(nfp[i].date);
    nfpMom.push(nfp[i].value - nfp[i - 1].value);
  }

  const panels: Array<[string, string, string]> = [
    ["UNRATE", "Unemployment Rate (%)", t.loss],
    ["ICSA", "Initial Jobless Claims (Weekly)", t.hv20],
    ["JTSJOL", "Job Openings (JOLTS)", t.accent],
    ["CES0500000003", "Avg Hourly Earnings ($)", t.gain],
  ];

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          {metrics.map(m => <Metric key={m.label} label={m.label} value={m.value} delta={m.delta} deltaType="neutral" />)}
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Monthly Payroll Changes (MoM)</div>
        <Plot
          data={[{
            type: "bar" as const, x: nfpDates, y: nfpMom,
            marker: { color: nfpMom.map(v => v > 0 ? t.gain : t.loss) },
            hovertemplate: "%{x}: %{y:.0f}K<extra></extra>",
          }]}
          layout={{
            height: 320, ...L, yaxis: { title: "Monthly Change (K)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid },
            shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 0, y1: 0, line: { color: t.text, width: 1 } }],
            hovermode: "x unified",
          }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {panels.map(([sid, label, color]) => {
          const arr = labor[sid] ?? [];
          if (arr.length === 0) return null;
          return (
            <div key={sid} className="card">
              <div className="text-sm font-semibold mb-2">{label}</div>
              <Plot
                data={[{ type: "scatter" as const, mode: "lines" as const, x: arr.map(r => r.date), y: arr.map(r => r.value), line: { color, width: 2 } }]}
                layout={{ height: 250, ...L, yaxis: { gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Tab 4: Macro Dashboard
// ───────────────────────────────────────────────────────────────────
function MacroDashboard({ macro, isLoading, t, L }: { macro?: Record<string, FredRow[]>; isLoading: boolean; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  if (isLoading || !macro) return <Spinner />;

  const cells: Array<[string, string, string]> = [
    ["FEDFUNDS", "Fed Funds Rate", "%"],
    ["UNRATE", "Unemployment", "%"],
    ["CPIAUCSL", "CPI Index", ""],
    ["GDP", "GDP", "$B"],
  ];

  const charts: Array<[string, string, string]> = [
    ["FEDFUNDS", "Fed Funds Rate (%)", t.accent],
    ["UNRATE", "Unemployment Rate (%)", t.loss],
    ["CPIAUCSL", "CPI (All Urban Consumers)", t.hv20],
    ["T10Y2Y", "10Y-2Y Treasury Spread (%)", t.gain],
  ];

  const extras: Array<[string, string]> = [
    ["PAYEMS", "Total Nonfarm Payrolls (Thousands)"],
    ["RSAFS", "Retail Sales (Millions $)"],
    ["INDPRO", "Industrial Production Index"],
    ["HOUST", "Housing Starts (Thousands)"],
    ["UMCSENT", "Consumer Sentiment"],
    ["DTWEXBGS", "Trade-Weighted Dollar Index"],
  ];

  const metric = (sid: string, label: string, unit: string) => {
    const arr = macro[sid] ?? [];
    if (arr.length < 2) return { label, value: "—", delta: "—" };
    const latest = arr[arr.length - 1].value;
    const prev = arr[arr.length - 2].value;
    const change = latest - prev;
    if (unit === "%") return { label, value: `${latest.toFixed(1)}%`, delta: `${change >= 0 ? "+" : ""}${change.toFixed(1)}%` };
    if (unit === "$B") return { label, value: `$${latest.toLocaleString(undefined, { maximumFractionDigits: 0 })}B`, delta: `${change >= 0 ? "+" : ""}${change.toFixed(0)}` };
    return { label, value: latest.toLocaleString(undefined, { maximumFractionDigits: 1 }), delta: `${change >= 0 ? "+" : ""}${change.toFixed(1)}` };
  };

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          {cells.map(([sid, label, unit]) => {
            const m = metric(sid, label, unit);
            return <Metric key={sid} label={m.label} value={m.value} delta={m.delta} deltaType="neutral" />;
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {charts.map(([sid, title, color]) => {
          const arr = macro[sid] ?? [];
          if (arr.length === 0) return null;
          return (
            <div key={sid} className="card">
              <div className="text-sm font-semibold mb-2">{title}</div>
              <Plot
                data={[{ type: "scatter" as const, mode: "lines" as const, x: arr.map(r => r.date), y: arr.map(r => r.value), line: { color, width: 2 } }]}
                layout={{ height: 260, ...L, yaxis: { gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
              />
            </div>
          );
        })}
      </div>

      <details className="card">
        <summary className="text-sm font-semibold cursor-pointer select-none">More Indicators</summary>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-3">
          {extras.map(([sid, title]) => {
            const arr = macro[sid] ?? [];
            if (arr.length === 0) return null;
            return (
              <div key={sid}>
                <div className="text-xs font-semibold mb-1">{title}</div>
                <Plot
                  data={[{ type: "scatter" as const, mode: "lines+markers" as const, x: arr.map(r => r.date), y: arr.map(r => r.value), line: { color: t.accent, width: 1.5 } }]}
                  layout={{ height: 220, ...L, yaxis: { gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified" }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
                />
              </div>
            );
          })}
        </div>
      </details>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Tab 5: Earnings
// ───────────────────────────────────────────────────────────────────
function EarningsTab({ earnings, isLoading, t: _t }: { earnings: EarningsEntry[]; isLoading: boolean; t: ReturnType<typeof getChartTheme> }) {
  const [showAll, setShowAll] = useState(false);

  const filtered = useMemo(() => {
    const weekdayOnly = earnings.filter(e => isWeekday(e.date));
    if (showAll) return [...weekdayOnly].sort((a, b) => a.date.localeCompare(b.date) || a.symbol.localeCompare(b.symbol));
    const bigCap = weekdayOnly.filter(e => BIG_CAP.has(e.symbol));
    if (bigCap.length > 0) return [...bigCap].sort((a, b) => a.date.localeCompare(b.date) || a.symbol.localeCompare(b.symbol));
    // Fallback: top 30 by revenue estimate
    return [...weekdayOnly].filter(e => (e.revenueEstimate ?? 0) > 0).sort((a, b) => (b.revenueEstimate ?? 0) - (a.revenueEstimate ?? 0)).slice(0, 30);
  }, [earnings, showAll]);

  if (isLoading) return <Spinner />;
  if (earnings.length === 0) {
    return <div className="card text-center py-8 text-sm text-text-muted">No earnings data available.</div>;
  }

  const fmtEPS = (v: number | null) => v == null ? "—" : `$${v.toFixed(2)}`;
  const fmtRev = (v: number | null) => v == null ? "—" : v > 1e9 ? `$${(v / 1e9).toFixed(2)}B` : `$${(v / 1e6).toFixed(0)}M`;
  const timing = (h: string) => h === "bmo" ? "Pre-Market" : h === "amc" ? "After-Close" : h === "dmh" ? "During" : h || "TBD";
  const beatMiss = (e: EarningsEntry) => {
    if (e.epsActual == null || e.epsEstimate == null) return "—";
    if (e.epsActual > e.epsEstimate) return "BEAT";
    if (e.epsActual < e.epsEstimate) return "MISS";
    return "MET";
  };

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
          <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} className="accent-accent" />
          <span>Show all earnings (including small-cap)</span>
        </label>
      </div>
      <div className="card">
        <div className="text-sm font-semibold mb-2">Upcoming Earnings ({filtered.length})</div>
        <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
          <table className="data-table text-xs">
            <thead className="sticky top-0 bg-surface">
              <tr><th>Date</th><th>Ticker</th><th>EPS Est.</th><th>EPS Actual</th><th>Rev Est.</th><th>Rev Actual</th><th>Timing</th><th>Result</th></tr>
            </thead>
            <tbody>
              {filtered.map((e, i) => {
                const res = beatMiss(e);
                return (
                  <tr key={`${e.date}-${e.symbol}-${i}`}>
                    <td>{formatDay(e.date)}, {formatDate(e.date)}</td>
                    <td className="font-semibold font-data">{e.symbol}</td>
                    <td className="font-data">{fmtEPS(e.epsEstimate)}</td>
                    <td className="font-data">{fmtEPS(e.epsActual)}</td>
                    <td className="font-data">{fmtRev(e.revenueEstimate)}</td>
                    <td className="font-data">{fmtRev(e.revenueActual)}</td>
                    <td className="text-text-muted">{timing(e.hour)}</td>
                    <td className={res === "BEAT" ? "text-gain font-semibold" : res === "MISS" ? "text-loss font-semibold" : "text-text-muted"}>{res}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!showAll && <div className="text-[11px] text-text-muted mt-2">Showing big-cap names only. Toggle &quot;Show all&quot; to include small-caps.</div>}
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Tab 6: Treasury Auctions
// ───────────────────────────────────────────────────────────────────
function AuctionsTab({ auctions, isLoading, today, t, L }: { auctions: TreasuryAuction[]; isLoading: boolean; today: string; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  if (isLoading) return <Spinner />;
  if (auctions.length === 0) return <div className="card text-center py-8 text-sm text-text-muted">No upcoming Treasury auctions.</div>;

  const typeColors: Record<string, string> = {
    Bill: t.accent, Note: t.gain, Bond: t.hv20, TIPS: t.hv60, FRN: t.loss, CMB: t.muted,
  };

  const fmtAmount = (amt?: string) => {
    if (!amt) return "—";
    const n = Number(amt);
    if (!Number.isFinite(n) || n <= 0) return "—";
    return `$${(n / 1e3).toFixed(0)}B`;
  };

  // Group auctions by type for scatter
  const types = Array.from(new Set(auctions.map(a => a.security_type)));

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-2">Upcoming Auctions</div>
        <div className="overflow-x-auto max-h-[500px] overflow-y-auto">
          <table className="data-table text-xs">
            <thead className="sticky top-0 bg-surface">
              <tr><th>Auction Date</th><th>Type</th><th>Term</th><th>Announcement</th><th>Issue Date</th><th>Size</th><th>CUSIP</th></tr>
            </thead>
            <tbody>
              {auctions.map((a, i) => (
                <tr key={`${a.cusip}-${i}`}>
                  <td className="font-data">{(a.auction_date || "").slice(0, 10)}</td>
                  <td style={{ color: typeColors[a.security_type] ?? t.text }} className="font-semibold">{a.security_type}</td>
                  <td>{a.security_term}</td>
                  <td className="font-data">{(a.announcemt_date || "").slice(0, 10)}</td>
                  <td className="font-data">{(a.issue_date || "").slice(0, 10)}</td>
                  <td className="font-data">{fmtAmount(a.offering_amt)}</td>
                  <td className="font-data text-text-muted">{a.cusip}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Auction Timeline</div>
        <Plot
          data={types.map(ty => {
            const subset = auctions.filter(a => a.security_type === ty);
            return {
              type: "scatter" as const, mode: "markers" as const, name: ty,
              x: subset.map(a => (a.auction_date || "").slice(0, 10)),
              y: subset.map(a => a.security_type),
              marker: { size: 12, color: typeColors[ty] ?? t.muted },
              text: subset.map(a => `${a.security_term}${a.offering_amt ? ` · ${fmtAmount(a.offering_amt)}` : ""}`),
              hovertemplate: "%{x}<br>%{text}<extra>%{y}</extra>",
            };
          })}
          layout={{
            height: 320, ...L,
            yaxis: { gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            shapes: [{ type: "line", x0: today, x1: today, yref: "paper", y0: 0, y1: 1, line: { color: t.accent, width: 1, dash: "dot" as const } }],
          }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Tab 7: Surprise Tracker
// ───────────────────────────────────────────────────────────────────
type SurpriseType = "change" | "yoy" | "mom" | "level";
type SurpriseRow = { date: string; indicator: string; actual: number; consensus: number; surprise: number; unit: string; z: number };

function SurpriseTracker({ series, isLoading, t, L }: { series?: Record<string, FredRow[]>; isLoading: boolean; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  if (isLoading || !series) return <Spinner />;

  const indicators: Array<[string, string, string, SurpriseType]> = [
    ["PAYEMS", "Nonfarm Payrolls", "K", "change"],
    ["UNRATE", "Unemployment Rate", "%", "level"],
    ["CPIAUCSL", "CPI", "%", "yoy"],
    ["RSAFS", "Retail Sales", "%", "mom"],
    ["INDPRO", "Industrial Production", "%", "mom"],
    ["UMCSENT", "Consumer Sentiment", "pts", "level"],
    ["HOUST", "Housing Starts", "K", "level"],
  ];

  const rows: SurpriseRow[] = [];
  for (const [sid, name, unit, kind] of indicators) {
    const arr = (series[sid] ?? []).slice().sort((a, b) => a.date.localeCompare(b.date));
    const minRequired = kind === "yoy" ? 14 : 4;
    if (arr.length < minRequired) continue;
    const startIdx = kind === "yoy" ? 13 : 3;
    for (let i = startIdx; i < arr.length; i++) {
      let actual: number, consensus: number;
      if (kind === "change") {
        actual = arr[i].value - arr[i - 1].value;
        const priors: number[] = [];
        for (let j = Math.max(1, i - 3); j < i; j++) priors.push(arr[j].value - arr[j - 1].value);
        consensus = priors.reduce((s, v) => s + v, 0) / priors.length;
      } else if (kind === "yoy") {
        actual = (arr[i].value / arr[i - 12].value - 1) * 100;
        consensus = (arr[i - 1].value / arr[i - 13].value - 1) * 100;
      } else if (kind === "mom") {
        actual = (arr[i].value / arr[i - 1].value - 1) * 100;
        const priors: number[] = [];
        for (let j = Math.max(1, i - 3); j < i; j++) priors.push((arr[j].value / arr[j - 1].value - 1) * 100);
        consensus = priors.reduce((s, v) => s + v, 0) / priors.length;
      } else {
        actual = arr[i].value;
        const priors = arr.slice(i - 3, i).map(r => r.value);
        consensus = priors.reduce((s, v) => s + v, 0) / priors.length;
      }
      rows.push({ date: arr[i].date, indicator: name, actual, consensus, surprise: actual - consensus, unit, z: 0 });
    }
  }

  if (rows.length === 0) return <div className="card text-center py-8 text-sm text-text-muted">Not enough data for surprise calculation.</div>;

  // Standardize per-indicator surprises; invert sign for unemployment (lower = better)
  const byInd = new Map<string, SurpriseRow[]>();
  for (const r of rows) {
    const list = byInd.get(r.indicator) ?? [];
    list.push(r);
    byInd.set(r.indicator, list);
  }
  for (const [ind, list] of byInd) {
    const mean = list.reduce((s, r) => s + r.surprise, 0) / list.length;
    const std = Math.sqrt(list.reduce((s, r) => s + (r.surprise - mean) ** 2, 0) / list.length);
    const invert = ind === "Unemployment Rate" ? -1 : 1;
    for (const r of list) r.z = std > 1e-10 ? (r.surprise / std) * invert : 0;
  }

  // Latest per indicator
  const latestByInd = new Map<string, SurpriseRow>();
  for (const r of rows) {
    const cur = latestByInd.get(r.indicator);
    if (!cur || r.date > cur.date) latestByInd.set(r.indicator, r);
  }
  const latestSorted = [...latestByInd.values()].sort((a, b) => b.surprise - a.surprise);

  // Monthly aggregate index
  const byMonth = new Map<string, number[]>();
  for (const r of rows) {
    const ym = r.date.slice(0, 7);
    const list = byMonth.get(ym) ?? [];
    list.push(r.z);
    byMonth.set(ym, list);
  }
  const months = Array.from(byMonth.keys()).sort();
  const idxVals = months.map(m => {
    const list = byMonth.get(m)!;
    return list.reduce((s, v) => s + v, 0) / list.length;
  });

  // 3-month streak
  const recent = idxVals.slice(-3);
  const streakPos = recent.length >= 3 && recent.every(v => v > 0);
  const streakNeg = recent.length >= 3 && recent.every(v => v < 0);

  // Heatmap: indicator × month (last 6 months)
  const last6 = months.slice(-6);
  const indicatorLabels = Array.from(byInd.keys());
  const heatmap = indicatorLabels.map(ind => last6.map(m => {
    const match = rows.find(r => r.indicator === ind && r.date.slice(0, 7) === m);
    return match ? match.z : 0;
  }));

  return (
    <div className="space-y-4">
      <div className="text-sm text-text-muted">
        Compares actual economic releases to a rolling 3-month average (a consensus proxy — real survey consensus is paywalled). Positive = beat, negative = miss. Unemployment is inverted (lower = beat).
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Latest Release Surprises</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {latestSorted.map(r => {
            const invert = r.indicator === "Unemployment Rate";
            const beat = invert ? r.surprise < 0 : r.surprise > 0;
            const label = r.surprise === 0 ? "MET" : beat ? "BEAT" : "MISS";
            const color = label === "BEAT" ? t.gain : label === "MISS" ? t.loss : t.hv20;
            return (
              <div key={r.indicator} className="border border-border rounded-md p-3 text-center">
                <div className="text-[10px] text-text-muted">{r.indicator}</div>
                <div className="text-lg font-bold" style={{ color }}>{label}</div>
                <div className="text-[10px] text-text-muted">Actual: {r.actual.toFixed(1)}{r.unit} · Exp: {r.consensus.toFixed(1)}{r.unit}</div>
                <div className="text-[11px]" style={{ color }}>{r.surprise >= 0 ? "+" : ""}{r.surprise.toFixed(2)} surprise</div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Economic Surprise Index</div>
        <Plot
          data={[{
            type: "bar" as const, x: months, y: idxVals,
            marker: { color: idxVals.map(v => v > 0 ? t.gain : t.loss) },
            hovertemplate: "%{x}: %{y:.2f}σ<extra></extra>",
          }]}
          layout={{
            height: 320, ...L,
            yaxis: { title: "Surprise Index (z-score)", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 0, y1: 0, line: { color: t.text, width: 1 } }],
            hovermode: "x unified",
          }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
        {streakPos && <div className="text-sm text-gain mt-2"><strong>3-month positive streak.</strong> Economy consistently beating expectations — analysts may be behind the curve.</div>}
        {streakNeg && <div className="text-sm text-loss mt-2"><strong>3-month negative streak.</strong> Economy consistently missing — consider defensive positioning.</div>}
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Surprise Heatmap by Indicator</div>
        <Plot
          data={[{
            type: "heatmap" as const,
            z: heatmap, x: last6, y: indicatorLabels,
            colorscale: "RdYlGn", zmid: 0,
            text: heatmap.map(row => row.map(v => v.toFixed(2))), texttemplate: "%{text}", textfont: { size: 10 },
            hovertemplate: "%{y} · %{x}: %{z:.2f}σ<extra></extra>",
          }]}
          layout={{ height: 280, ...L, margin: { l: 150, r: 20, t: 10, b: 40 } }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
        <div className="text-[11px] text-text-muted mt-1">
          Green = beat, red = miss. Rows that stay one color suggest systematic consensus error.
        </div>
      </div>
    </div>
  );
}

function Spinner() {
  return <div className="card text-center py-10"><div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" /></div>;
}
