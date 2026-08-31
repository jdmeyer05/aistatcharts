"""The aggregate control comparison was confounded by label mix. Match it."""
import io

p = "src/es_chop_record.py"
s = io.open(p, encoding="utf-8").read()

# ---- average the control over several draws ------------------------------
a = """        try:
            ctrl_panel = _control_panel(fine[fine.index.normalize() != today])
            ctrl = _score(ctrl_panel) if not ctrl_panel.empty else pd.DataFrame()
        except Exception as e:                      # a missing control is not a failure
            logger.warning(f"chop control failed: {e}")
            ctrl = pd.DataFrame()"""
assert s.count(a) == 1
s = s.replace(a, """        # Averaged over several draws. One sign-randomisation is itself a sample:
        # two seeds moved the control's overall score by 3.5 points, which is
        # larger than any effect being argued about here.
        try:
            src_ = fine[fine.index.normalize() != today]
            parts = []
            for seed in _CONTROL_SEEDS:
                cp = _control_panel(src_, seed)
                if not cp.empty:
                    parts.append(_score(cp))
            ctrl = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        except Exception as e:                      # a missing control is not a failure
            logger.warning(f"chop control failed: {e}")
            ctrl = pd.DataFrame()""")

a2 = """        def _control_panel(src: pd.DataFrame) -> pd.DataFrame:
            rng = np.random.default_rng(20260831)"""
assert s.count(a2) == 1
s = s.replace(a2, """        def _control_panel(src: pd.DataFrame, seed: int) -> pd.DataFrame:
            rng = np.random.default_rng(seed)""")

a3 = "_REFIT_EVERY = 21       # roughly monthly, the standard walk-forward cadence\n"
assert s.count(a3) == 1
s = s.replace(a3, a3 + """
# Seeds for the sign-randomised control. Fixed so the scorecard does not wobble,
# and several because one randomisation is a sample rather than the truth.
_CONTROL_SEEDS = (20260831, 20260901, 20260902)
""")

# ---- mix-matched edge ----------------------------------------------------
a4 = """            "control": ({
                "overall_pct": round(float(ctrl.hit.mean() * 100), 1),
                "real_pct": round(float(r.hit.mean() * 100), 1),
                "edge_pp": round(float((r.hit.mean() - ctrl.hit.mean()) * 100), 1),
                "n": int(len(ctrl)),
                "verdict": ("no measurable edge over a random walk"
                            if float((r.hit.mean() - ctrl.hit.mean()) * 100) < 2.0
                            else "beats a random walk"),"""
assert s.count(a4) == 1
s = s.replace(a4, """            "control": ({
                "overall_pct": round(float(ctrl.hit.mean() * 100), 1),
                "real_pct": round(float(r.hit.mean() * 100), 1),
                # MIX-MATCHED, and the raw difference is kept only as a warning.
                # Comparing the two pooled accuracies is Simpson's paradox waiting
                # to happen and duly happened: the control beat the real read on
                # every directional label while the pooled figure said the read
                # was 4 points ahead, purely because the read says "mixed" more
                # often and scores better when it does. The honest comparison
                # weights each label's difference by how often the READ emits it.
                "edge_pp": _mix_matched_edge,
                "edge_pp_unmatched": round(
                    float((r.hit.mean() - ctrl.hit.mean()) * 100), 1),
                "beaten_on_every_directional_label": _ctrl_sweeps,
                "n": int(len(ctrl)),
                "verdict": ("no measurable edge over a random walk"
                            if (_mix_matched_edge is None or _mix_matched_edge < 2.0)
                            else "beats a random walk"),""")

# compute those two before `out`
a5 = """        mixed = next((x for x in out_rows if x["label"] == "mixed"), None)"""
assert s.count(a5) == 1
s = s.replace(a5, """        # Weighted by how often the READ emits each label, so the two are compared
        # on the same mix rather than on whichever mix each happened to produce.
        _mix_matched_edge, _ctrl_sweeps = None, None
        if not ctrl.empty:
            num = den = 0.0
            sweeps = True
            for row in out_rows:
                if row.get("never_fired") or row.get("beats_control_pp") is None:
                    continue
                num += row["beats_control_pp"] * row["n"]
                den += row["n"]
                if row["label"] != "mixed" and row["beats_control_pp"] > 0:
                    sweeps = False
            if den > 0:
                _mix_matched_edge = round(num / den, 1)
                _ctrl_sweeps = bool(sweeps)

        mixed = next((x for x in out_rows if x["label"] == "mixed"), None)""")

io.open(p, "w", encoding="utf-8").write(s)
print("ok")
