"""Test the SESSION read against a random walk too, and say so on the card."""
import io

p = "src/es_chop.py"
s = io.open(p, encoding="utf-8").read()

a = '''        med = float(np.median(hist))'''
assert s.count(a) == 1
s = s.replace(a, '''        # THE SAME TEST THE HOURLY ROWS GET. An hour cannot beat a coin flip; a
        # session sometimes can, because it has 78 bars rather than 12 and
        # because whole sessions do deviate from a random walk where single
        # hours do not — 12.9% of sessions clear the trending tail at p<0.10 and
        # 8.0% the choppy one, against a 10% chance rate. So the percentile below
        # is worth reporting, and this is what says whether it means anything.
        _sess_r = sess["Close"].to_numpy(dtype=float)[: i + 1]
        p_trend_sf, p_chop_sf = _sign_flip_p(np.diff(_sess_r))
        rw = None
        if np.isfinite(p_trend_sf) and np.isfinite(p_chop_sf):
            rw = {
                "p_trend": round(p_trend_sf, 4),
                "p_chop": round(p_chop_sf, 4),
                "verdict": ("trended" if p_trend_sf < 0.10
                            else "chopped" if p_chop_sf < 0.10 else "coin flip"),
                "note": (
                    "Sign-flip test on the session so far: every move keeps its size "
                    "and loses its direction, and this is how often chance alone "
                    "produces at least this much net progress. "
                    + (f"Only {p_chop_sf * 100:.1f}% of those worlds chop this hard."
                       if p_chop_sf < 0.10 else
                       f"Only {p_trend_sf * 100:.1f}% of those worlds trend this far."
                       if p_trend_sf < 0.10 else
                       "This session is not distinguishable from a random walk.")
                ),
            }

        med = float(np.median(hist))''')

a2 = '            "forward": fwd,\n'
assert s.count(a2) == 1
s = s.replace(a2, '            "forward": fwd,\n'
                  '            # Whether the percentile above describes anything a coin flip\n'
                  '            # would not have produced. The hourly rows almost never pass\n'
                  '            # this; a session sometimes does.\n'
                  '            "random_walk": rw,\n')

io.open(p, "w", encoding="utf-8").write(s)
print("es_chop ok")

# ---- card ----
p2 = "frontend/components/home/es-briefing.tsx"
t = io.open(p2, encoding="utf-8").read()
anchor = """                  {/* The null travels WITH the reading rather than in a"""
assert t.count(anchor) == 1
add = """                  {/* Does the percentile above describe anything chance would
                      not have produced? For an HOUR the answer is essentially
                      never — which is why the hourly labels were removed. For a
                      session it is sometimes yes, and when it is, this is the
                      strongest statement on the card: not "low compared with
                      other days" but "chance alone rarely does this". */}
                  {d.chop_trend.random_walk && (
                    <p className="text-[0.6rem] leading-snug">
                      <span
                        className={
                          d.chop_trend.random_walk.verdict === "chopped" ? "text-amber-400 font-medium"
                            : d.chop_trend.random_walk.verdict === "trended" ? "text-accent font-medium"
                              : "text-text-muted"
                        }
                      >
                        {d.chop_trend.random_walk.verdict === "coin flip"
                          ? "Not distinguishable from a random walk"
                          : `Beat a random walk: ${d.chop_trend.random_walk.verdict}`}
                      </span>{" "}
                      <span className="text-text-muted tabular-nums">
                        (p={(d.chop_trend.random_walk.verdict === "trended"
                          ? d.chop_trend.random_walk.p_trend
                          : d.chop_trend.random_walk.p_chop
                        )?.toFixed(3)})
                      </span>
                    </p>
                  )}
"""
t = t.replace(anchor, add + anchor)
io.open(p2, "w", encoding="utf-8").write(t)

p3 = "frontend/lib/api.ts"
u = io.open(p3, encoding="utf-8").read()
a3 = "  hourly_note?: string;\n"
assert u.count(a3) == 1
u = u.replace(a3, """  /** Sign-flip test on the session so far. The hourly rows almost never clear
   *  it; a session sometimes does, and when it does this is a stronger claim
   *  than any percentile — chance alone rarely produces it. */
  random_walk?: {
    p_trend: number; p_chop: number;
    verdict: "trended" | "chopped" | "coin flip";
    note: string;
  } | null;
  hourly_note?: string;
""")
io.open(p3, "w", encoding="utf-8").write(u)
print("card + types ok")
