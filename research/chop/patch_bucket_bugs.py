"""Three bugs the audit found in the hourly row."""
import io

p = "src/es_chop.py"
s = io.open(p, encoding="utf-8").read()

# ---- 1. a partial bucket is not a bucket --------------------------------
a = "_BUCKET_MIN_FRAC = 0.8      # an hour missing a fifth of its bars is not an hour\n"
assert s.count(a) == 1
s = s.replace(a, '''# An hour is scored only when EVERY bar of it has arrived. The old rule accepted
# four fifths, which quietly reintroduced the one bias this module exists to
# avoid: efficiency falls with bar count, so a 10-bar hour ranked against a
# population of 12-bar hours reads systematically more trending than it was. The
# 5-minute grid is fixed and SPY trades every bucket, so a short hour means the
# feed has not caught up yet, not that the hour was short.
''')

s = s.replace("""        need = _BUCKET_BARS[k] * _BUCKET_MIN_FRAC
        if len(cc) < need:""",
"""        if len(cc) < _BUCKET_BARS[k]:""")

s = s.replace("""            if len(cc) < _BUCKET_BARS[k] * _BUCKET_MIN_FRAC:
                continue""",
"""            if len(cc) < _BUCKET_BARS[k]:
                continue""")

# ---- 2. an untested hour is not a coin flip -----------------------------
a2 = """        if np.isfinite(p_trend) and p_trend < 0.10:
            verdict, p = "trended", p_trend
        elif np.isfinite(p_chop) and p_chop < 0.10:
            verdict, p = "chopped", p_chop
        else:
            verdict, p = "coin flip", (min(p_trend, p_chop)
                                       if np.isfinite(p_trend) and np.isfinite(p_chop)
                                       else float("nan"))"""
assert s.count(a2) == 1
s = s.replace(a2, """        if not (np.isfinite(p_trend) and np.isfinite(p_chop)):
            # NOT a coin flip. "Coin flip" is a result — the null was run and the
            # hour did not beat it. When the test could not run at all, saying so
            # is a different statement, and collapsing the two prints an absence
            # as a measurement.
            verdict, p = "untested", float("nan")
        elif p_trend < 0.10:
            verdict, p = "trended", p_trend
        elif p_chop < 0.10:
            verdict, p = "chopped", p_chop
        else:
            verdict, p = "coin flip", min(p_trend, p_chop)""")

# ---- 3. `bars` meant returns while `bars_expected` meant closes ---------
a3 = '            "bars": int(len(r)), "bars_expected": _BUCKET_BARS[k],\n'
assert s.count(a3) == 1
s = s.replace(a3, '            "bars": int(len(cc)), "bars_expected": _BUCKET_BARS[k],\n'
                  '            "returns": int(len(r)),\n')

io.open(p, "w", encoding="utf-8").write(s)
print("es_chop ok")

# ---- the card must not print a verdict it does not have -----------------
p2 = "frontend/components/home/es-briefing.tsx"
t = io.open(p2, encoding="utf-8").read()
a4 = """                            if (!h || h.state !== "complete" || !h.verdict) {
                              return <td key={e.slot} className="text-right px-1 text-text-muted/60">—</td>;
                            }
                            const real = h.verdict !== "coin flip";"""
assert t.count(a4) == 1
t = t.replace(a4, """                            if (!h || h.state !== "complete" || !h.verdict ||
                                h.verdict === "untested") {
                              return <td key={e.slot} className="text-right px-1 text-text-muted/60">—</td>;
                            }
                            const real = h.verdict !== "coin flip";""")
io.open(p2, "w", encoding="utf-8").write(t)

p3 = "frontend/lib/api.ts"
u = io.open(p3, encoding="utf-8").read()
a5 = '    verdict?: "trended" | "chopped" | "coin flip";\n'
assert u.count(a5) == 1
u = u.replace(a5, '    /** "untested" when the hour holds too few returns for the null to run —\n'
                  '     *  distinct from "coin flip", which means the null RAN and was not beaten. */\n'
                  '    verdict?: "trended" | "chopped" | "coin flip" | "untested";\n')
a6 = "    bars?: number;\n    bars_expected?: number;\n"
assert u.count(a6) == 1
u = u.replace(a6, "    /** Closes in the bucket; `returns` is one fewer. Both are reported because\n"
                  "     *  the two were once conflated in a single field. */\n"
                  "    bars?: number;\n    bars_expected?: number;\n    returns?: number;\n")
io.open(p3, "w", encoding="utf-8").write(u)
print("card + types ok")
