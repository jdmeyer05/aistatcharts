"""Fit the read on a ROLLING window so its cuts track the drifting distribution."""
import io

# ---------------- es_chop.py ----------------
p = "src/es_chop.py"
s = io.open(p, encoding="utf-8").read()

a = "_MIN_CELL = 40           # below this a band is widened rather than quoted\n"
assert s.count(a) == 1
s = s.replace(a, a + '''_FIT_WINDOW = 750        # sessions; the cuts track the tape rather than average it
''')

# Document the change where the module explains itself.
a2 = '''CONFIDENCE IS MEASURED, NOT WORDED.'''
assert s.count(a2) == 1
s = s.replace(a2, '''FITTED ON A ROLLING WINDOW, NOT ALL OF HISTORY. The efficiency distribution
drifts. Measured on this sample, the 33rd percentile of final-session efficiency
fell from 0.084 in 2021-22 to 0.070 in 2024-26 — sessions genuinely got choppier
— and a class cut fitted on everything since 2021 therefore calls 36.4% of
recent sessions choppy where a stationary cut would call 33.3%. That is not a
rounding error: it showed up in the walk-forward scorecard as the choppy side
delivering 4 points MORE than it promised while the trendy side delivered 3
points less, a symmetry that is the signature of stale cuts rather than a bad
threshold.

So the fit uses the most recent 750 sessions. Walk-forward, that cuts weighted
calibration error from 2.23 to 1.43 points with accuracy and coverage unchanged,
and the optimum is broad — every window from 600 to 1000 beats an expanding one.
Stated honestly, the gain is concentrated in the recent half of the test window
(2.39 -> 0.65 points) and the window is marginally WORSE early on (2.26 -> 2.58),
which is what a drift correction should look like: there is less to correct
before the drift has accumulated, and a shorter window is meanwhile noisier.
Production always forecasts forward with the full 750 behind it, so it lives in
the regime where the correction pays.

CONFIDENCE IS MEASURED, NOT WORDED.''')

# Trim the cumulative panel.
a3 = """        if panel.empty or len(panel) < 200:
            return {"available": False, "reason": "not enough history to calibrate"}
"""
assert s.count(a3) == 1
s = s.replace(a3, """        if panel.empty or len(panel) < 200:
            return {"available": False, "reason": "not enough history to calibrate"}
        # The cuts and band rates come from the recent window only. `panel` is
        # sorted by date, so this is the tail. Everything older still exists —
        # it is simply not allowed to define what "choppy" currently means.
        history_available = int(len(panel))
        panel = panel.iloc[-_FIT_WINDOW:] if len(panel) > _FIT_WINDOW else panel
"""
)

# Trim the hourly panel the same way, for the same reason.
a4 = """            hp = _hour_panel(fine[fine.index.normalize() != today])"""
assert s.count(a4) == 1
s = s.replace(a4, """            # Same rolling treatment: an hourly tercile cut fitted on 2021
            # over-calls choppy hours in 2026 for exactly the reason the
            # session-level one does. The hourly labels are descriptive and so
            # carry no calibration score of their own, which is precisely why
            # the correction is applied by argument rather than waiting for a
            # scorecard to catch it.
            _hist = fine[fine.index.normalize() != today]
            _days = _hist.index.normalize().unique()
            if len(_days) > _FIT_WINDOW:
                _hist = _hist[_hist.index.normalize() >= _days[-_FIT_WINDOW]]
            hp = _hour_panel(_hist)""")

# Report the window so a reader can see what the numbers were fitted on.
a5 = '            "sessions": int(len(col)),\n'
assert s.count(a5) == 1
s = s.replace(a5, '            "sessions": int(len(col)),\n'
                  '            "fit_window": _FIT_WINDOW,\n'
                  '            "history_available": history_available,\n')

a6 = '''                f"the {len(col):,} historical sessions AT THE SAME MARK, never across "
                "clock times. Class cuts are the terciles of final-session efficiency; "'''
assert s.count(a6) == 1
s = s.replace(a6, '''                f"the {len(col):,} most recent historical sessions AT THE SAME MARK, "
                "never across clock times. The window is rolling rather than the full "
                f"history ({history_available:,} sessions available) because the "
                "efficiency distribution drifts, and cuts fitted on 2021 over-call "
                "choppy today. Class cuts are the terciles of final-session efficiency; "''')

io.open(p, "w", encoding="utf-8").write(s)
print("es_chop ok")

# ---------------- es_chop_record.py ----------------
p2 = "src/es_chop_record.py"
t = io.open(p2, encoding="utf-8").read()

a7 = "_REFIT_EVERY = 21       # roughly monthly, the standard walk-forward cadence\n"
assert t.count(a7) == 1
t = t.replace(a7, a7 + '''
# The record must score what production actually does, so it uses the same
# rolling fit window. Scoring an expanding fit would grade a module that is not
# the one shipping.
''')

a8 = """            tr = panel.iloc[:start]"""
assert t.count(a8) == 1
t = t.replace(a8, """            from src.es_chop import _FIT_WINDOW
            tr = panel.iloc[max(0, start - _FIT_WINDOW):start]""")

a9 = '''                "Walk-forward: a {} -session minimum training window refitted "'''
# (method string is built with an f-string; patch it textually)
a9b = '''                f"Walk-forward: a {_MIN_TRAIN}-session minimum training window refitted "
                f"every {_REFIT_EVERY} sessions, each session scored only against sessions "
                "before it.'''
assert t.count(a9b) == 1
t = t.replace(a9b, '''                f"Walk-forward on the same rolling {_FIT_WINDOW_DOC}-session window "
                f"production fits on, refitted every {_REFIT_EVERY} sessions after a "
                f"{_MIN_TRAIN}-session warm-up, each session scored only against sessions "
                "before it.''')

a10 = """        out = {
            "available": True,"""
assert t.count(a10) == 1
t = t.replace(a10, """        from src.es_chop import _FIT_WINDOW as _FIT_WINDOW_DOC
        out = {
            "available": True,
            "fit_window": _FIT_WINDOW_DOC,""")

io.open(p2, "w", encoding="utf-8").write(t)
print("es_chop_record ok")
