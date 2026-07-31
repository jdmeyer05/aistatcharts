"use client";

/**
 * CTA Positioning — projected trend-follower flows for the S&P 500.
 *
 * Mirrors the layout desk research uses for this readout (Goldman / Nomura
 * publish the same shape): a fan of exposure paths under ±1σ/±2σ terminal
 * price scenarios, with the horizon flows and the trend pivot ladder called
 * out alongside.
 *
 * UNITS: exposure is in model points (−100..100), NOT notional dollars. The
 * published desk versions quote $bn by scaling exposure to an assumed
 * trend-following AUM. We don't have that scalar, so it is deliberately not
 * applied — an invented AUM would put fabricated precision on the y-axis.
 * The shape of the curve and the sign of the flow are the signal here.
 *
 * The model is our own reconstruction (SMA + breakout + momentum ensemble in
 * src/cta_model.py), not a redistribution of anyone's proprietary estimates.
 */

import Link from "next/link";
import { useTheme } from "next-themes";
import { useQuery } from "@tanstack/react-query";
import { Plot } from "@/components/plot";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { fetchCtaFlows, type CtaBias, type CtaFlowBoard } from "@/lib/api";

/** Plot order is deliberate: most bearish first so the legend reads top-down
 *  in the same order the lines stack on the chart. */
const SCENARIO_ROWS = [
  { key: "down_2sig", label: "Down 2σ" },
  { key: "down_1sig", label: "Down 1σ" },
  { key: "flat", label: "Flat" },
  { key: "up_1sig", label: "Up 1σ" },
  { key: "up_2sig", label: "Up 2σ" },
] as const;

const PIVOT_ROWS = [
  { key: "short_term", label: "Short-term" },
  { key: "medium_term", label: "Mid-term" },
  { key: "long_term", label: "Long-term" },
] as const;

function biasLabel(b?: CtaBias): string {
  switch (b) {
    case "all_buying": return "Buying in all scenarios";
    case "all_selling": return "Selling in all scenarios";
    case "neutral": return "Neutral";
    case "mixed": return "Direction-dependent";
    default: return "—";
  }
}

function biasClass(b?: CtaBias): string {
  switch (b) {
    case "all_buying": return "bg-gain/15 text-gain";
    case "all_selling": return "bg-loss/15 text-loss";
    case "neutral": return "bg-border text-text-muted";
    default: return "bg-accent/15 text-accent";
  }
}

/** Signed formatting — the sign is the whole point of a flow number. */
function signed(n: number, digits = 1): string {
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}`;
}

function flowClass(n: number): string {
  if (n > 1) return "text-gain";
  if (n < -1) return "text-loss";
  return "text-text-muted";
}

export default function CtaFlows() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const q = useQuery<CtaFlowBoard>({
    queryKey: ["cta-flows", "13874A"],
    queryFn: () => fetchCtaFlows("13874A"),
    refetchInterval: 10 * 60_000,
    staleTime: 5 * 60_000,
  });

  const d = q.data;

  // Scenario colors run loss → muted → gain so direction is readable without
  // consulting the legend.
  const lineColor: Record<string, string> = {
    down_2sig: t.loss,
    down_1sig: t.hv20,
    flat: t.muted,
    up_1sig: t.accent,
    up_2sig: t.gain,
  };

  const traces =
    d?.available
      ? SCENARIO_ROWS.flatMap(({ key, label }) => {
          const s = d.scenarios?.[key];
          if (!s) return [];
          return [{
            x: [0, ...s.path.map((p) => p.day)],
            // Anchor every path at 0 on day 0: these are *changes* in exposure
            // from today, so they must share an origin to be comparable.
            y: [0, ...s.path.map((p) => p.delta_exposure)],
            type: "scatter" as const,
            mode: "lines" as const,
            name: label,
            line: {
              color: lineColor[key],
              width: key === "flat" ? 2.5 : 2,
              dash: key === "flat" ? ("dash" as const) : undefined,
            },
            hovertemplate: `${label}: %{y:.1f} pts<br>day %{x}<extra></extra>`,
          }];
        })
      : [];

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-sm font-bold uppercase tracking-wider text-accent">
              CTA Positioning — S&amp;P 500
            </h2>
            {d?.available && (
              <>
                <span className={`px-2 py-0.5 rounded text-[0.6rem] font-bold ${biasClass(d.bias_1w)}`}>
                  1W: {biasLabel(d.bias_1w)}
                </span>
                <span className={`px-2 py-0.5 rounded text-[0.6rem] font-bold ${biasClass(d.bias_1m)}`}>
                  1M: {biasLabel(d.bias_1m)}
                </span>
              </>
            )}
          </div>
          <div className="text-[0.6rem] text-text-muted mt-0.5">
            Projected trend-follower flow under ±1σ/±2σ moves · exposure points, not $bn
          </div>
        </div>
        <Link href="/positioning" className="text-[0.6rem] text-text-muted hover:text-accent whitespace-nowrap">
          Full model →
        </Link>
      </div>

      {q.isLoading && (
        <div className="py-10 text-center">
          <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {!q.isLoading && !d?.available && (
        <p className="text-xs text-text-muted py-4">
          CTA model unavailable{d?.reason ? ` — ${d.reason}` : ""}.
        </p>
      )}

      {d?.available && (
        <>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[0.65rem] text-text-muted">
            <span>Spot <span className="text-text font-semibold tabular-nums">{d.last_price.toLocaleString()}</span></span>
            <span>Current exposure <span className="text-text font-semibold tabular-nums">{signed(d.current_exposure)}</span></span>
            <span>1σ over {d.horizon_days}d <span className="text-text font-semibold tabular-nums">±{d.sigma_1_pct}%</span></span>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="lg:col-span-2">
              <Plot
                data={traces}
                layout={{
                  height: 300,
                  ...L,
                  yaxis: { title: "Δ exposure (pts)", gridcolor: t.grid, zeroline: true, zerolinecolor: t.muted },
                  xaxis: { title: `Business days ahead (${d.horizon_days}d horizon)`, gridcolor: t.grid },
                  hovermode: "x unified" as const,
                  legend: { orientation: "h" as const, y: -0.28, bgcolor: "transparent" },
                  showlegend: true,
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>

            <div className="space-y-3">
              {/* Terminal flows — same numbers the plotted paths end on. */}
              <div className="border border-border rounded p-2.5">
                <div className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted mb-1.5">
                  Flow by horizon
                </div>
                <table className="w-full text-[0.68rem] tabular-nums">
                  <thead>
                    <tr className="text-text-muted">
                      <th className="text-left font-medium pb-1">Tape</th>
                      <th className="text-right font-medium pb-1">1W</th>
                      <th className="text-right font-medium pb-1">1M</th>
                    </tr>
                  </thead>
                  <tbody>
                    {SCENARIO_ROWS.map(({ key, label }) => {
                      const w = d.terminal?.["1w"]?.[key]?.delta_exposure;
                      const m = d.terminal?.["1m"]?.[key]?.delta_exposure;
                      return (
                        <tr key={key}>
                          <td className="text-text py-0.5">{label}</td>
                          <td className={`text-right ${typeof w === "number" ? flowClass(w) : ""}`}>
                            {typeof w === "number" ? signed(w) : "—"}
                          </td>
                          <td className={`text-right ${typeof m === "number" ? flowClass(m) : ""}`}>
                            {typeof m === "number" ? signed(m) : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Pivot ladder — the prices the trend components flip at. */}
              <div className="border border-border rounded p-2.5">
                <div className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted mb-1.5">
                  Key SPX pivot levels
                </div>
                <div className="space-y-1">
                  {PIVOT_ROWS.map(({ key, label }) => {
                    const p = d.pivots?.[key];
                    if (!p) return null;
                    return (
                      <div key={key} className="flex items-baseline justify-between text-[0.68rem]">
                        <span className="text-text-muted">{label}</span>
                        <span className="tabular-nums">
                          <span className="text-text font-semibold">{p.level.toLocaleString()}</span>
                          <span className={`ml-1.5 ${p.distance_pct < 0 ? "text-loss" : "text-gain"}`}>
                            {signed(p.distance_pct, 2)}%
                          </span>
                        </span>
                      </div>
                    );
                  })}
                </div>
                <div className="text-[0.55rem] text-text-muted mt-1.5 leading-snug">
                  Distance from spot. Crossing flips that trend component.
                </div>
              </div>
            </div>
          </div>

          <p className="text-[0.55rem] text-text-muted leading-snug border-t border-border pt-2">
            Independent reconstruction of trend-follower positioning (SMA + breakout + momentum
            ensemble), not a redistribution of any bank&apos;s proprietary estimates. Exposure is
            model points, not notional — desk versions quote $bn by assuming a trend AUM we
            don&apos;t have.
          </p>
        </>
      )}
    </div>
  );
}
