"""Record the calibration findings."""
import io

p = r"C:\Users\jdmey\.claude\projects\C--Windows-System32\memory\project_chop_trend.md"
s = io.open(p, encoding="utf-8").read()

anchor = "## Editing note"
assert s.count(anchor) == 1

add = """## Calibration pass (5772a5d) — the scorecard was grading against the wrong number

**A FLOOR IS NOT A FORECAST — the generalizable trap.** The scorecard compared
delivered against the floor the label's NAME implies ("confident" = 65%). It
delivered 77%, so it reported *12pp of headroom* and advised loosening the
threshold. But cells that clear a 65% bar average **80.2%** — so 77.2% is 3pp
SHORT of what was claimed, not 12pp over. **Acting on that note would have made
the read worse while the scorecard congratulated it.** Always score a
probabilistic label against the mean claimed p of the readings that fired, never
against the cutoff that admitted them.

**Score the probability of the OUTCOME YOU ARE SCORING.** "Mixed" rows were
scored on "finished mixed" but carried `p_best` = max(p_chop, p_trend) — the odds
of one outcome against the occurrence of another. Lowest reliability bin read 32%
claimed vs 43% delivered, **z=9.2**, and the scorecard listed it as a defect.
Fix: `1 - p_chop - p_trend`. Bin went to +4.0pp / z=1.7; phantom finding gone.

**Report only past 2.5 SE.** Five labels scored ⇒ largest of five |z| ≈ 2.3 under
the null. "Calibrated within noise" is a real answer; a scorecard that always
finds something to fix is tuning itself into the sample.

### The real finding: DRIFT, not a bad threshold

Symmetric error — choppy side **+4.0pp**, trendy side **−3.0pp** — is the
signature of stale cuts. Confirmed: final-session efficiency p33 fell **0.084
(2021-22) → 0.070 (2024-26)**, and cuts fitted on everything since 2021 call
**36.4%** of recent sessions choppy vs 33.3% stationary.

**Fix = ROLLING 750-session fit window** (session-level AND hourly panels), not
all history. Walk-forward weighted calibration error **2.23 → 1.44pp**, accuracy
and coverage unchanged; likely choppy +4.0pp(3.2 SE) → +1.8pp(1.4 SE). Optimum is
broad (600-1000 all beat expanding). **Honestly: not a uniform win** — gain is in
the recent half (2.39→0.65) and it is slightly worse early (2.26→2.58), which is
what a drift correction should look like.

### SHRINKAGE TESTED AND REJECTED — do not retry

Top of the reliability curve runs ~3pp hot (winner's-curse signature), so
empirical-Bayes shrinkage (weight *estimated*, not chosen) was implemented and
scored: **1.43pp either way**, marginally worse in BOTH halves, −0.8pp coverage.
Estimated prior weight ≈ 0 because **between-band variance dominates within-cell
noise** — the bands genuinely differ and the cells are not thin. So the residual
top-end gap is NOT a cell-noise problem.

Final state: calibrated within **1.44pp** weighted, every label inside 3.3pp, the
one survivor (confident trendy −3.3pp at 2.5 SE) printing as *marginal*.

## Editing note"""

s = s.replace(anchor, add)
io.open(p, "w", encoding="utf-8").write(s)
print("memory updated")
