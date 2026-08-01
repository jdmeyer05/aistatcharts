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

/**
 * Plain-language read derived arithmetically from the payload — no model call.
 * The AI panel below gives the richer take, but a user who never clicks it
 * should still be told what the numbers mean, so this always renders.
 */
function plainRead(d: CtaFlowBoard): { headline: string; detail: string } | null {
  const w = d.terminal?.["1w"];
  if (!w) return null;
  const down = w["down_2sig"]?.delta_exposure;
  const up = w["up_2sig"]?.delta_exposure;
  if (typeof down !== "number" || typeof up !== "number") return null;

  // Nearest pivot by absolute distance — the one most likely to fire.
  const pivots = Object.entries(d.pivots ?? {}).filter(([, p]) => p);
  const nearest = pivots.length
    ? pivots.reduce((a, b) => (Math.abs(a[1]!.distance_pct) <= Math.abs(b[1]!.distance_pct) ? a : b))
    : null;
  const nearestLabel = nearest
    ? PIVOT_ROWS.find((r) => r.key === nearest[0])?.label ?? nearest[0]
    : null;

  let headline: string;
  if (d.bias_1w === "all_selling") {
    headline = "Systematic supply in every scenario — trend flow is a headwind regardless of direction.";
  } else if (d.bias_1w === "all_buying") {
    headline = "Systematic demand in every scenario — trend flow is a tailwind regardless of direction.";
  } else if (d.bias_1w === "neutral") {
    headline = "Trend flow is dormant — little mechanical buying or selling on either side.";
  } else {
    // Mixed is the common case and the most misread: it is not "neutral".
    const skew =
      Math.abs(down) > Math.abs(up) * 1.15
        ? "with more force behind a selloff than a rally"
        : Math.abs(up) > Math.abs(down) * 1.15
          ? "with more force behind a rally than a selloff"
          : "roughly symmetrically";
    headline = `Trend flow amplifies whichever way price breaks, ${skew}.`;
  }

  // The quoted deltas are the 1-WEEK terminals, so they must be described with
  // the 1-week sigma. sigma_1_pct covers horizon_days (20d) and would overstate
  // the weekly move by roughly 2x.
  const sig1w = d.sigma_1w_pct;
  const move2sig = typeof sig1w === "number" ? (sig1w * 2).toFixed(2) : null;

  const detail =
    (move2sig
      ? `Over a week, a 2σ drop (−${move2sig}%) implies ${signed(down)} pts of exposure change and a 2σ rally (+${move2sig}%) implies ${signed(up)}.`
      : `Over a week, a 2σ drop implies ${signed(down)} pts of exposure change and a 2σ rally implies ${signed(up)}.`) +
    (nearest && nearestLabel
      ? ` Nearest flip level is the ${nearestLabel.toLowerCase()} pivot at ${nearest[1]!.level.toLocaleString()} ` +
        `(${signed(nearest[1]!.distance_pct, 2)}% away)` +
        (typeof sig1w === "number" && Math.abs(nearest[1]!.distance_pct) <= sig1w
          ? " — inside a 1σ week, so it is live on this horizon."
          : ".")
      : "");

  return { headline, detail };
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
    // Signals are daily-bar based behind a 4h server price cache, so polling
    // faster than this returns identical numbers while re-running the path
    // walk server-side on every call. 30m keeps it fresh without the churn.
    queryKey: ["cta-flows", "13874A"],
    queryFn: () => fetchCtaFlows("13874A"),
    refetchInterval: 30 * 60_000,
    staleTime: 25 * 60_000,
  });

  const d = q.data;
  const read = d?.available ? plainRead(d) : null;

  // Diverging encoding: two hues + a neutral midpoint. Only three colour slots
  // because the meaning is only down / flat / up — sigma magnitude rides on line
  // width and opacity instead. That is legitimate here because the five paths
  // cannot cross (a larger down-move always implies more selling), so vertical
  // position already encodes the ordering unambiguously.
  //
  // A 5-hue version failed CVD validation on the inner-step-vs-gray pair
  // (ΔE 4.4 deutan). This set measures ΔE 13.6 protan / 11.6 deutan, both well
  // clear of the ΔE 8 target, and passes the normal-vision floor at 18.6.
  const SCENARIO_STYLE: Record<string, { color: string; width: number; opacity: number; dash?: "dash" }> = {
    down_2sig: { color: t.loss,  width: 2.5, opacity: 1 },
    down_1sig: { color: t.loss,  width: 1.5, opacity: 0.55 },
    flat:      { color: t.muted, width: 2,   opacity: 0.9, dash: "dash" },
    up_1sig:   { color: t.gain,  width: 1.5, opacity: 0.55 },
    up_2sig:   { color: t.gain,  width: 2.5, opacity: 1 },
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
            // Carry the projected index level so hover answers "at what price?",
            // which is the question that makes a flow number actionable.
            customdata: [d.last_price, ...s.path.map((p) => p.price)],
            type: "scatter" as const,
            mode: "lines" as const,
            name: label,
            line: {
              color: SCENARIO_STYLE[key].color,
              width: SCENARIO_STYLE[key].width,
              dash: SCENARIO_STYLE[key].dash,
            },
            opacity: SCENARIO_STYLE[key].opacity,
            hovertemplate:
              `${label} · day %{x}<br>SPX %{customdata:,.0f}<br>` +
              `Δ exposure %{y:+.1f} pts<extra></extra>`,
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
        <div className="py-4 flex items-baseline gap-2 flex-wrap">
          <p className="text-xs text-text-muted">
            {/* A fetch failure and a model that returned unavailable are
                different problems — say which one happened. */}
            {q.isError
              ? "Couldn't load the CTA model."
              : `CTA model unavailable${d?.reason ? ` — ${d.reason}` : ""}.`}
          </p>
          {q.isError && (
            <button
              type="button"
              onClick={() => q.refetch()}
              disabled={q.isFetching}
              className="text-[0.65rem] text-accent hover:underline disabled:opacity-50"
            >
              {q.isFetching ? "Retrying…" : "Retry"}
            </button>
          )}
        </div>
      )}

      {d?.available && (
        <>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[0.65rem] text-text-muted">
            <span>Spot <span className="text-text font-semibold tabular-nums">{d.last_price.toLocaleString()}</span></span>
            <span>Current exposure <span className="text-text font-semibold tabular-nums">{signed(d.current_exposure)}</span></span>
            {/* Both sigmas, because the table quotes both horizons — showing only
                the 20d one leaves the 1W column unanchored to any price move. */}
            {typeof d.sigma_1w_pct === "number" && (
              <span>1σ week <span className="text-text font-semibold tabular-nums">±{d.sigma_1w_pct}%</span></span>
            )}
            {/* Label by the horizon the number actually describes. sigma_1_pct
                covers horizon_days, which only equals the 1M sigma at the
                default 20 — don't call it "month" if the horizon was changed. */}
            {typeof d.sigma_1m_pct === "number" ? (
              <span>1σ month <span className="text-text font-semibold tabular-nums">±{d.sigma_1m_pct}%</span></span>
            ) : (
              <span>1σ over {d.horizon_days}d <span className="text-text font-semibold tabular-nums">±{d.sigma_1_pct}%</span></span>
            )}
            {d.price_asof && (
              <span className="ml-auto">Bars through <span className="text-text font-semibold tabular-nums">{d.price_asof}</span></span>
            )}
          </div>

          {/* Derived read — pure arithmetic on the payload, always present so the
              card is never just numbers without a takeaway. */}
          {read && (
            <div className="border-l-2 border-l-accent bg-accent/5 px-3 py-2 rounded-r">
              <p className="text-xs text-text font-semibold leading-snug">{read.headline}</p>
              <p className="text-[0.65rem] text-text-muted leading-snug mt-1">{read.detail}</p>
            </div>
          )}

          {/* Methodology, collapsed — explains the concept without displacing data. */}
          <details className="group">
            <summary className="text-[0.62rem] text-text-muted hover:text-accent cursor-pointer select-none list-none flex items-center gap-1">
              <span className="transition-transform group-open:rotate-90">▸</span>
              How to read this
            </summary>
            <div className="text-[0.65rem] text-text-muted leading-relaxed mt-2 space-y-1.5 pl-3 border-l border-border">
              <p>
                <span className="text-text font-semibold">What it models.</span> Trend-following funds (CTAs)
                size S&amp;P exposure off price signals alone — moving averages, breakouts, momentum. Their
                buying and selling is mechanical: it fires when price crosses a level, regardless of
                valuation or news. That makes it forecastable in a way discretionary flow isn&apos;t.
              </p>
              <p>
                <span className="text-text font-semibold">The chart.</span> Each line is projected exposure
                change if the index lands at that scenario&apos;s price in {d.horizon_days} business days.
                All lines start at zero because they measure change from today&apos;s{" "}
                {signed(d.current_exposure)}. Below zero means selling, above means buying.
              </p>
              <p>
                <span className="text-text font-semibold">Chart vs table.</span> The chart is the{" "}
                {d.horizon_days}-day fan, so only its right edge lines up with the table&apos;s 1M column.
                Its fifth day is <em>not</em> the 1W column — each horizon scales its own sigma, so a
                one-week scenario targets a smaller move ({typeof d.sigma_1w_pct === "number" ? `±${d.sigma_1w_pct}%` : "smaller"} at
                1σ) than the monthly path happens to pass through on day five.
              </p>
              <p>
                <span className="text-text font-semibold">Why it moves equities.</span> This flow is
                price-insensitive and concentrated in liquid futures, so it lands on the index path over
                days-to-weeks. When selling clusters below spot, dips get amplified into larger drawdowns;
                when buying clusters above, breakouts get extended. Direction-dependent flow (the common
                case) widens both tails rather than picking a side.
              </p>
              <p>
                <span className="text-text font-semibold">Pivots.</span> Prices where a trend component
                flips sign. Spot crossing one converts projected flow into actual flow — the closer the
                level relative to a 1σ move, the likelier it fires.
              </p>
              <p>
                <span className="text-text font-semibold">Limits.</span> An independent reconstruction
                (SMA + breakout + momentum ensemble), not a redistribution of any bank&apos;s published
                estimate. Signals are daily-bar based, so it won&apos;t tick
                intraday. Exposure is model points; converting to dollars needs an industry AUM figure we
                don&apos;t have, so we don&apos;t guess one.
              </p>
            </div>
          </details>

          <div className="space-y-3">
            <div className="min-w-0">
              <Plot
                data={traces}
                layout={{
                  height: 220,
                  ...L,
                  yaxis: { title: "Δ exposure (pts)", gridcolor: t.grid, zeroline: true, zerolinecolor: t.muted },
                  xaxis: { title: `Business days ahead (${d.horizon_days}d horizon)`, gridcolor: t.grid },
                  hovermode: "x unified" as const,
                  legend: { orientation: "h" as const, y: -0.3, x: 0, bgcolor: "transparent", font: { size: 9 } },
                  showlegend: true,
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 min-w-0">
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

        </>
      )}
    </div>
  );
}
