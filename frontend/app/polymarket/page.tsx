"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import {
  fetchPolymarket,
  fetchPolymarketHistory,
  type PolymarketEvent,
  type PolymarketHistoryPoint,
} from "@/lib/api";
import { AIInterpretation } from "@/components/ai-interpretation";
import { Plot } from "@/components/plot";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";

const CATEGORY_ORDER = [
  "Fed Rates",
  "Economy",
  "Geopolitics",
  "Politics",
  "Crypto",
  "Sports",
  "Other",
] as const;

type Category = typeof CATEGORY_ORDER[number];
type Tab = Category | "All" | "Actionable" | "Volume";

const TABS: Tab[] = ["All", "Actionable", "Volume", ...CATEGORY_ORDER];

const CATEGORY_COLORS: Record<string, string> = {
  "Fed Rates": "#00d1ff",
  Economy: "#3fb950",
  Geopolitics: "#f85149",
  Politics: "#a78bfa",
  Crypto: "#f59e0b",
  Sports: "#db6d28",
  Other: "#6e7681",
};

function fmtDollars(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

function eventBestActionability(ev: PolymarketEvent): number {
  if (!ev.outcomes.length) return 0;
  return Math.max(...ev.outcomes.map((o) => o.actionability ?? 0));
}

function filterAndSort(events: PolymarketEvent[], tab: Tab): PolymarketEvent[] {
  let list = [...events];
  if (tab === "Actionable") {
    list = list
      .filter((e) => eventBestActionability(e) >= 10)
      .sort((a, b) => eventBestActionability(b) - eventBestActionability(a));
  } else if (tab === "Volume") {
    list.sort((a, b) => b.volume_24h - a.volume_24h);
  } else if (tab === "All") {
    list.sort((a, b) => b.volume_24h - a.volume_24h);
  } else {
    list = list
      .filter((e) => (e.category ?? "Other") === tab)
      .sort((a, b) => b.volume_24h - a.volume_24h);
  }
  return list;
}

/* ─── Event card ────────────────────────────────────────────── */

function OutcomeBar({
  label,
  pct,
  maxPct,
  isFocused,
  onClick,
}: {
  label: string;
  pct: number;
  maxPct: number;
  isFocused: boolean;
  onClick: () => void;
}) {
  const width = Math.max(2, (pct / Math.max(maxPct, 100)) * 100);
  const isYes = pct >= 50;
  return (
    <button
      onClick={onClick}
      className={`w-full text-left group transition-colors ${
        isFocused ? "bg-surface-alt" : "hover:bg-surface-alt/60"
      } rounded-md px-2 py-1.5`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="text-xs text-text truncate" title={label}>
          {label}
        </div>
        <div
          className={`text-xs font-bold tabular-nums shrink-0 ${
            isYes ? "text-gain" : "text-loss"
          }`}
        >
          {pct.toFixed(1)}%
        </div>
      </div>
      <div className="h-1.5 bg-border rounded-full overflow-hidden">
        <div
          className={`h-full ${isYes ? "bg-gain" : "bg-loss"}`}
          style={{ width: `${width}%` }}
        />
      </div>
    </button>
  );
}

function EventCard({ event }: { event: PolymarketEvent }) {
  const [focusedIdx, setFocusedIdx] = useState<number | null>(null);
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const focused = focusedIdx === null ? event.outcomes[0] : event.outcomes[focusedIdx];
  const maxPct = Math.max(...event.outcomes.map((o) => o.yes_pct));

  const historyQ = useQuery({
    queryKey: ["poly-hist", focused?.token_id],
    queryFn: () => fetchPolymarketHistory(focused!.token_id!, "1m"),
    enabled: !!focused?.token_id,
    staleTime: 5 * 60 * 1000,
  });

  const category = event.category ?? "Other";
  const categoryColor = CATEGORY_COLORS[category] ?? CATEGORY_COLORS.Other;

  return (
    <div className="card card-compact space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span
              className="text-[0.55rem] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded"
              style={{ backgroundColor: `${categoryColor}20`, color: categoryColor }}
            >
              {category}
            </span>
            <span className="text-[0.6rem] text-text-muted tabular-nums">
              24h {fmtDollars(event.volume_24h)} · liq {fmtDollars(event.liquidity)}
            </span>
          </div>
          <a
            href={event.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm font-semibold text-text hover:text-accent transition-colors line-clamp-2"
            title={event.title}
          >
            {event.title}
          </a>
        </div>
      </div>

      <div className="space-y-1">
        {event.outcomes.map((o, i) => (
          <OutcomeBar
            key={i}
            label={o.label}
            pct={o.yes_pct}
            maxPct={maxPct}
            isFocused={focusedIdx === i || (focusedIdx === null && i === 0)}
            onClick={() => setFocusedIdx(i)}
          />
        ))}
      </div>

      {focused?.token_id && (
        <div className="border-t border-border pt-2">
          <div className="text-[0.6rem] text-text-muted uppercase tracking-wider mb-1">
            {focused.label} — history
          </div>
          {historyQ.isLoading ? (
            <div className="h-20 flex items-center justify-center text-[0.65rem] text-text-muted">
              Loading…
            </div>
          ) : historyQ.data?.points && historyQ.data.points.length > 1 ? (
            <Plot
              data={[
                {
                  x: historyQ.data.points.map((p: PolymarketHistoryPoint) => new Date(p.t * 1000)),
                  y: historyQ.data.points.map((p: PolymarketHistoryPoint) => p.p),
                  type: "scatter",
                  mode: "lines",
                  line: { color: categoryColor, width: 1.5 },
                  fill: "tozeroy",
                  fillcolor: `${categoryColor}15`,
                  hovertemplate: "%{y:.1f}%<br>%{x|%b %d}<extra></extra>",
                },
              ]}
              layout={{
                ...L,
                height: 90,
                margin: { t: 2, b: 18, l: 30, r: 4 },
                xaxis: { showgrid: false, tickfont: { size: 9 }, gridcolor: t.grid },
                yaxis: { ticksuffix: "%", tickfont: { size: 9 }, range: [0, 100], gridcolor: t.grid },
                showlegend: false,
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%", height: 90 }}
            />
          ) : (
            <div className="h-20 flex items-center justify-center text-[0.65rem] text-text-muted">
              No history
            </div>
          )}
          <div className="flex items-center justify-between text-[0.6rem] text-text-muted">
            <span>resolves in {focused.days_out ?? "?"} days</span>
            <span>actionability {focused.actionability?.toFixed(1) ?? "—"}</span>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Page ──────────────────────────────────────────────────── */

export default function PolymarketPage() {
  const [tab, setTab] = useState<Tab>("All");

  const q = useQuery({
    queryKey: ["polymarket-markets"],
    queryFn: fetchPolymarket,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  });

  const markets = q.data?.markets ?? [];

  const counts = useMemo(() => {
    const c: Record<string, number> = { All: markets.length };
    c.Actionable = markets.filter((m) => eventBestActionability(m) >= 10).length;
    c.Volume = markets.length;
    for (const cat of CATEGORY_ORDER) {
      c[cat] = markets.filter((m) => (m.category ?? "Other") === cat).length;
    }
    return c;
  }, [markets]);

  const visible = useMemo(() => filterAndSort(markets, tab), [markets, tab]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Polymarket</h1>
        <p className="text-text-secondary text-sm mt-1">
          Crowd-priced odds on Fed decisions, macro, geopolitics, and politics.
          Ranked by actionability (near-term × uncertainty) and 24-hour volume.
        </p>
      </div>

      {/* AI interpretation card — reads the current snapshot and produces a short
          "what the crowd is pricing" read. Falls back gracefully on cold cache. */}
      {markets.length > 0 && (
        <AIInterpretation
          page="polymarket"
          subject="Current market odds"
          data={{
            markets: markets.slice(0, 20).map((m) => ({
              title: m.title,
              category: m.category ?? "Other",
              volume_24h: m.volume_24h,
              liquidity: m.liquidity,
              outcomes: m.outcomes.map((o) => ({
                label: o.label,
                yes_pct: o.yes_pct,
                days_out: o.days_out,
                actionability: o.actionability,
              })),
            })),
          }}
        />
      )}

      {/* Tabs */}
      <div className="flex flex-wrap gap-1 border-b border-border">
        {TABS.map((t) => {
          const active = t === tab;
          const count = counts[t] ?? 0;
          return (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors rounded-t ${
                active
                  ? "border-b-2 border-accent text-accent"
                  : "text-text-muted hover:text-text"
              }`}
            >
              {t}
              {count > 0 && (
                <span className="ml-1.5 text-[0.6rem] font-normal text-text-muted">
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Error + loading */}
      {q.isError && (
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm flex items-center justify-between">
          <span>Polymarket fetch failed: {(q.error as Error)?.message ?? "unknown"}.</span>
          <button
            onClick={() => q.refetch()}
            className="px-3 py-1 text-xs rounded border border-loss hover:bg-loss/10"
          >
            Retry
          </button>
        </div>
      )}
      {q.isLoading && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching Polymarket odds…</p>
        </div>
      )}

      {/* Event grid */}
      {!q.isLoading && visible.length === 0 && (
        <div className="card text-center py-12 text-sm text-text-muted">
          No markets in this category right now.
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {visible.map((event) => (
          <EventCard key={event.slug} event={event} />
        ))}
      </div>
    </div>
  );
}
