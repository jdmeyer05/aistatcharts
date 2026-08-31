"""Score the MIXED label against the probability of the outcome being scored."""
import io

p = "src/es_chop_record.py"
s = io.open(p, encoding="utf-8").read()

a = '''    if int(m.sum()) == 0:
        return "mixed", "mixed", float("nan")
    f = train_fin[m]
    p_chop, p_trend = float((f < lo_f).mean()), float((f >= hi_f).mean())
    p_best, side = (p_trend, "trendy") if p_trend >= p_chop else (p_chop, "choppy")
    if not np.isfinite(p_best) or p_best < likely:
        return "mixed", "mixed", p_best
    return (("confident " if p_best >= confident else "likely ") + side), side, p_best'''
assert s.count(a) == 1

b = '''    if int(m.sum()) == 0:
        return "mixed", "mixed", float("nan")
    f = train_fin[m]
    p_chop, p_trend = float((f < lo_f).mean()), float((f >= hi_f).mean())
    p_best, side = (p_trend, "trendy") if p_trend >= p_chop else (p_chop, "choppy")
    if not np.isfinite(p_best) or p_best < likely:
        # The probability returned must be the probability of the OUTCOME THIS
        # ROW IS SCORED ON, and a "mixed" row is scored on the session finishing
        # mixed. Returning p_best here — the chance of the class that merely came
        # closest — compared the odds of one outcome against the occurrence of
        # another, and it showed: the lowest reliability bin read 32% claimed
        # against 43% delivered at z=9.2, and the scorecard duly reported "mixed
        # understates" as something to fix. Nothing was wrong with the read.
        return "mixed", "mixed", max(0.0, 1.0 - p_chop - p_trend)
    return (("confident " if p_best >= confident else "likely ") + side), side, p_best'''
s = s.replace(a, b)
io.open(p, "w", encoding="utf-8").write(s)
print("ok")
