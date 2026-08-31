"""Replace the floor-based improvement rule with a real calibration test."""
import io

p = "src/es_chop_record.py"
s = io.open(p, encoding="utf-8").read()

# ---- 1. docstring: record why the floor comparison was wrong -------------
a = """HOW IT IMPROVES. The gap between what a label CLAIMS and what it DELIVERS is
the lever. A "confident" bucket delivering well above its floor is leaving calls
on the table and the threshold could come down; one delivering below it is
miscalibrated and must go up. Coverage matters alongside accuracy — a read that
says "mixed" four times in five is well behaved and useless. Both print, plus an
era split, because a fit that was right on 2022 and wrong on 2026 is a stale fit
rather than a broken idea, and those need different fixes.
"""
assert s.count(a) == 1
b = '''HOW IT IMPROVES — AND THE COMPARISON THAT LOOKS RIGHT AND IS NOT. The first
version of this file scored each label against the FLOOR its name implies:
"confident" claims 65%, it delivered 77%, so it looked like 12 points of headroom
and the file duly recommended loosening the threshold to call more sessions.

That recommendation was backwards. A floor is a minimum, not a forecast. Cells
that clear a 65% threshold have an average well above 65% — measured here, 80.2%
— so delivering 77% against that floor is not headroom at all. Against what the
label ACTUALLY claimed it is 3 points SHORT, and loosening the threshold would
have made the read worse while the scorecard congratulated it.

So calibration is measured against the mean claimed probability of the readings
that fired, never against the threshold that admitted them. And a deviation is
only reported when it clears sampling noise: with five labels scored, the largest
of five z-scores runs about 2.3 under the null, so the bar is 2.5 rather than the
usual 2. Silence — "calibrated within noise" — is a real and common answer, and a
scorecard that always finds something to fix is a scorecard tuning itself into
the sample.

Coverage matters alongside accuracy: a read that says "mixed" four times in five
is well behaved and useless. An era split prints too, because a fit that was
right on 2023 and wrong on 2026 is a stale fit rather than a broken idea, and
those need different fixes.
'''
s = s.replace(a, b)

# ---- 2. keep the floor, but make calibration the headline stat -----------
a2 = """                "clears_floor": bool(fl is None or delivered >= fl),
                "margin_pp": round(delivered - fl, 1) if fl is not None else None,
            })"""
b2 = """                "clears_floor": bool(fl is None or delivered >= fl),
                "margin_pp": round(delivered - fl, 1) if fl is not None else None,
            })"""
assert s.count(a2) == 1

# ---- 3. add calibration error + z to every row --------------------------
a3 = """        out_rows = []
        for lbl in ("confident trendy", "likely trendy", "mixed",
                    "likely choppy", "confident choppy"):"""
b3 = """        def _calib(s_):
            \"\"\"Delivered minus CLAIMED, in points, with its sampling z-score.

            The claim is the mean of the per-reading probabilities that fired,
            not the threshold that let them through. Those are different numbers
            and only the first is a forecast.\"\"\"
            if s_.empty or not s_.p.notna().any():
                return None, None
            claimed = float(s_.p.mean())
            delivered_ = float(s_.hit.mean())
            n_ = len(s_)
            se = float(np.sqrt(max(claimed * (1 - claimed), 1e-9) / n_))
            return (delivered_ - claimed) * 100, (delivered_ - claimed) / se if se > 0 else None

        out_rows = []
        for lbl in ("confident trendy", "likely trendy", "mixed",
                    "likely choppy", "confident choppy"):"""
assert s.count(a3) == 1
s = s.replace(a3, b3)

a4 = """                "clears_floor": bool(fl is None or delivered >= fl),
                "margin_pp": round(delivered - fl, 1) if fl is not None else None,
            })"""
b4 = """                "clears_floor": bool(fl is None or delivered >= fl),
                "margin_pp": round(delivered - fl, 1) if fl is not None else None,
                # The honest calibration statistic. `margin_pp` above is kept
                # because the floor is what the WORD promises a reader, but it
                # must never drive a tuning decision — see the module docstring.
                "calibration_pp": (lambda c: round(c[0], 1) if c[0] is not None else None)(_calib(s)),
                "calibration_z": (lambda c: round(c[1], 2) if c[1] is not None else None)(_calib(s)),
            })"""
assert s.count(a4) == 1
s = s.replace(a4, b4)

# ---- 4. reliability curve: the standard diagnostic -----------------------
a5 = """        # Drift: the same labels over the first and second half of the SCORED"""
b5 = """        # A reliability curve, which is the diagnostic a per-label table cannot
        # be: it bins by what was CLAIMED rather than by which word was printed,
        # so a read that is calibrated on average while being optimistic at the
        # top and pessimistic in the middle shows up here and nowhere else.
        reliability = []
        rr = r[r.p.notna()]
        if len(rr) >= 200:
            edges = [0.0, 0.35, 0.45, 0.55, 0.65, 0.75, 1.01]
            for i in range(len(edges) - 1):
                seg = rr[(rr.p >= edges[i]) & (rr.p < edges[i + 1])]
                if len(seg) < 50:
                    continue
                cl, dv = float(seg.p.mean()), float(seg.hit.mean())
                se = float(np.sqrt(max(cl * (1 - cl), 1e-9) / len(seg)))
                reliability.append({
                    "claimed_pct": round(cl * 100, 1),
                    "delivered_pct": round(dv * 100, 1),
                    "gap_pp": round((dv - cl) * 100, 1),
                    "z": round((dv - cl) / se, 2) if se > 0 else None,
                    "n": int(len(seg)),
                })

        # Drift: the same labels over the first and second half of the SCORED"""
assert s.count(a5) == 1
s = s.replace(a5, b5)

# ---- 5. rewrite the improvement notes -----------------------------------
a6 = """        notes = []
        for row in out_rows:
            if row.get("never_fired") or row["label"] == "mixed":
                continue
            m = row.get("margin_pp")
            if m is None:
                continue
            if m < 0:
                notes.append(f"{row['label']} delivers {row['delivered_pct']:.0f}% against a "
                             f"{row['claimed_floor_pct']:.0f}% floor — the threshold is too "
                             f"loose and should rise.")
            elif m > 12:
                notes.append(f"{row['label']} delivers {row['delivered_pct']:.0f}% against a "
                             f"{row['claimed_floor_pct']:.0f}% floor, {m:.0f}pp of headroom — "
                             f"the threshold is conservative and could be relaxed to call more "
                             f"sessions.")"""
b6 = """        notes = []
        Z_BAR = 2.5     # five labels scored; the largest of five |z| runs ~2.3 under the null
        worst = None
        for row in out_rows:
            if row.get("never_fired"):
                continue
            z, cpp = row.get("calibration_z"), row.get("calibration_pp")
            if z is None or cpp is None:
                continue
            if worst is None or abs(z) > abs(worst[1]):
                worst = (row["label"], z, cpp)
            if abs(z) < Z_BAR:
                continue
            if cpp < 0:
                notes.append(f"{row['label']} promises {row['claimed_avg_pct']:.0f}% and "
                             f"delivers {row['delivered_pct']:.0f}% ({cpp:+.0f}pp, {z:.1f} SE) — "
                             f"it OVERSTATES, so the probabilities need shrinking toward the base "
                             f"rate rather than the threshold being moved.")
            else:
                notes.append(f"{row['label']} promises {row['claimed_avg_pct']:.0f}% and "
                             f"delivers {row['delivered_pct']:.0f}% ({cpp:+.0f}pp, {z:.1f} SE) — "
                             f"it understates, so the threshold is genuinely conservative and "
                             f"could admit more readings.")
        if not notes and worst is not None:
            notes.append(f"Calibrated within noise on every label — the largest deviation is "
                         f"{worst[0]} at {worst[2]:+.1f}pp ({worst[1]:.1f} SE, and the bar is "
                         f"{Z_BAR:.1f} across five labels). No threshold change is supported by "
                         f"this window.")"""
assert s.count(a6) == 1
s = s.replace(a6, b6)

# ---- 6. attach the curve --------------------------------------------------
a7 = '            "eras": eras,\n'
assert s.count(a7) == 1
s = s.replace(a7, '            "eras": eras,\n            "reliability": reliability,\n')

io.open(p, "w", encoding="utf-8").write(s)
print("ok")
