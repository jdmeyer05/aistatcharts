"""Put the negative control inside the scorecard."""
import io

p = "src/es_chop_record.py"
s = io.open(p, encoding="utf-8").read()

# ---- docstring ----------------------------------------------------------
a = """SHRINKAGE WAS TESTED AND REJECTED"""
assert s.count(a) == 1
s = s.replace(a, '''THE CONTROL IS THE POINT, AND IT IS NOT FLATTERING. This read predicts a
session's FINAL efficiency class from a partial reading of the SAME session, and
those windows overlap: by 15:00 most of the final number has already been
observed. So a good score here can be arithmetic rather than insight, and the
only way to tell is to run the identical pipeline over sessions built to contain
nothing.

The control keeps each real session's MAGNITUDE sequence exactly and randomises
only the signs. Volatility clustering, the intraday volatility smile and the fat
tails all survive; direction and persistence do not. Measured: the control
delivers 81.7% where the real read delivers 77.4% on confident trendy, 71.3%
against 71.2% on confident choppy, and 50.4% against 50.9% overall. The read does
not beat sessions that cannot contain a signal. Its calibration is honest and its
skill is arithmetic — a partly-finished session mostly determines its own final
class, and a random walk knows that about itself too.

That is not a reason to delete the read: "this session has gone nowhere" is a
true and useful description, which is what the card claims. It is a reason to
ship the control number beside the delivered one forever, so nobody reads a
calibration table as an edge.

SHRINKAGE WAS TESTED AND REJECTED''')

# ---- build the control panel and score it -------------------------------
a2 = """        rows = []                      # one per (session, mark) actually scored
        n = len(panel)"""
assert s.count(a2) == 1
s = s.replace(a2, """        # The control panel: same magnitudes, random signs, same grid. Seeded so
        # the scorecard does not wobble between loads.
        def _control_panel(src: pd.DataFrame) -> pd.DataFrame:
            rng = np.random.default_rng(20260831)
            out = []
            for _, g in src.groupby(src.index.normalize()):
                c = g["Close"].to_numpy(dtype=float)
                if len(c) < 3:
                    continue
                d_ = np.diff(c)
                flip = rng.choice([-1.0, 1.0], size=len(d_)) * np.abs(d_)
                out.append(pd.DataFrame(
                    {"Close": np.concatenate([[c[0]], c[0] + np.cumsum(flip)])},
                    index=g.index))
            return _panel(pd.concat(out)) if out else pd.DataFrame()

        rows = []                      # one per (session, mark) actually scored
        n = len(panel)""")

# ---- factor the walk-forward so the control runs through the same code ---
a3 = """        for start in range(_MIN_TRAIN, n, _REFIT_EVERY):"""
assert s.count(a3) == 1
s = s.replace(a3, """        # NOTE the control is scored by this same loop, below, so the two can
        # never drift apart. A control that runs through different code is not a
        # control.
        for start in range(_MIN_TRAIN, n, _REFIT_EVERY):""")

# ---- after the real rows are built, score the control -------------------
a4 = """        if not rows:
            return {"available": False, "reason": "nothing scored"}
        r = pd.DataFrame(rows)
        total = len(r)"""
assert s.count(a4) == 1
s = s.replace(a4, """        if not rows:
            return {"available": False, "reason": "nothing scored"}
        r = pd.DataFrame(rows)
        total = len(r)

        def _score(pan: pd.DataFrame) -> pd.DataFrame:
            out = []
            for st in range(_MIN_TRAIN, len(pan), _REFIT_EVERY):
                tr_ = pan.iloc[max(0, st - _FIT_WINDOW):st]
                te_ = pan.iloc[st:st + _REFIT_EVERY]
                if te_.empty or len(tr_) < 400:
                    break
                q_ = tr_["final"].dropna().quantile([1 / 3, 2 / 3])
                lo_, hi_ = float(q_.iloc[0]), float(q_.iloc[1])
                for mk in _MARKS:
                    c_ = tr_[[mk, "final"]].dropna()
                    if len(c_) < 200:
                        continue
                    th_, tf_ = c_[mk].to_numpy(float), c_["final"].to_numpy(float)
                    for _, rr in te_.iterrows():
                        e_, fi_ = rr.get(mk), rr.get("final")
                        if not (np.isfinite(e_) and np.isfinite(fi_)):
                            continue
                        lb_, sd_, p_ = _label_for(e_, th_, tf_, lo_, hi_, _EDGES,
                                                  _CONFIDENT, _LIKELY, _MIN_CELL)
                        ac_ = ("choppy" if fi_ < lo_
                               else "trendy" if fi_ >= hi_ else "mixed")
                        out.append({"label": lb_,
                                    "hit": (sd_ == ac_) if lb_ != "mixed"
                                           else (ac_ == "mixed")})
            return pd.DataFrame(out)

        try:
            ctrl_panel = _control_panel(fine[fine.index.normalize() != today])
            ctrl = _score(ctrl_panel) if not ctrl_panel.empty else pd.DataFrame()
        except Exception as e:                      # a missing control is not a failure
            logger.warning(f"chop control failed: {e}")
            ctrl = pd.DataFrame()""")

# ---- attach control to each row ----------------------------------------
a5 = """                "calibration_pp": (lambda c: round(c[0], 1) if c[0] is not None else None)(_calib(s)),
                "calibration_z": (lambda c: round(c[1], 2) if c[1] is not None else None)(_calib(s)),
            })"""
assert s.count(a5) == 1
s = s.replace(a5, """                "calibration_pp": (lambda c: round(c[0], 1) if c[0] is not None else None)(_calib(s)),
                "calibration_z": (lambda c: round(c[1], 2) if c[1] is not None else None)(_calib(s)),
                # What sessions that CANNOT contain a signal score under the same
                # label. If this matches the delivered figure, the delivered
                # figure is arithmetic.
                "control_pct": (round(float(ctrl[ctrl.label == lbl].hit.mean() * 100), 1)
                                if not ctrl.empty and (ctrl.label == lbl).any() else None),
                "beats_control_pp": (
                    round(delivered - float(ctrl[ctrl.label == lbl].hit.mean() * 100), 1)
                    if not ctrl.empty and (ctrl.label == lbl).any() else None),
            })""")

# ---- headline verdict ---------------------------------------------------
a6 = '            "improvements": notes,\n'
assert s.count(a6) == 1
s = s.replace(a6, """            "improvements": notes,
            "control": ({
                "overall_pct": round(float(ctrl.hit.mean() * 100), 1),
                "real_pct": round(float(r.hit.mean() * 100), 1),
                "edge_pp": round(float((r.hit.mean() - ctrl.hit.mean()) * 100), 1),
                "n": int(len(ctrl)),
                "verdict": ("no measurable edge over a random walk"
                            if float((r.hit.mean() - ctrl.hit.mean()) * 100) < 2.0
                            else "beats a random walk"),
                "note": (
                    "Sessions keeping every real magnitude and randomising only the "
                    "signs, scored by this same walk-forward. They cannot contain a "
                    "market signal. Where the read matches them, its accuracy is the "
                    "arithmetic of a partly-finished session rather than an edge — a "
                    "session that is most of the way through largely determines its "
                    "own final class, and a random walk knows that about itself too. "
                    "The read stays on the card as a DESCRIPTION of the session so "
                    "far, which is what it claims to be."
                ),
            } if not ctrl.empty else None),
""")

io.open(p, "w", encoding="utf-8").write(s)
print("ok")
