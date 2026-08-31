"""Remove code left dead by the label removal, and bound the enumeration."""
import io

p = "src/es_chop.py"
s = io.open(p, encoding="utf-8").read()

# ---- 1. bound the exact enumeration by construction, not by luck ----------
a = """    if n <= 20:
        # bit i of k gives the sign of return i; the first sign is fixed at +1
        # because flipping every sign leaves |sum| unchanged.
        k = np.arange(1 << (n - 1), dtype=np.int64)"""
assert s.count(a) == 1
b = """    if n <= _EXACT_MAX_N:
        # bit i of k gives the sign of return i; the first sign is fixed at +1
        # because flipping every sign leaves |sum| unchanged.
        k = np.arange(1 << (n - 1), dtype=np.int64)"""
s = s.replace(a, b)

a2 = "_TODAY_TTL_S = 60        # today's bars are the live half; history is not\n"
assert s.count(a2) == 1
s = s.replace(a2, a2 + '''
# Above this the sign-flip null is sampled rather than enumerated. The bound is
# about MEMORY, not accuracy: enumeration allocates 2^(n-1) x (n-1) floats, which
# is 0.2 MB at n=12, 3.9 MB at n=16 and 80 MB at n=20. An hour holds 11 returns
# so the cap is never approached in practice — which is exactly why it needs to
# be set deliberately rather than left where nothing happens to reach it.
_EXACT_MAX_N = 16
''')

# ---- 2. drop the helpers that only fed the removed confidence gate --------
start = s.index("def _agreement(")
end = s.index("def _panel(")
mid = s[start:end]
assert "_hour_er" in mid and "_agreement" in mid
s = s[:start] + s[end:]

# _hour_panel used _hour_er purely for its jackknife half; `_er` already exists.
a3 = """            e, jk, _ = _hour_er(cc)
            if np.isfinite(e):
                per[k].append((e, jk))"""
assert s.count(a3) == 1
s = s.replace(a3, """            e = _er(cc)
            if np.isfinite(e):
                per[k].append(e)""")

a4 = """    for k, rows in per.items():
        if len(rows) < 200:
            continue
        er = np.array([r[0] for r in rows], dtype=float)
        lo, hi = np.quantile(er, [1 / 3, 2 / 3])
        d10, d90 = np.quantile(er, [0.10, 0.90])"""
assert s.count(a4) == 1
s = s.replace(a4, """    for k, rows in per.items():
        if len(rows) < 200:
            continue
        er = np.array(rows, dtype=float)
        lo, hi = np.quantile(er, [1 / 3, 2 / 3])""")

start2 = s.index("        # Typical robustness per class, so \"confident\" means robust FOR ITS KIND.")
end2 = s.index('        out[k] = {"er": er,')
s = s[:start2] + s[end2:]

a5 = '''        out[k] = {"er": er, "lo": float(lo), "hi": float(hi),
                  "d10": float(d10), "d90": float(d90),
                  "agree_median": agree_by_class, "n": len(er)}'''
assert s.count(a5) == 1
s = s.replace(a5, '''        out[k] = {"er": er, "lo": float(lo), "hi": float(hi), "n": len(er)}''')

# The docstring for _hour_panel still explains the removed machinery.
a6 = '''    """Per-bucket history: the efficiency distribution, and how robust each class
    typically is in that bucket.

    The second half is what stops the confidence word from being a synonym for
    the label. Measured over this sample, leave-one-out agreement is 1.00 for
    52% of TRENDY hours and for 0% of choppy ones — a straight line stays
    straight when you drop a bar, while a choppy hour's ratio is a small
    difference of large numbers and swings. An absolute agreement threshold
    would therefore print "confident" on trendy hours and never on choppy ones,
    which restates the label instead of qualifying it. So each class is scored
    against its OWN typical robustness.
    """'''
assert s.count(a6) == 1
s = s.replace(a6, '''    """Per-bucket efficiency history — one distribution per hourly slot.

    Only the distribution survives. This used to carry class cuts and a
    per-class robustness score for a confidence word, all of which went when the
    hourly labels did: an hour cannot be shown to have trended, so nothing built
    to qualify that claim has anything left to qualify. What remains is the
    ranking the card still prints, and the buckets stay separate because the
    15:30 slot is half the width of the others.
    """''')

io.open(p, "w", encoding="utf-8").write(s)
print("es_chop cleaned")

# ---- 3. hoist the per-iteration import ----------------------------------
p2 = "src/es_chop_record.py"
t = io.open(p2, encoding="utf-8").read()
a7 = """            from src.es_chop import _FIT_WINDOW
            tr = panel.iloc[max(0, start - _FIT_WINDOW):start]"""
assert t.count(a7) == 1
t = t.replace(a7, """            tr = panel.iloc[max(0, start - _FIT_WINDOW):start]""")
a8 = """        from src.es_chop import (_panel, _MARKS, _EDGES, _CONFIDENT, _LIKELY,
                                 _MIN_CELL)"""
assert t.count(a8) == 1
t = t.replace(a8, """        from src.es_chop import (_panel, _MARKS, _EDGES, _CONFIDENT, _LIKELY,
                                 _MIN_CELL, _FIT_WINDOW)""")
a9 = "        from src.es_chop import _FIT_WINDOW as _FIT_WINDOW_DOC\n"
assert t.count(a9) == 1
t = t.replace(a9, "")
t = t.replace("_FIT_WINDOW_DOC", "_FIT_WINDOW")
io.open(p2, "w", encoding="utf-8").write(t)
print("es_chop_record cleaned")
