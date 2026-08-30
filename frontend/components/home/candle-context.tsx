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

// Labels only. The NUMBERS come from the payload — hardcoding them here would
// let the card drift silently the first time the study is regenerated, and the
// backend already knows which bucket today's bar landed in.
const CLV_LABELS = ["bottom fifth", "lower-mid", "mid", "upper-mid", "top fifth"];

/** A literal candle, scaled so 1 ATR is a fixed height. */
function CandleGlyph({ bar }: { bar: NonNullable<EsCandleContext["bar"]> }) {
  // The glyph must fit a FIXED box. Scaling a fixed px-per-ATR meant a 3-ATR
  // session rendered a 246px-tall SVG that shoved the layout apart — rare, and
  // exactly the kind of day you would be looking at the card. So the box is
  // fixed and the ATR rule scales inside it instead.
  const BOX = 74;             // px the tallest element may occupy
  const pad = 12;
  const total = Math.max(bar.range_atr, 0.05);
  const scale = BOX / Math.max(total, 1);   // 1 ATR is BOX px until range exceeds it
  const H = scale;            // length of the 1-ATR reference rule
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
          {f.prob_exceeds_1_atr.toFixed(0)}% exceed 1 ATR · n={f.n.toLocaleString("en-US")}
        </span>
      </div>
    </div>
  );
}

export default function CandleContextBlock({ d }: { d: EsCandleContext | null | undefined }) {
  if (!d?.available || !d.bar) return null;
  const bar = d.bar;
  const curve = d.close_location_curve ?? [];
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
          Options price <span className="tabular-nums">{div.implied_range.toFixed(2)}</span>, bars
          like today&apos;s have delivered <span className="tabular-nums">{div.empirical_p50.toFixed(2)}</span>
          {" "}— <span className="font-semibold">{div.label}</span> at{" "}
          <span className="tabular-nums">{div.ratio.toFixed(2)}×</span>. {div.note}
        </p>
      )}

      {/* The study measures the CASH INDEX and reports a bare point figure.
          This is the translation into what it means for an ES session — index
          points and ES points are the same size, because the basis is a level
          offset rather than a scale factor. It was computed and shipped in the
          payload but never rendered. */}
      {(d.es_read?.reads?.length ?? 0) > 0 && (
        <div className="border border-border rounded p-2 space-y-1.5">
          <div className="text-[0.55rem] uppercase tracking-wider text-text-muted">
            For the ES session
          </div>
          {d.es_read!.reads!.map((r) => (
            <div key={r.label} className="text-[0.65rem] leading-snug">
              <span className="text-text-muted">{r.label}: </span>
              <span className="text-text font-semibold font-data tabular-nums">{r.value}</span>
              <span className="text-text-muted"> — {r.note}</span>
              {r.caveat && (
                <span className="block text-[0.55rem] text-text-muted/80 mt-0.5">{r.caveat}</span>
              )}
            </div>
          ))}
        </div>
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
      {d.direction_tilt && curve.length > 0 && (
        <div>
          <div className="text-[0.55rem] uppercase tracking-wider text-text-muted mb-1">
            Where it closed → next session (measured, monotonic)
          </div>
          <div className="flex gap-[2px]">
            {curve.map((b, i) => {
              // Shade by POSITION in the ordered sequence, never by value — an
              // ordinal ramp, not a value ramp on nominal categories.
              const shade = 14 + (curve.length - 1 - i) * 9;
              return (
                <div key={b.bucket} className="flex-1 min-w-0"
                     title={`Closed ${CLV_LABELS[b.bucket] ?? b.bucket}: up ${b.next_up_pct.toFixed(1)}% next session over ${b.n.toLocaleString("en-US")} instances, median ${b.median_next_ret_pct >= 0 ? "+" : ""}${b.median_next_ret_pct.toFixed(3)}%.`}>
                  <div
                    className={`h-6 rounded-sm flex items-end justify-center ${b.is_today ? "ring-1 ring-accent" : ""}`}
                    style={{ background: `color-mix(in oklab, var(--color-accent) ${shade}%, transparent)` }}
                  >
                    <span className={`text-[0.5rem] tabular-nums pb-0.5 ${b.is_today ? "text-text font-semibold" : "text-text-muted"}`}>
                      {b.next_up_pct.toFixed(1)}
                    </span>
                  </div>
                  <div className={`text-[0.45rem] text-center mt-0.5 truncate ${b.is_today ? "text-accent" : "text-text-muted"}`}>
                    {b.is_today ? "today" : (CLV_LABELS[b.bucket] ?? "")}
                  </div>
                </div>
              );
            })}
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
