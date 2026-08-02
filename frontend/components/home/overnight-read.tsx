"use client";

/**
 * The Globex range, and what the cash session has historically done with it.
 *
 * This is the highest-value measured content in the cockpit and it had no UI:
 * where the session OPENS inside the overnight range predicts which side breaks
 * (82% / 91% at the extremes), while the range itself survives the day only
 * about one time in twenty.
 *
 * Two rules the payload enforces and this card must not undo:
 *
 *  1. A side that has ALREADY broken is dropped from `expected`. What has
 *     happened is a fact; restating its prior as a probability would report a
 *     90.8% chance of breaking a level that already broke.
 *  2. The base rates are conditioned on the OPEN, not on the last price. Before
 *     the bell there is no open, so `open_is_estimated` is set and the read is
 *     labelled as provisional rather than quietly using `last` in its place.
 *
 * The study is ES over two years. The path base rates elsewhere on this page
 * are SPY over five. Both are labelled on-card because a reader glancing
 * between them would otherwise assume one instrument.
 */

import type { EsOvernight } from "@/lib/api";

function Pct({ v }: { v: number }) {
  return <span className="font-data tabular-nums font-semibold">{v.toFixed(1)}%</span>;
}

export default function OvernightRead({ d }: { d?: EsOvernight | null }) {
  if (!d?.available) return null;
  const live = d.live;
  const rs = d.range_survival;

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-bold uppercase tracking-wider text-accent">
            Overnight → Cash Session
          </h2>
          <div className="text-[0.6rem] text-text-muted mt-0.5">
            {d.instrument ?? "ES futures"} · {d.sessions ?? "—"} sessions
            {d.from && d.to && ` · ${d.from} to ${d.to}`}
            {d.complete === false && " · incomplete"}
          </div>
        </div>
        {live?.contract && (
          <span className="text-[0.55rem] text-text-muted font-data">{live.contract}</span>
        )}
      </div>

      {live ? (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {[
              ["Overnight high", live.overnight_high.toFixed(2)],
              ["Overnight low", live.overnight_low.toFixed(2)],
              ["Range", `${live.overnight_range.toFixed(2)} pts`],
              ["Last", live.last.toFixed(2)],
            ].map(([k, v]) => (
              <div key={k} className="border border-border rounded p-2">
                <div className="text-[0.55rem] uppercase tracking-wider text-text-muted">{k}</div>
                <div className="text-sm font-bold font-data tabular-nums">{v}</div>
              </div>
            ))}
          </div>

          <div className="border border-border rounded p-2">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="text-[0.6rem] uppercase tracking-wider text-text-muted">
                {live.open_is_estimated ? "Price sits" : "Opened"}
              </span>
              <span className="text-sm font-bold font-data tabular-nums">
                {live.position_in_range_pct.toFixed(0)}%
              </span>
              <span className="text-[0.65rem] text-text">
                into the range — {live.band}
              </span>
            </div>
            {/* Position bar. The open is what the base rates key on, so that is
                what is marked, not the last price. */}
            <div className="relative h-1.5 mt-2 rounded bg-border">
              <div
                className="absolute top-0 h-1.5 w-1 rounded bg-accent"
                style={{ left: `${Math.max(0, Math.min(100, live.position_in_range_pct))}%` }}
              />
            </div>
            <div className="flex justify-between text-[0.55rem] text-text-muted mt-0.5">
              <span>ON low</span><span>ON high</span>
            </div>
            {live.open_is_estimated && (
              <div className="text-[0.55rem] text-warn mt-1.5 leading-snug">
                No cash open yet — this uses the last price as a stand-in. The base rates below
                are conditioned on where the session actually opens, so this read is provisional
                until 09:30.
              </div>
            )}
          </div>

          {(live.broke_on_high || live.broke_on_low) && (
            <div className="text-[0.62rem] text-text border border-border rounded p-2">
              Already resolved this session:{" "}
              {live.broke_on_high && <span className="text-gain font-semibold">high broken</span>}
              {live.broke_on_high && live.broke_on_low && " · "}
              {live.broke_on_low && <span className="text-loss font-semibold">low broken</span>}
              <span className="text-text-muted">
                {" "}— dropped from the expectations below rather than restated as a probability.
              </span>
            </div>
          )}

          {live.expected && (
            <div className="border border-border rounded p-2 space-y-1">
              <div className="text-[0.55rem] uppercase tracking-wider text-text-muted">
                From this opening position (n={live.expected.n})
              </div>
              {typeof live.expected.breaks_on_high_pct === "number" && (
                <div className="text-[0.68rem]">
                  Overnight high has been taken out <Pct v={live.expected.breaks_on_high_pct} /> of the time
                </div>
              )}
              {typeof live.expected.breaks_on_low_pct === "number" && (
                <div className="text-[0.68rem]">
                  Overnight low has been taken out <Pct v={live.expected.breaks_on_low_pct} /> of the time
                </div>
              )}
              {live.expected.breaks_on_high_pct == null && live.expected.breaks_on_low_pct == null && (
                <div className="text-[0.62rem] text-text-muted">Both sides already resolved.</div>
              )}
            </div>
          )}

          {live.rth_range_expectation && (
            <div className="text-[0.65rem] text-text-muted border border-border rounded p-2">
              Cash sessions following an overnight range this size ran{" "}
              <span className="text-text font-data tabular-nums">
                {live.rth_range_expectation.median.toFixed(0)} pts
              </span>{" "}
              median, {live.rth_range_expectation.p25.toFixed(0)}–
              {live.rth_range_expectation.p75.toFixed(0)} interquartile (n={live.rth_range_expectation.n}).
            </div>
          )}
        </>
      ) : (
        <div className="text-[0.65rem] text-text-muted border border-border rounded p-2">
          No live overnight read — the session could not be measured. The historical study below
          does not depend on it and still stands.
        </div>
      )}

      {rs && (
        <div className="grid grid-cols-3 gap-2">
          {[
            ["One side breaks", rs.one_sided_pct],
            ["Both sides break", rs.both_sides_pct],
            ["Range holds", rs.held_inside_pct],
          ].map(([k, v]) => (
            <div key={k as string} className="border border-border rounded p-2">
              <div className="text-[0.55rem] uppercase tracking-wider text-text-muted">{k}</div>
              <div className="text-sm font-bold font-data tabular-nums">{(v as number).toFixed(1)}%</div>
            </div>
          ))}
        </div>
      )}

      <details className="group">
        <summary className="text-[0.62rem] text-text-muted hover:text-accent cursor-pointer select-none list-none flex items-center gap-1">
          <span className="transition-transform group-open:rotate-90">▸</span>
          What this measures
        </summary>
        <div className="text-[0.65rem] text-text-muted leading-relaxed mt-2 space-y-1.5 pl-3 border-l border-border">
          {rs?.note && <p>{rs.note}</p>}
          {(d.notes ?? []).map((n) => <p key={n}>{n}</p>)}
          {typeof d.overnight_share_of_full_range_pct === "number" && (
            <p>
              <span className="text-text font-semibold">
                {d.overnight_share_of_full_range_pct.toFixed(0)}%
              </span>{" "}
              of the full 23-hour range is typically already made before the bell.
            </p>
          )}
          <p>
            Measured on <span className="text-text">{d.instrument ?? "ES futures"}</span> over{" "}
            {d.sessions ?? "—"} sessions. The path base rates elsewhere on this page are SPY over
            five years — different instrument, different window, both labelled.
            {d.complete === false && d.contracts_missing?.length
              ? ` Contracts missing from the study: ${d.contracts_missing.join(", ")}.`
              : ""}
          </p>
        </div>
      </details>
    </div>
  );
}
