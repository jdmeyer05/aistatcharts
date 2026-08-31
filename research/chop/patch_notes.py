"""Stop the scorecard prescribing a remedy that was tested and rejected."""
import io

p = "src/es_chop_record.py"
s = io.open(p, encoding="utf-8").read()

# Record the negative result where someone would otherwise retry it.
a = """Coverage matters alongside accuracy: a read that says "mixed" four times in five
is well behaved and useless."""
assert s.count(a) == 1
s = s.replace(a, '''SHRINKAGE WAS TESTED AND REJECTED — do not retry it. The top of the reliability
curve runs about three points hot, which is the signature of a winner's curse:
the band with the highest observed rate is partly high BECAUSE it was highest.
The textbook fix is to shrink each band toward the base rate, so it was
implemented with an empirical-Bayes weight (estimated, not chosen) and scored the
same way as everything else here. It changed weighted calibration error not at
all — 1.43pp either way — was marginally worse in BOTH halves, and cost half a
point of coverage. The estimated prior weight came out near zero, which is the
explanation: between-band variance dominates within-cell noise, so the bands
genuinely differ and the cells are not thin enough to need shrinking. The
residual top-end gap is therefore not a cell-noise problem, and the scorecard
reports it rather than pretending a known-ineffective remedy would close it.

Coverage matters alongside accuracy: a read that says "mixed" four times in five
is well behaved and useless.''')

# The note itself: mark marginal deviations as marginal, and stop prescribing
# shrinkage now that it is known not to work.
a2 = '''            if cpp < 0:
                notes.append(f"{row['label']} promises {row['claimed_avg_pct']:.0f}% and "
                             f"delivers {row['delivered_pct']:.0f}% ({cpp:+.0f}pp, {z:.1f} SE) — "
                             f"it OVERSTATES, so the probabilities need shrinking toward the base "
                             f"rate rather than the threshold being moved.")'''
assert s.count(a2) == 1
s = s.replace(a2, '''            marginal = "Marginally: " if abs(z) < 3.0 else ""
            if cpp < 0:
                notes.append(f"{marginal}{row['label']} promises "
                             f"{row['claimed_avg_pct']:.0f}% and delivers "
                             f"{row['delivered_pct']:.0f}% ({cpp:+.0f}pp, {z:.1f} SE) — it "
                             f"overstates. Empirical-Bayes shrinkage was tested against this "
                             f"and did not improve calibration, so thin cells are not the "
                             f"cause; reported rather than corrected.")''')

a3 = '''            else:
                notes.append(f"{row['label']} promises {row['claimed_avg_pct']:.0f}% and "
                             f"delivers {row['delivered_pct']:.0f}% ({cpp:+.0f}pp, {z:.1f} SE) — "
                             f"it understates, so the threshold is genuinely conservative and "
                             f"could admit more readings.")'''
assert s.count(a3) == 1
s = s.replace(a3, '''            else:
                notes.append(f"{marginal}{row['label']} promises "
                             f"{row['claimed_avg_pct']:.0f}% and delivers "
                             f"{row['delivered_pct']:.0f}% ({cpp:+.0f}pp, {z:.1f} SE) — it "
                             f"understates, so the threshold is conservative and could admit "
                             f"more readings.")''')

io.open(p, "w", encoding="utf-8").write(s)
print("ok")
