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

import { useMemo } from "react";
import type { EsOvernight } from "@/lib/api";
import { Takeaway } from "@/components/home/primitives";

function Pct({ v }: { v: number }) {
  return <span className="font-data tabular-nums font-semibold">{v.toFixed(1)}%</span>;
}

export default function OvernightRead({ d }: { d?: EsOvernight | null }) {
  const live = d?.live;
  const rs = d?.range_survival;

  // THE TAKEAWAY. Twelve numbers across five boxes, and the finding that
  // actually earns the card — the opening position predicts which side goes,
  // while the range itself almost never survives — was left for the reader to
  // assemble. State both, and state the pre-open case AS provisional rather
  // than letting a stand-in price read like a settled open.
  //
  // Declared ahead of the early return below so the hook order is fixed. The
  // component used to bail out before any hook existed, which is fine right up
  // until the first one is added and then is a crash.
  const read = useMemo(() => {
    if (!d?.available) return null;

    if (!live) {
      return {
        tone: "neutral" as const,
        headline: "No live overnight read this session — the historical study below still stands.",
        detail: rs
          ? `Across ${d.sessions ?? "—"} sessions the overnight range held all day only ` +
            `${rs.held_inside_pct.toFixed(1)}% of the time; one side went ` +
            `${rs.one_sided_pct.toFixed(1)}% and both went ${rs.both_sides_pct.toFixed(1)}%.`
          : undefined,
      };
    }

    const hi = live.expected?.breaks_on_high_pct;
    const lo = live.expected?.breaks_on_low_pct;
    const withheld = live.expected?.withheld;

    if (withheld) {
      return {
        tone: "warn" as const,
        headline:
          `Price sits ${live.position_in_range_pct.toFixed(0)}% into the overnight range ` +
          `(${live.band}), but the break expectations are withheld — ${withheld}`,
        detail:
          `The base rates are conditioned on a FINISHED overnight range and on where the cash ` +
          `session actually opens. Quoting them now would attach a measured frequency to a session ` +
          `that does not exist yet.`,
      };
    }

    const sides: string[] = [];
    if (typeof hi === "number") sides.push(`the high has gone ${hi.toFixed(1)}% of the time`);
    if (typeof lo === "number") sides.push(`the low has gone ${lo.toFixed(1)}% of the time`);
    const resolved = live.broke_on_high || live.broke_on_low;

    return {
      tone: "neutral" as const,
      headline:
        sides.length === 0
          ? "Both sides of the overnight range have already been taken out this session — there is nothing left for the base rates to say."
          : `${live.open_is_estimated ? "Price sits" : "The session opened"} ` +
            `${live.position_in_range_pct.toFixed(0)}% into the overnight range (${live.band}), and ` +
            `from there ${sides.join(" and ")}${live.expected?.n ? ` (n=${live.expected.n})` : ""}.`,
      detail:
        (rs
          ? `The range itself survives a whole session only ${rs.held_inside_pct.toFixed(1)}% of the ` +
            `time, so the live question is usually which side goes rather than whether one does. `
          : "") +
        (resolved
          ? `A side that has already broken is dropped rather than restated as a probability. `
          : "") +
        (live.open_is_estimated
          ? `Provisional: there is no cash open yet, so this substitutes the last price for the ` +
            `number the base rates are actually conditioned on. `
          : "") +
        `Measured on ${d.instrument ?? "ES futures"} over ${d.sessions ?? "—"} sessions — a ` +
        `different instrument and window from the SPY path base rates elsewhere on this card.`,
    };
  }, [d, live, rs]);

  if (!d?.available) return null;

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

      {read && <Takeaway headline={read.headline} detail={read.detail} tone={read.tone} />}

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
              {/* Withheld while the range is still being built. Saying why beats
                  showing a frequency about a session that does not exist yet. */}
              {live.expected.withheld ? (
                <div className="text-[0.62rem] text-text-muted leading-snug">
                  Withheld — {live.expected.withheld}
                  {typeof d.live?.overnight_elapsed_pct === "number" &&
                    ` (${d.live.overnight_elapsed_pct.toFixed(0)}% of the 18:00–09:30 window elapsed)`}
                  . {live.expected.note}
                </div>
              ) : (
                <>
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
                </>
              )}
            </div>
          )}

          {live.overnight_complete === false && (
            <div className="text-[0.6rem] text-text-muted border border-border rounded p-2 leading-snug">
              The range is still forming
              {typeof live.overnight_elapsed_pct === "number" &&
                ` — ${live.overnight_elapsed_pct.toFixed(0)}% of the Globex window has elapsed`}
              . The session-range expectation is bucketed on the FINISHED overnight range, so it is
              held back rather than drawn from a bucket the range has not settled into yet.
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
