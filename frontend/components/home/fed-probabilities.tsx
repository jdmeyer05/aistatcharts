"use client";

/**
 * What 30-Day Fed Funds futures price for the next FOMC decisions.
 *
 * SWING horizon, and regime context rather than a signal: this says where the
 * rates market thinks policy lands over months, which conditions the backdrop
 * an ES session sits in. It says nothing about today and the card does not
 * pretend otherwise.
 *
 * `method` and `leverage` are shown per meeting on purpose. The within-month
 * solve divides by the post-meeting days left in the contract month, so a
 * late-month decision multiplies settlement noise — measured up to 30x, where
 * one ZQ tick moves the answer by 15bp. A reader should be able to see which
 * estimator produced a number instead of having to trust it.
 */

import { useQuery } from "@tanstack/react-query";
import { fetchFedProbabilities, type FedProbabilities, type FedMeeting } from "@/lib/api";

function fmtMeetingDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** The largest-probability outcome, which is what a reader looks for first. */
function topOutcome(m: FedMeeting): { label: string; p: number } | null {
  const entries = Object.entries(m.probabilities ?? {});
  if (!entries.length) return null;
  const [label, p] = entries.reduce((a, b) => (b[1] > a[1] ? b : a));
  return { label, p };
}

export default function FedProbabilitiesCard() {
  const q = useQuery<FedProbabilities>({
    // Daily ZQ settlements — nothing to gain from polling faster than the
    // server's own 30-minute hold.
    queryKey: ["fed-probabilities", 4],
    queryFn: () => fetchFedProbabilities(4),
    refetchInterval: 30 * 60_000,
    staleTime: 25 * 60_000,
  });
  const d = q.data;
  const priced = (d?.meetings ?? []).filter((m) => !m.error);

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-bold uppercase tracking-wider text-accent">
            What Rates Are Priced For
          </h2>
          <div className="text-[0.6rem] text-text-muted mt-0.5">
            Next {priced.length || 4} FOMC decisions from 30-day fed funds futures · swing horizon
          </div>
        </div>
        {typeof d?.cumulative_bp === "number" && (
          <div className="text-right">
            <div className={`text-lg font-bold font-data tabular-nums ${
              d.cumulative_bp > 0 ? "text-loss" : d.cumulative_bp < 0 ? "text-gain" : "text-text"}`}>
              {d.cumulative_bp > 0 ? "+" : ""}{d.cumulative_bp.toFixed(0)}bp
            </div>
            <div className="text-[0.55rem] text-text-muted">
              priced by {priced.length ? fmtMeetingDate(priced[priced.length - 1].date) : "—"}
            </div>
          </div>
        )}
      </div>

      {q.isLoading && (
        <div className="py-6 text-center">
          <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {!q.isLoading && !d?.available && (
        <p className="text-xs text-text-muted py-2">
          {q.isError ? "Couldn't load rate pricing." : `Unavailable${d?.reason ? ` — ${d.reason}` : ""}.`}
        </p>
      )}

      {d?.available && (
        <>
          <div className="space-y-1.5">
            {d.meetings?.map((m) => {
              if (m.error) {
                return (
                  <div key={m.date} className="flex items-baseline gap-2 border border-border rounded p-2">
                    <span className="text-xs font-semibold">{fmtMeetingDate(m.date)}</span>
                    <span className="text-[0.6rem] text-text-muted">{m.error}</span>
                  </div>
                );
              }
              const top = topOutcome(m);
              const delta = m.delta_bp ?? 0;
              return (
                <div key={m.date} className="border border-border rounded p-2">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="text-xs font-bold w-14 shrink-0">{fmtMeetingDate(m.date)}</span>
                    <span className={`text-sm font-semibold font-data tabular-nums ${
                      delta > 1 ? "text-loss" : delta < -1 ? "text-gain" : "text-text-muted"}`}>
                      {delta > 0 ? "+" : ""}{delta.toFixed(1)}bp
                    </span>
                    {top && (
                      <span className="text-[0.65rem] text-text">
                        {top.label} at <span className="tabular-nums font-semibold">{(top.p * 100).toFixed(0)}%</span>
                      </span>
                    )}
                    <span className="ml-auto text-[0.55rem] text-text-muted tabular-nums">
                      {m.ticker}
                      {m.method === "within-month" && m.leverage != null
                        ? ` · within-month, ${m.leverage.toFixed(1)}× tick leverage`
                        : " · next-month, no leverage"}
                    </span>
                  </div>
                  {m.probabilities && Object.keys(m.probabilities).length > 1 && (
                    <div className="flex gap-3 mt-1 text-[0.6rem] text-text-muted tabular-nums">
                      {Object.entries(m.probabilities).map(([k, v]) => (
                        <span key={k}>{k} <span className="text-text">{(v * 100).toFixed(0)}%</span></span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <details className="group">
            <summary className="text-[0.62rem] text-text-muted hover:text-accent cursor-pointer select-none list-none flex items-center gap-1">
              <span className="transition-transform group-open:rotate-90">▸</span>
              How this is built, and where it breaks
            </summary>
            <div className="text-[0.65rem] text-text-muted leading-relaxed mt-2 space-y-1.5 pl-3 border-l border-border">
              <p>
                <span className="text-text font-semibold">Reconstruction, not a licensed feed.</span> ZQ
                settles at 100 minus the average daily EFFR over <em>every calendar day</em> of its
                delivery month, so a mid-month decision is only partly reflected in its own month&apos;s
                contract. It settles on EFFR, not on the target range — mixing the two is a silent
                8–12bp error. Anchor here is {d.anchor}
                {typeof d.anchor_rate === "number" && ` at ${d.anchor_rate.toFixed(3)}%`}
                {typeof d.spot_effr === "number" && `, against spot EFFR of ${d.spot_effr.toFixed(3)}%`}.
              </p>
              <p>
                <span className="text-text font-semibold">Each meeting is chained to the last.</span> The
                rate prevailing before a decision is the rate solved after the one before it. Anchoring
                every meeting on spot EFFR instead reported +180.83bp for one 2026 meeting that actually
                prices +3.69bp — it hands all previously-priced tightening to whichever decision you look
                at.
              </p>
              <p>
                <span className="text-text font-semibold">Leverage is why the estimator is labelled.</span>{" "}
                When the following month holds no meeting, its contract prices a whole month at the new
                rate and the answer needs no day-weighting. Otherwise the solve divides by the
                post-meeting days remaining: 2.1× for a mid-month decision, 30× for one on the 29th, where
                a single half-basis-point tick moves the result 15bp — sixty probability points.
              </p>
              <p>
                <span className="text-text font-semibold">The percentages are an interpolation.</span> CME
                assumes moves come in whole 25bp steps and splits a priced change across the two adjacent
                buckets to reproduce its expected value. It cannot express &ldquo;50bp or nothing&rdquo;.
              </p>
              {d.calendar_exhausted && (
                <p className="text-warn">
                  The encoded FOMC calendar ends {d.calendar_ends}, so fewer meetings are shown than
                  requested. Beyond that date no month can be treated as meeting-free, which is why the
                  last decision on the board uses the within-month solve.
                </p>
              )}
            </div>
          </details>

          <div className="text-[0.55rem] text-text-muted border-t border-border pt-2">
            {d.source} · as of {d.asof} · describes what is priced over months, not what happens today
          </div>
        </>
      )}
    </div>
  );
}
