"use client";

/**
 * Sector Relative Rotation Graph.
 *
 * Strength on x, momentum on y, both centred on 100 (= in line with the S&P).
 * Quadrants rotate clockwise when rotation is healthy:
 *
 *   Improving (top-left) → Leading (top-right)
 *   Lagging (bottom-left) ← Weakening (bottom-right)
 *
 * The tail is the point of the chart. A dot sitting in Leading is already-known
 * information; a dot curling out of Lagging toward Improving is the early read.
 *
 * COLOUR: quadrant membership is a STATUS encoding, not a categorical series
 * set — four reserved states with fixed meaning. Sector identity is carried by
 * the direct label on every dot, so hue never has to distinguish 11 things
 * (which no palette can do accessibly).
 */

import Link from "next/link";
import { useTheme } from "next-themes";
import { useQuery } from "@tanstack/react-query";
import { Plot } from "@/components/plot";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { fetchSectorRrg, type SectorRrg, type RrgQuadrant, type RrgMeasure } from "@/lib/api";
import { ordinal } from "@/lib/home-constants";

/** Where a reading sits in its own history, in words. An extreme is the whole
 *  point of the measure, so 0 and 100 are named rather than printed as "0th". */
function rankPhrase(m: RrgMeasure): string | null {
  if (m.pctile == null) return null;
  if (m.pctile <= 0) return `lowest of ${m.n_history} weeks`;
  if (m.pctile >= 100) return `highest of ${m.n_history} weeks`;
  return `${ordinal(m.pctile)} pctile of ${m.n_history} weeks`;
}

function RegimeTile({ title, m, fmt, note }: {
  title: string; m?: RrgMeasure; fmt: (v: number) => string; note: string;
}) {
  if (!m) return null;
  const rank = rankPhrase(m);
  const c = m.context;
  return (
    <div className="border border-border rounded p-2 min-w-0">
      <div className="text-[0.55rem] uppercase tracking-wider text-text-muted">{title}</div>
      <div className="flex items-baseline gap-1.5 mt-0.5 flex-wrap">
        <span className="text-sm font-bold font-data tabular-nums">{fmt(m.value)}</span>
        {m.band && <span className="text-[0.6rem] text-text">{m.band}</span>}
      </div>
      {rank && <div className="text-[0.55rem] text-text-muted mt-0.5">{rank}</div>}
      <div className="text-[0.55rem] text-text-muted mt-1 leading-snug">{note}</div>
      {/* Every stat can be null independently, and a bare "have run alongside
          (n=58)" is worse than saying nothing. */}
      {c && (c.realized_vol != null || c.avg_sector_corr != null || c.trend_vs_50dma != null) && (
        <div className="text-[0.55rem] text-text-muted mt-1 leading-snug border-t border-border pt-1">
          Weeks at this level have run alongside{" "}
          {c.realized_vol != null && <span className="text-text tabular-nums">{c.realized_vol.toFixed(1)}% vol</span>}
          {c.avg_sector_corr != null && <>, <span className="text-text tabular-nums">{c.avg_sector_corr.toFixed(2)} correlation</span></>}
          {c.trend_vs_50dma != null && <>, S&amp;P <span className="text-text tabular-nums">{c.trend_vs_50dma >= 0 ? "+" : ""}{c.trend_vs_50dma.toFixed(1)}%</span> vs its 50-day</>}
          {" "}(n={c.n}).
        </div>
      )}
    </div>
  );
}

const QUADRANTS: { key: RrgQuadrant; label: string; blurb: string }[] = [
  { key: "leading", label: "Leading", blurb: "strong and still accelerating" },
  { key: "weakening", label: "Weakening", blurb: "still strong, losing momentum" },
  { key: "lagging", label: "Lagging", blurb: "weak and still decelerating" },
  { key: "improving", label: "Improving", blurb: "weak but momentum turning up" },
];

export default function SectorRrgCard() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const isDark = resolvedTheme === "dark";

  // 8 WEEKLY points. This was 4 when the board was daily and the tail meant
  // four weeks of trading days; a 4-point weekly tail is barely a curve.
  const q = useQuery<SectorRrg>({
    queryKey: ["sector-rrg", 8],
    queryFn: () => fetchSectorRrg(8),
    refetchInterval: 30 * 60_000,
    staleTime: 25 * 60_000,
  });

  const d = q.data;

  // Status palette — reserved states, not a categorical ramp.
  const qColor: Record<RrgQuadrant, string> = {
    leading: t.gain,
    weakening: t.spot,
    lagging: t.loss,
    improving: t.accent,
  };

  const rows = d?.rows ?? [];

  // Square the axes around 100. An RRG is only readable if both axes share a
  // scale — an auto-fitted range would distort the quadrant geometry and make
  // a small momentum move look like a large one.
  // Floor is 15, not 1.5: values are scaled `100 + 10*z` to match the canonical
  // RRG, so a quiet board spans ~10 units where it used to span ~1.
  const span = Math.max(
    15,
    ...rows.flatMap((r) => [
      Math.abs(r.ratio - 100), Math.abs(r.mom - 100),
      ...r.tail.flatMap((p) => [Math.abs(p.ratio - 100), Math.abs(p.mom - 100)]),
    ])
  ) * 1.15;
  const lo = 100 - span;
  const hi = 100 + span;

  const traces = rows.flatMap((r) => {
    const c = qColor[r.quadrant];
    return [
      // Tail first so the head dot draws on top of it. Graduated marker size
      // along the path tapers it toward the present, which is what makes the
      // direction of travel readable without an arrowhead.
      {
        x: r.tail.map((p) => p.ratio),
        y: r.tail.map((p) => p.mom),
        type: "scatter" as const,
        mode: "lines+markers" as const,
        line: { color: c, width: 1.25, shape: "spline" as const },
        marker: {
          color: c,
          size: r.tail.map((_, i) => 2 + (i / Math.max(1, r.tail.length - 1)) * 3),
          line: { width: 0 },
        },
        opacity: 0.45,
        hoverinfo: "skip" as const,
        showlegend: false,
      },
      {
        x: [r.ratio],
        y: [r.mom],
        type: "scatter" as const,
        mode: "markers+text" as const,
        marker: { color: c, size: 10, line: { color: t.plot, width: 1.5 } },
        text: [r.symbol],
        textposition: "top center" as const,
        textfont: { size: 9, color: t.text },
        hovertemplate:
          `<b>${r.label}</b> (${r.symbol})<br>Strength %{x:.2f} · Momentum %{y:.2f}<br>` +
          `${r.quadrant}${r.prev_quadrant !== r.quadrant ? ` (from ${r.prev_quadrant})` : ""}<extra></extra>`,
        showlegend: false,
      },
    ];
  });

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-bold uppercase tracking-wider text-accent">
            Sector Rotation
          </h2>
          <div className="text-[0.6rem] text-text-muted mt-0.5">
            Weekly · relative strength vs momentum against the S&amp;P 500 · {d?.tail_weeks ?? 8}-week trail
          </div>
        </div>
        <Link href="/sectors" className="text-[0.6rem] text-text-muted hover:text-accent whitespace-nowrap">
          Sector analysis →
        </Link>
      </div>

      {q.isLoading && (
        <div className="py-10 text-center">
          <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {!q.isLoading && !d?.available && (
        <div className="py-4 flex items-baseline gap-2 flex-wrap">
          <p className="text-xs text-text-muted">
            {q.isError ? "Couldn't load sector rotation." : `Rotation unavailable${d?.reason ? ` — ${d.reason}` : ""}.`}
          </p>
          {q.isError && (
            <button type="button" onClick={() => q.refetch()} disabled={q.isFetching}
              className="text-[0.65rem] text-accent hover:underline disabled:opacity-50">
              {q.isFetching ? "Retrying…" : "Retry"}
            </button>
          )}
        </div>
      )}

      {d?.available && (
        <>
          {/* Regime strip leads the card. These are the measures that persist;
              the quadrant tally below changes far faster than the environment
              it describes, so it is context for the chart, not the headline. */}
          {d.regime && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <RegimeTile
                title="Leadership tilt" m={d.regime.tilt}
                fmt={(v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}`}
                note="Defensive minus cyclical relative strength. Above zero, the low-beta half leads."
              />
              <RegimeTile
                title="Dispersion" m={d.regime.dispersion}
                fmt={(v) => v.toFixed(1)}
                note="Mean distance of the sectors from the benchmark point. Wide means leadership is spread."
              />
              <RegimeTile
                title="Sector correlation" m={d.regime.correlation}
                fmt={(v) => v.toFixed(2)}
                note="Average pairwise, 60 daily returns. Measured directly — the rotation picture does not stand in for it."
              />
            </div>
          )}

          <div className="space-y-3">
            {/* The wrapper owns the height and clips, the chart fills it.
                Previously the height lived only in `layout` and the wrapper
                reserved none, so Plotly's SVG sat in a slot that measured
                shorter than the graph and painted straight over the next
                sibling — the quadrant boxes below. `autosize` + a 100% height
                inside a bounded, clipped box is the combination that keeps a
                responsive Plotly chart inside its own space. */}
            <div className="min-w-0 h-[300px] overflow-hidden">
          <Plot
            data={traces}
            layout={{
              autosize: true,
              ...L,
              xaxis: { title: "Relative strength →", range: [lo, hi], gridcolor: t.grid, zeroline: false },
              yaxis: { title: "Momentum →", range: [lo, hi], gridcolor: t.grid, zeroline: false },
              showlegend: false,
              // Quadrant dividers at 100/100 — the only reference lines that
              // carry meaning here, so they're the only ones drawn.
              shapes: [
                // Quadrant tints, drawn below everything. Kept very low alpha:
                // they orient the eye without competing with the marks.
                { type: "rect", x0: 100, x1: hi, y0: 100, y1: hi, layer: "below",
                  fillcolor: t.gain, opacity: isDark ? 0.07 : 0.05, line: { width: 0 } },
                { type: "rect", x0: 100, x1: hi, y0: lo, y1: 100, layer: "below",
                  fillcolor: t.spot, opacity: isDark ? 0.07 : 0.05, line: { width: 0 } },
                { type: "rect", x0: lo, x1: 100, y0: lo, y1: 100, layer: "below",
                  fillcolor: t.loss, opacity: isDark ? 0.07 : 0.05, line: { width: 0 } },
                { type: "rect", x0: lo, x1: 100, y0: 100, y1: hi, layer: "below",
                  fillcolor: t.accent, opacity: isDark ? 0.07 : 0.05, line: { width: 0 } },
                { type: "line", x0: 100, x1: 100, y0: lo, y1: hi, line: { color: t.muted, width: 1, dash: "dot" } },
                { type: "line", x0: lo, x1: hi, y0: 100, y1: 100, line: { color: t.muted, width: 1, dash: "dot" } },
              ],
              annotations: [
                { x: hi, y: hi, text: "LEADING", showarrow: false, xanchor: "right", yanchor: "top",
                  font: { size: 8, color: t.gain }, opacity: isDark ? 0.75 : 0.6 },
                { x: hi, y: lo, text: "WEAKENING", showarrow: false, xanchor: "right", yanchor: "bottom",
                  font: { size: 8, color: t.spot }, opacity: isDark ? 0.75 : 0.6 },
                { x: lo, y: lo, text: "LAGGING", showarrow: false, xanchor: "left", yanchor: "bottom",
                  font: { size: 8, color: t.loss }, opacity: isDark ? 0.75 : 0.6 },
                { x: lo, y: hi, text: "IMPROVING", showarrow: false, xanchor: "left", yanchor: "top",
                  font: { size: 8, color: t.accent }, opacity: isDark ? 0.75 : 0.6 },
              ],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%", height: "100%" }}
          />
            </div>

            <div className="grid grid-cols-2 gap-2 content-start min-w-0">
            {QUADRANTS.map(({ key, label, blurb }) => {
              const members = rows.filter((r) => r.quadrant === key);
              return (
                <div key={key} className="border border-border rounded p-2">
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: qColor[key] }} />
                    <span className="text-[0.6rem] font-bold uppercase tracking-wider text-text">{label}</span>
                    <span className="text-[0.6rem] text-text-muted ml-auto tabular-nums">{members.length}</span>
                  </div>
                  <div className="text-[0.55rem] text-text-muted mt-0.5 leading-snug">{blurb}</div>
                  <div className="text-[0.62rem] text-text mt-1 leading-snug">
                    {members.length ? members.map((m) => m.symbol).join(" · ") : "—"}
                  </div>
                </div>
              );
            })}
            </div>
          </div>

          <details className="group">
            <summary className="text-[0.62rem] text-text-muted hover:text-accent cursor-pointer select-none list-none flex items-center gap-1">
              <span className="transition-transform group-open:rotate-90">▸</span>
              How to read this
            </summary>
            <div className="text-[0.65rem] text-text-muted leading-relaxed mt-2 space-y-1.5 pl-3 border-l border-border">
              <p>
                <span className="text-text font-semibold">Both axes are relative to the S&amp;P.</span> 100
                means in line with the index. Right of centre is outperforming; above centre means that
                out/under-performance is accelerating. A sector can be strong and fading at once — that is
                the Weakening quadrant, and it is the one plain performance tables hide.
              </p>
              <p>
                <span className="text-text font-semibold">Watch the trail, not the dot.</span> Healthy
                rotation travels clockwise: Improving → Leading → Weakening → Lagging. A name deep in
                Lagging but curling upward is an earlier signal than one already sitting in Leading, where
                the move is largely behind you.
              </p>
              <p>
                <span className="text-text font-semibold">This describes the environment, it does not
                forecast the session.</span> Rotation state was tested directly against the next
                session&apos;s direction, range and trend-efficiency over 1,829 day-pairs. Direction is null
                at every quintile against a 54.5% baseline; trend-efficiency is null; the one range result
                that looked real failed a split-half with the opposite sign in the earlier period.
                StockCharts says the same of the licensed original — it is &ldquo;not a trading system, and
                there are no predefined trading rules or signals.&rdquo; What the quadrants and the regime
                measures do carry is a description of the conditions you are trading in.
              </p>
              <p>
                <span className="text-text font-semibold">Weekly, because daily was too fast.</span> A
                sector held a quadrant for a median two days on the daily build against two weeks weekly,
                and 1.99 of 11 sectors changed quadrant per day versus 0.90/day equivalent — the daily
                board relabelled 2–5× faster than the environment it claimed to describe.
              </p>
              <p>
                <span className="text-text font-semibold">Limits.</span> This reconstructs the RRG method;
                the original JdK RS-Ratio and RS-Momentum are proprietary and unpublished, and both
                StockCharts and relativerotationgraphs.com decline to publish the formula. Quadrants and
                rotation behave the same, but the absolute values will not tie out against a licensed RRG
                terminal. Sectors are also relative to each other by construction — in a broad selloff
                something still has to be Leading. The environment figures on the tiles are
                co-occurrences, and part of that link is definitional: defensive leadership is what a
                falling market looks like.
              </p>
            </div>
          </details>

          <div className="text-[0.55rem] text-text-muted border-t border-border pt-2">
            Data through {d.data_asof} · benchmark {d.benchmark}
            {d.week_ending && ` · week ending ${d.week_ending}`}
            {d.week_complete === false && " (partial week — moves until Friday's close)"}
            {d.unavailable && d.unavailable.length > 0 && ` · unavailable: ${d.unavailable.join(", ")}`}
          </div>
        </>
      )}
    </div>
  );
}
