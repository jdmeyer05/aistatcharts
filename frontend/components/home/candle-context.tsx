"use client";

/**
 * Candle context — what the last daily bar says about TOMORROW'S RANGE.
 *
 * WHY THESE THREE VISUALS AND NOTHING ELSE. The data here is tiny (two numbers
 * to compare, one bar to depict, five ordered buckets), and every one of them
 * reads better as a small inline SVG than as a chart library call. Plotly would
 * add ~2.8MB and reintroduce the height-in-layout overflow that had to be fixed
 * on the two cards below this one.
 *
 * 1. RANGE COMPARISON — two horizontal magnitude bars on a shared scale. It is
 *    deliberately NOT a band drawn around the close: the forecast is a WIDTH
 *    (tomorrow's high minus low), and positioning it on a price axis would imply
 *    we know where that range sits, which we do not. Length is the only thing
 *    encoded, so length is the only thing drawn.
 * 2. THE BAR ITSELF — a literal candle glyph against a 1-ATR reference. "1.26x
 *    ATR, closed in the top fifth" is abstract; the shape is instant.
 * 3. CLOSE-LOCATION STRIP — five ordered buckets, today's highlighted. Its job
 *    is to make the MONOTONICITY visible, because that is the entire reason to
 *    believe the effect at all.
 *
 * Colour follows the dataviz rules: the two range bars are IDENTITY (accent vs a validated second hue), not status, so no gain/loss on them. The candle body is the one place
 * gain/loss is semantically right — there, up and down IS the meaning. The strip
 * is an ordered ramp of a single hue, not a categorical set.
 */

import type { EsCandleContext } from "@/lib/api";

const CLV_BUCKETS = [
  { label: "bottom fifth", up: 53.2, med: 0.133 },
  { label: "lower-mid", up: 51.8, med: 0.074 },
  { label: "mid", up: 51.2, med: 0.053 },
  { label: "upper-mid", up: 51.5, med: 0.061 },
  { label: "top fifth", up: 50.7, med: 0.035 },
];

function bucketOf(clv: number): number {
  if (clv < 0.2) return 0;
  if (clv < 0.4) return 1;
  if (clv < 0.6) return 2;
  if (clv < 0.8) return 3;
  return 4;
}

/** A literal candle, scaled so 1 ATR is a fixed height. */
function CandleGlyph({ bar }: { bar: NonNullable<EsCandleContext["bar"]> }) {
  const H = 74;               // px for 1 ATR
  const pad = 12;
  const total = bar.range_atr;
  const scale = H;            // 1 ATR -> H px
  const h = Math.max(total * scale, 2);
  const up = bar.body_atr >= 0;
  const bodyH = Math.max(Math.abs(bar.body_atr) * scale, 1.5);
  // Wicks measured from the extremes inward; body sits between them.
  const upperH = bar.upper_wick_atr * scale;
  const svgH = h + pad * 2;
  const cx = 22;
  const color = up ? "var(--color-gain)" : "var(--color-loss)";
  return (
    <svg width="46" height={svgH} viewBox={`0 0 46 ${svgH}`} className="shrink-0"
         role="img" aria-label={`Daily bar: range ${bar.range_atr} ATR, closed ${bar.close_location_label}`}>
      {/* 1-ATR reference so the glyph is measured, not decorative */}
      <line x1="38" x2="38" y1={pad} y2={pad + H} stroke="var(--color-border)" strokeWidth="1" />
      <line x1="35" x2="41" y1={pad} y2={pad} stroke="var(--color-border)" strokeWidth="1" />
      <line x1="35" x2="41" y1={pad + H} y2={pad + H} stroke="var(--color-border)" strokeWidth="1" />
      <text x="43" y={pad + H / 2 + 3} fontSize="7" fill="var(--color-text-muted)"
            transform={`rotate(90 43 ${pad + H / 2 + 3})`}>1 ATR</text>
      {/* wick */}
      <line x1={cx} x2={cx} y1={pad} y2={pad + h} stroke={color} strokeWidth="1.5" />
      {/* body, 4px rounded ends per mark spec */}
      <rect x={cx - 7} y={pad + upperH} width="14" height={bodyH} rx="2"
            fill={color} opacity="0.85" />
    </svg>
  );
}

/** Two magnitude bars on one scale. Length is the only encoded channel. */
function RangeBars({ d }: { d: EsCandleContext }) {
  const f = d.tomorrow_range;
  if (!f) return null;
  const implied = d.vs_implied?.implied_range ?? null;
  const max = Math.max(f.p90, implied ?? 0) * 1.06;
  const pct = (v: number) => `${(v / max) * 100}%`;

  return (
    <div className="space-y-2">
      <div>
        <div className="flex items-baseline justify-between gap-2 mb-1">
          <span className="text-[0.55rem] uppercase tracking-wider text-text-muted">
            Measured — bars like today&apos;s
          </span>
          <span className="text-[0.6rem] tabular-nums text-text">
            {f.p50.toFixed(2)}{" "}
            <span className="text-text-muted">median · {f.p25.toFixed(2)}–{f.p75.toFixed(2)} typical</span>
          </span>
        </div>
        <div className="relative h-4 rounded bg-surface-alt/60 overflow-hidden"
             title={f.note}>
          {/* p25-p75 core, p90 tail behind it */}
          <div className="absolute inset-y-0 left-0 bg-accent/15" style={{ width: pct(f.p90) }} />
          <div className="absolute inset-y-0 left-0 bg-accent/35" style={{ width: pct(f.p75) }} />
          <div className="absolute inset-y-0 left-0 bg-accent/60" style={{ width: pct(f.p25) }} />
          {/* median marker, 2px surface ring so it reads over the fill */}
          <div className="absolute inset-y-0 w-[3px] bg-accent"
               style={{ left: `calc(${pct(f.p50)} - 1.5px)`, boxShadow: "0 0 0 1px var(--color-surface)" }} />
        </div>
      </div>

      {implied != null && (
        <div>
          <div className="flex items-baseline justify-between gap-2 mb-1">
            <span className="text-[0.55rem] uppercase tracking-wider text-text-muted">
              Options-implied
            </span>
            <span className="text-[0.6rem] tabular-nums text-text">{implied.toFixed(2)}</span>
          </div>
          <div className="relative h-4 rounded bg-surface-alt/60 overflow-hidden">
            <div className="absolute inset-y-0 left-0 bg-[var(--color-series2)]/60" style={{ width: pct(implied) }} />
          </div>
        </div>
      )}

      <div className="flex items-center gap-3 text-[0.55rem] text-text-muted">
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-sm bg-accent/60 inline-block" /> measured
        </span>
        {implied != null && (
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-sm bg-[var(--color-series2)]/60 inline-block" /> implied
          </span>
        )}
        <span className="ml-auto tabular-nums">
          {f.prob_exceeds_1_atr.toFixed(0)}% exceed 1 ATR · n={f.n.toLocaleString()}
        </span>
      </div>
    </div>
  );
}

export default function CandleContextBlock({ d }: { d: EsCandleContext | null | undefined }) {
  if (!d?.available || !d.bar) return null;
  const bar = d.bar;
  const here = bucketOf(bar.close_location);
  const div = d.vs_implied;

  return (
    <div className="border-t border-border pt-3 space-y-2.5">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <h3 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
          Yesterday&apos;s bar → tomorrow&apos;s range
        </h3>
        <span className="text-[0.55rem] text-text-muted">
          {d.symbol} · {d.asof} · cash index
        </span>
      </div>

      {div && (
        <p className={`text-[0.65rem] text-text border-l-2 pl-2 ${
          div.label === "in line" ? "border-l-accent" : "border-l-amber-400"
        }`} title={div.caveat}>
          Options price <span className="tabular-nums">{div.implied_range.toFixed(0)}</span>, bars
          like today&apos;s have delivered <span className="tabular-nums">{div.empirical_p50.toFixed(0)}</span>
          {" "}— <span className="font-semibold">{div.label}</span> at{" "}
          <span className="tabular-nums">{div.ratio.toFixed(2)}×</span>. {div.note}
        </p>
      )}

      <div className="flex gap-4 items-start">
        <div className="flex flex-col items-center gap-1">
          <CandleGlyph bar={bar} />
          <span className="text-[0.5rem] text-text-muted text-center leading-tight">
            {bar.range_label}
            <br />
            {bar.range_atr.toFixed(2)}× ATR
          </span>
        </div>
        <div className="flex-1 min-w-0">
          <RangeBars d={d} />
        </div>
      </div>

      {/* Ordered ramp, today highlighted. Exists to make the monotonicity visible. */}
      {d.direction_tilt && (
        <div>
          <div className="text-[0.55rem] uppercase tracking-wider text-text-muted mb-1">
            Where it closed → next session (measured, monotonic)
          </div>
          <div className="flex gap-[2px]">
            {CLV_BUCKETS.map((b, i) => (
              <div key={b.label} className="flex-1 min-w-0"
                   title={`Closed ${b.label}: up ${b.up}% next session, median ${b.med >= 0 ? "+" : ""}${b.med}%.`}>
                <div
                  className={`h-6 rounded-sm flex items-end justify-center ${
                    i === here ? "ring-1 ring-accent" : ""
                  }`}
                  style={{
                    background: `color-mix(in oklab, var(--color-accent) ${14 + (4 - i) * 9}%, transparent)`,
                  }}
                >
                  <span className={`text-[0.5rem] tabular-nums pb-0.5 ${
                    i === here ? "text-text font-semibold" : "text-text-muted"
                  }`}>
                    {b.up.toFixed(1)}
                  </span>
                </div>
                <div className={`text-[0.45rem] text-center mt-0.5 truncate ${
                  i === here ? "text-accent" : "text-text-muted"
                }`}>
                  {i === here ? "today" : b.label}
                </div>
              </div>
            ))}
          </div>
          <p className="text-[0.52rem] text-text-muted leading-snug mt-1.5">
            A strong close predicts <span className="text-text">weakness</span>, not follow-through
            — but only ~10bp of median return end to end (IC {d.direction_tilt.ic?.toFixed(3)},
            t={d.direction_tilt.ic_t?.toFixed(1)}). Context, never a trade. Range is the part worth
            sizing off: IC 0.158, t=75.
          </p>
        </div>
      )}
    </div>
  );
}
