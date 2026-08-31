"""Replace the hourly label rows with the random-walk verdict."""
import io

p = "frontend/components/home/es-briefing.tsx"
s = io.open(p, encoding="utf-8").read()

start = s.index('                        <tr title="What THIS hour did, scored against the same hour')
end_marker = """                          })}
                        </tr>
                      </>
                    )}
"""
end = s.index(end_marker, start) + len(end_marker)
old = s[start:end]
assert "confidence" in old and "Hour&apos;s character" in old, old[:200]

new = '''                        {/* NET PROGRESS, not "character". The label that used to
                            sit here ranked each hour's efficiency against the same
                            hour in history and called the top third trendy — but
                            that population is itself almost entirely random walks,
                            so its 70th percentile is a random walk too. It printed
                            "likely trendy" over hours that had done nothing. */}
                        <tr title="Net move as a share of total travel inside the hour. 100% is a straight line; single digits mean price covered ground and ended where it started.">
                          <td className="text-text-muted py-0.5">Net progress</td>
                          {(d.base_rates.path.extremes ?? []).map((e) => {
                            const h = (d.chop_trend?.hourly ?? []).find((x) => x.bucket === e.slot);
                            return (
                              <td key={e.slot} className="text-right px-1 text-text tabular-nums">
                                {h?.state === "complete" && h.net_progress_pct != null
                                  ? `${h.net_progress_pct.toFixed(0)}%`
                                  : h?.state === "pending" ? "…" : "—"}
                              </td>
                            );
                          })}
                        </tr>
                        <tr title="Where that sits among the same hour across past sessions. A ranking, not a verdict — the row below says whether it means anything.">
                          <td className="text-text-muted py-0.5 pl-2">vs same hour</td>
                          {(d.base_rates.path.extremes ?? []).map((e) => {
                            const h = (d.chop_trend?.hourly ?? []).find((x) => x.bucket === e.slot);
                            return (
                              <td key={e.slot} className="text-right px-1 text-text-muted tabular-nums">
                                {h?.state === "complete" && h.pctile != null ? h.pctile.toFixed(0) : "—"}
                              </td>
                            );
                          })}
                        </tr>
                        {/* The row that keeps the two above honest. Against a
                            sign-flip null — every move's size kept, its direction
                            randomised — 9.5% of 8,708 historical hours clear
                            p<0.10 on the trending side and 10.0% on the choppy
                            side. Chance is 10%. There is no excess in either
                            tail, and the same holds at 1-minute resolution, so
                            this is an absence of signal rather than a shortage of
                            bars. Most hours therefore read "coin flip", and that
                            is the finding rather than a gap in it. */}
                        <tr title="Sign-flip test: keep the size of every move in the hour, randomise its direction, and ask how often chance alone produces this much net progress. Measured over 8,708 past hours, only 9.5% beat it on the trending side and 10.0% on the choppy side — chance is 10%, so an hour of this tape is statistically a coin flip.">
                          <td className="text-text-muted py-0.5 pl-2">vs a coin flip</td>
                          {(d.base_rates.path.extremes ?? []).map((e) => {
                            const h = (d.chop_trend?.hourly ?? []).find((x) => x.bucket === e.slot);
                            if (!h || h.state !== "complete" || !h.verdict) {
                              return <td key={e.slot} className="text-right px-1 text-text-muted/60">—</td>;
                            }
                            const real = h.verdict !== "coin flip";
                            return (
                              <td
                                key={e.slot}
                                className={`text-right px-1 ${
                                  h.verdict === "chopped" ? "text-amber-400 font-medium"
                                    : h.verdict === "trended" ? "text-accent font-medium"
                                      : "text-text-muted/70"
                                }`}
                              >
                                {h.verdict}
                                {real && h.p != null && (
                                  <span className="text-text-muted/70 tabular-nums">
                                    {" "}p={h.p.toFixed(2)}
                                  </span>
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      </>
                    )}
'''
s = s[:start] + new + s[end:]

# The forecast null, stated where the row is read rather than in a footnote.
anchor = """                        </tr>
                      </>
                    )}
"""
idx = s.index(new) + len(new)
extra = """                    {d.chop_trend?.hourly_forecast && (
                      <tr>
                        <td
                          className="text-[0.55rem] text-text-muted/80 leading-snug pt-1"
                          colSpan={1 + (d.base_rates.path.extremes ?? []).length}
                        >
                          {/* Asked for directly, and the answer is a null, so it
                              prints as one. Reporting the R2 rather than a
                              reassuring sentence is the whole point. */}
                          Nothing here forecasts the next hour: prior-hour efficiency, reversal
                          rate, volatility, range and the session&apos;s cumulative reading, fitted
                          on 60% of sessions and scored on the rest, give an out-of-sample R
                          <sup>2</sup> of {d.chop_trend.hourly_forecast.oos_r2?.toFixed(4)} and
                          classify the next hour {d.chop_trend.hourly_forecast.accuracy_pct?.toFixed(1)}%
                          correctly against a {d.chop_trend.hourly_forecast.baseline_pct?.toFixed(1)}%
                          baseline. The one variable that looked predictive was time of day, and it
                          was the half-width 15:30 bucket — excluding it, that correlation falls
                          from +0.127 to −0.006.
                        </td>
                      </tr>
                    )}
"""
s = s[:idx] + extra + s[idx:]
io.open(p, "w", encoding="utf-8").write(s)
print("tsx ok")

# ---- types ----
p2 = "frontend/lib/api.ts"
t = io.open(p2, encoding="utf-8").read()
a = """    label?: "choppy" | "mixed" | "trendy";
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
"""
assert t.count(a) == 1, t.count(a)
b = """    /** Sign-flip verdict. "coin flip" on roughly nine hours in ten, which is the
     *  measured finding rather than a gap: an hour of this tape is not
     *  distinguishable from a random walk at 5-minute OR 1-minute resolution. */
    verdict?: "trended" | "chopped" | "coin flip";
    p?: number | null;
    p_trend?: number | null;
    p_chop?: number | null;
    /** Net move as a share of total travel, in percent. */
    net_progress_pct?: number;
    efficiency?: number;
    pctile?: number;
    median_at_bucket?: number;
"""
t = t.replace(a, b)
a2 = "  hourly_note?: string;\n"
assert t.count(a2) == 1
t = t.replace(a2, """  hourly_note?: string;
  /** The next-hour forecast, measured and null. Carried so the card can print
   *  the number instead of a reassuring sentence. */
  hourly_forecast?: {
    verdict: "null";
    oos_r2: number;
    accuracy_pct: number;
    baseline_pct: number;
    note: string;
  } | null;
""")
io.open(p2, "w", encoding="utf-8").write(t)
print("types ok")
