"""Show the control beside the score it qualifies."""
import io

p = "frontend/components/home/es-briefing.tsx"
s = io.open(p, encoding="utf-8").read()

anchor = """                      {/* The improvement lever, printed rather than acted on."""
assert s.count(anchor) == 1
add = """                      {/* THE CONTROL, and it is the most important number in
                          this block. The read predicts a session's FINAL class
                          from a partial reading of the SAME session, and those
                          windows overlap — by 15:00 most of the final number is
                          already observed. So a good score here can be
                          arithmetic. Sessions that keep every real magnitude and
                          randomise only the signs cannot contain a market
                          signal, and they score the same. The read stays because
                          "this session has gone nowhere" is a true description;
                          the control stays so nobody reads a calibration table
                          as an edge. */}
                      {chopRecQ.data.control && (
                        <p className="text-[0.55rem] text-text-muted leading-snug">
                          <span className="uppercase tracking-wider">Against a control: </span>
                          sessions with every real move size kept and only their directions
                          randomised score{" "}
                          <span className="text-text tabular-nums">
                            {chopRecQ.data.control.overall_pct?.toFixed(1)}%
                          </span>{" "}
                          through this same pipeline, against{" "}
                          <span className="text-text tabular-nums">
                            {chopRecQ.data.control.real_pct?.toFixed(1)}%
                          </span>{" "}
                          for the real tape — a mix-matched edge of{" "}
                          <span className="text-text tabular-nums">
                            {chopRecQ.data.control.edge_pp != null
                              ? `${chopRecQ.data.control.edge_pp > 0 ? "+" : ""}${chopRecQ.data.control.edge_pp.toFixed(1)}pp`
                              : "—"}
                          </span>
                          . {chopRecQ.data.control.verdict === "no measurable edge over a random walk"
                            ? "So the accuracy above is the arithmetic of a session that is most of the way through, not a forecasting edge — a random walk predicts its own endpoint just as well. This read describes the session behind you, which is all it claims."
                            : "So the accuracy above is not simply arithmetic."}
                        </p>
                      )}
"""
s = s.replace(anchor, add + anchor)
io.open(p, "w", encoding="utf-8").write(s)
print("tsx ok")

p2 = "frontend/lib/api.ts"
t = io.open(p2, encoding="utf-8").read()
a = "  /** Measured statements about what would improve the read"
assert t.count(a) == 1
t = t.replace(a, """  /** The negative control: the same pipeline over sessions that keep every real
   *  move size and randomise only the directions, averaged over several draws
   *  (one randomisation moved individual labels by 6-7pp, so one is not enough).
   *  `edge_pp` is MIX-MATCHED — comparing the two pooled accuracies is Simpson's
   *  paradox and duly produced a sign flip against the per-label picture. */
  control?: {
    overall_pct: number;
    real_pct: number;
    edge_pp: number | null;
    edge_pp_unmatched: number;
    beaten_on_every_directional_label: boolean | null;
    n: number;
    verdict: string;
    note: string;
  } | null;
  /** Measured statements about what would improve the read""")
a2 = "    clears_floor?: boolean;\n"
assert t.count(a2) == 1
t = t.replace(a2, "    clears_floor?: boolean;\n    control_pct?: number | null;\n"
                  "    beats_control_pp?: number | null;\n")
io.open(p2, "w", encoding="utf-8").write(t)
print("types ok")
