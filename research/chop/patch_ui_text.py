"""The card described an expanding fit; it is now a rolling one."""
import io

p = "frontend/components/home/es-briefing.tsx"
s = io.open(p, encoding="utf-8").read()

a = """                          Walk-forward over {chopRecQ.data.sessions_scored?.toLocaleString("en-US")}{" "}
                          sessions ({chopRecQ.data.scored_from} to {chopRecQ.data.scored_to}):
                          a {chopRecQ.data.train_min}-session training window refitted every{" "}
                          {chopRecQ.data.refit_every}, each session scored only against sessions
                          before it. The class cuts are refitted too — cutting them on the whole
                          sample would leak the future into the definition of the outcome.
                        </p>"""
assert s.count(a) == 1
b = """                          Walk-forward over {chopRecQ.data.sessions_scored?.toLocaleString("en-US")}{" "}
                          sessions ({chopRecQ.data.scored_from} to {chopRecQ.data.scored_to}), on the
                          same rolling {chopRecQ.data.fit_window}-session window the read itself is
                          fitted on, refitted every {chopRecQ.data.refit_every}. Each session is
                          scored only against sessions before it, and the class cuts are refitted
                          too — cutting them on the whole sample would leak the future into the
                          definition of the outcome. Measured against what each reading actually
                          CLAIMED, never against the threshold that admitted it: cells clearing a
                          65% bar average far above it, so scoring them against 65% reads a
                          shortfall as headroom.
                        </p>"""
s = s.replace(a, b)
io.open(p, "w", encoding="utf-8").write(s)

p2 = "frontend/lib/api.ts"
t = io.open(p2, encoding="utf-8").read()
a2 = "  train_min?: number;\n  refit_every?: number;\n"
assert t.count(a2) == 1, t.count(a2)
t = t.replace(a2, "  train_min?: number;\n  refit_every?: number;\n"
                  "  /** Sessions in the rolling fit window. The read is deliberately NOT fitted\n"
                  "   *  on all available history: the efficiency distribution drifts, and cuts\n"
                  "   *  fitted on 2021 over-call choppy today. */\n"
                  "  fit_window?: number;\n"
                  "  /** Per-label calibration: delivered minus CLAIMED, and its sampling\n"
                  "   *  z-score. `margin_pp` compares against the floor instead and must not\n"
                  "   *  drive tuning — a floor is a minimum, not a forecast. */\n")
io.open(p2, "w", encoding="utf-8").write(t)
print("ok")
