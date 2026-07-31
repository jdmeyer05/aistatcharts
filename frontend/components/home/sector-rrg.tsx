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
import { fetchSectorRrg, type SectorRrg, type RrgQuadrant } from "@/lib/api";

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
  const span = Math.max(
    1.5,
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
      // Tail first so the head dot draws on top of it.
      {
        x: r.tail.map((p) => p.ratio),
        y: r.tail.map((p) => p.mom),
        type: "scatter" as const,
        mode: "lines" as const,
        line: { color: c, width: 1, shape: "spline" as const },
        opacity: 0.4,
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
            Relative strength vs momentum against the S&amp;P 500 · {d?.tail_weeks ?? 8}-week trail
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
          <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
            <div className="md:col-span-3 min-w-0">
          <Plot
            data={traces}
            layout={{
              height: 340,
              ...L,
              xaxis: { title: "Relative strength →", range: [lo, hi], gridcolor: t.grid, zeroline: false },
              yaxis: { title: "Momentum →", range: [lo, hi], gridcolor: t.grid, zeroline: false },
              showlegend: false,
              // Quadrant dividers at 100/100 — the only reference lines that
              // carry meaning here, so they're the only ones drawn.
              shapes: [
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
            style={{ width: "100%" }}
          />
            </div>

            <div className="md:col-span-2 grid grid-cols-2 md:grid-cols-1 gap-2 content-start min-w-0">
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
                <span className="text-text font-semibold">Limits.</span> This reconstructs the RRG method;
                the original JdK RS-Ratio and RS-Momentum are proprietary and unpublished. Quadrants and
                rotation behave the same, but the absolute values will not tie out against a licensed RRG
                terminal, which is why they are shown unitless. Sectors are also relative to each other by
                construction — in a broad selloff something still has to be Leading.
              </p>
            </div>
          </details>

          <div className="text-[0.55rem] text-text-muted border-t border-border pt-2">
            Data through {d.data_asof} · benchmark {d.benchmark}
            {d.unavailable && d.unavailable.length > 0 && ` · unavailable: ${d.unavailable.join(", ")}`}
          </div>
        </>
      )}
    </div>
  );
}
