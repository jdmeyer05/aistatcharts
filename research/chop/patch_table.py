"""Insert the per-hour character rows into the SESSION PATH table."""
import io

p = "frontend/components/home/es-briefing.tsx"
s = io.open(p, encoding="utf-8").read()

anchor = """                    <tr className="border-t border-border" title="Share of sessions where BOTH the high and the low are already in by the end of this hour — the day's range is settled.">
                      <td className="text-text-muted py-0.5">Both extremes in</td>
                      {(d.base_rates.path.progress ?? []).map((p) => (
                        <td key={p.slot} className="text-right px-1 text-text">{p.both_in_pct.toFixed(0)}%</td>
                      ))}
                    </tr>
"""
assert s.count(anchor) == 1, s.count(anchor)

add = """                    {/* TODAY, not a base rate. Everything above this divider is
                        what a TYPICAL session does; everything below is what THIS
                        one did. Two kinds of number in one table is how a reader
                        ends up quoting today's 8th-percentile hour as a
                        historical frequency, so the divider is labelled rather
                        than implied by styling alone.

                        Descriptive only. Measured on this sample an hour predicts
                        neither the next hour (|corr| <= 0.074 across all six
                        adjacent pairs) nor the session's final class (a choppy
                        hour precedes a choppy day 31-41% of the time against a
                        33% base). It shows the day's RHYTHM, which the cumulative
                        read cannot: a session can read choppy overall precisely
                        because each of its hours trended the other way. */}
                    {(d.chop_trend?.hourly ?? []).some((h) => h.state === "complete") && (
                      <>
                        <tr className="border-t-2 border-border">
                          <td
                            className="text-[0.55rem] uppercase tracking-wider text-accent pt-1.5 pb-0.5"
                            colSpan={1 + (d.base_rates.path.extremes ?? []).length}
                          >
                            This session · each hour on its own
                          </td>
                        </tr>
                        <tr title="What THIS hour did, scored against the same hour across past sessions. Never compared with another hour: efficiency falls with bar count and the 15:30 bucket is half the width.">
                          <td className="text-text-muted py-0.5">Hour&apos;s character</td>
                          {(d.base_rates.path.extremes ?? []).map((e) => {
                            const h = (d.chop_trend?.hourly ?? []).find((x) => x.bucket === e.slot);
                            if (!h || h.state !== "complete") {
                              return (
                                <td key={e.slot} className="text-right px-1 text-text-muted/60">
                                  {h?.state === "pending" ? "…" : "—"}
                                </td>
                              );
                            }
                            return (
                              <td
                                key={e.slot}
                                className={`text-right px-1 font-medium ${
                                  h.label === "choppy" ? "text-amber-400"
                                    : h.label === "trendy" ? "text-accent" : "text-text-muted"
                                }`}
                              >
                                {h.label}
                              </td>
                            );
                          })}
                        </tr>
                        <tr title="Where this hour's efficiency sits in that same bucket's own history. Low = it covered ground without going anywhere.">
                          <td className="text-text-muted py-0.5 pl-2">its percentile</td>
                          {(d.base_rates.path.extremes ?? []).map((e) => {
                            const h = (d.chop_trend?.hourly ?? []).find((x) => x.bucket === e.slot);
                            return (
                              <td key={e.slot} className="text-right px-1 text-text tabular-nums">
                                {h?.state === "complete" && h.pctile != null ? h.pctile.toFixed(0) : "—"}
                              </td>
                            );
                          })}
                        </tr>
                        {/* The confidence gate is TWO conditions and the tooltip
                            says so, because "confident" on a completed hour does
                            not mean what it means on a forecast. A finished hour
                            has no sampling uncertainty — efficiency is
                            deterministic in its returns and invariant to
                            permuting them — so the only thing that can be wrong
                            is the CLASSIFICATION. Hence: deep in the tail, AND
                            the label survives dropping any single bar at least as
                            often as its class typically manages. That second
                            clause is scored per class on purpose: leave-one-out
                            agreement is 1.00 for 52% of trendy hours and 0% of
                            choppy ones, so an absolute threshold would print
                            "confident" only on trending hours and merely restate
                            the label. */}
                        <tr title="Two conditions: the reading sits in the outer decile of this bucket's history, AND its label survives dropping any single bar at least as often as that class typically does. Scored per class — a choppy hour's ratio is a small difference of large numbers and is inherently more fragile than a trending one's.">
                          <td className="text-text-muted py-0.5 pl-2">confidence</td>
                          {(d.base_rates.path.extremes ?? []).map((e) => {
                            const h = (d.chop_trend?.hourly ?? []).find((x) => x.bucket === e.slot);
                            if (!h || h.state !== "complete" || h.confidence === "none") {
                              return <td key={e.slot} className="text-right px-1 text-text-muted/60">—</td>;
                            }
                            return (
                              <td
                                key={e.slot}
                                className={`text-right px-1 ${
                                  h.confidence === "confident" ? "text-text font-medium" : "text-text-muted"
                                }`}
                              >
                                {h.confidence}
                              </td>
                            );
                          })}
                        </tr>
                      </>
                    )}
"""
s = s.replace(anchor, anchor + add)
io.open(p, "w", encoding="utf-8").write(s)
print("tsx ok")

p2 = "frontend/lib/api.ts"
t = io.open(p2, encoding="utf-8").read()
a = "  note?: string;\n  method?: string;\n  caveat?: string;\n}\n"
assert t.count(a) == 1, t.count(a)
t = t.replace(a, """  /** THIS session's character hour by hour — a different measurement from
   *  everything else here, which is cumulative from the open. Descriptive only:
   *  an hour predicts neither the next hour nor the day, both measured null. */
  hourly?: Array<{
    bucket: string;
    state: "complete" | "pending" | "not_started" | "flat";
    label?: "choppy" | "mixed" | "trendy";
    confidence?: "confident" | "likely" | "none";
    read?: string;
    efficiency?: number;
    pctile?: number;
    median_at_bucket?: number;
    /** Share of leave-one-out replicates keeping the label, and what that class
     *  typically manages in this bucket. `fragile` is agreement below typical. */
    bar_agreement?: number | null;
    typical_agreement?: number | null;
    fragile?: boolean;
    bars?: number;
    bars_expected?: number;
    n_history?: number;
  }>;
  hourly_note?: string;
  note?: string;
  method?: string;
  caveat?: string;
}
""")
io.open(p2, "w", encoding="utf-8").write(t)
print("type ok")
