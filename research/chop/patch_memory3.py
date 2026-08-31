"""The deep-testing findings — the most important ones in this file."""
import io

p = r"C:\Users\jdmey\.claude\projects\C--Windows-System32\memory\project_chop_trend.md"
s = io.open(p, encoding="utf-8").read()

anchor = "## Editing note"
assert s.count(anchor) == 1

add = """## THE HEADLINE RESULT — chop is not measurable or predictable, anywhere

Read this before extending the module. Everything below is measured on 1,242
sessions / 8,708 hours of 5-min SPY, with 1-min cross-checks.

### An hour is a coin flip

Sign-flip null (keep each move's magnitude, randomise its direction; **permuting
returns is useless — ER is invariant to reordering**): **9.5%** of hours clear
p<0.10 trending, **10.0%** chopping. Chance is 10%. **No excess in either tail.**
Identical at **1-minute** resolution (60 bars/hour) — not a power problem, an
absence. Reversal rate fails too: 1-min sign-flip rate 0.505 vs 0.500 for a
random walk, cross-sectional spread only **1.07x** binomial noise.

The old percentile label ranked hours against a population that is *itself*
random walks, so its 70th percentile is a random walk. Hours it called "trendy"
cleared the null **29%** of the time, median p=0.17.

### The SESSION read's skill is ARITHMETIC — negative control

It predicts a session's FINAL class from a partial reading of the SAME session;
the windows OVERLAP. Control = same magnitudes, random signs (keeps vol
clustering, the smile, fat tails; kills direction). Averaged over 3 seeds
(n=22,260): confident trendy 77.4 vs **78.1**, confident choppy 71.2 vs **70.4**,
mix-matched edge **+1.4pp** = no edge. A random walk predicts its own endpoint
just as well. The read stays as DESCRIPTION; the control now ships beside it.

### Not predictable at any horizon

| horizon | result |
|---|---|
| within an hour | coin flip |
| hour → next hour | null, OOS R² **-0.001**, 34.5% vs 34.5% baseline |
| open hour → rest of day | null +0.020 (t=0.71), disjoint |
| day → next day | -0.091 t=-3.20 but **FAILS split-half** (-0.134 → -0.050) |
| prior-day range → character | null -0.034 |
| day of week | null (largest of five \\|z\\| = 1.54) |

Same-day range vs efficiency **+0.367** — related but distinct axes, which is
still the reason the module exists.

## Method traps worth carrying elsewhere

- **A FLOOR IS NOT A FORECAST.** Scoring a label against the threshold that
  admitted it (65%) instead of the mean probability it claimed (80.2%) turned a
  3pp SHORTFALL into "12pp of headroom" and recommended loosening the threshold.
- **Score the probability of the outcome you are scoring** — "mixed" rows carried
  `max(p_chop,p_trend)`, giving a phantom z=9.2 miscalibration.
- **ONE RANDOMISATION IS A SAMPLE.** A single control seed had the control
  beating the read by 6-7pp on every label; three seeds put every label within
  2.7pp. Average controls.
- **Pooled accuracy across labels = Simpson's paradox, and it fired**: control
  beat the read on every directional label while the pooled figure said the read
  was +4pp. Weight by how often the READ emits each label.
- **A "slot"/time-of-day predictor at t=+7.01 was the half-width 15:30 bucket.**
  Excluding it: +0.127 → **-0.006**. Any bucket of different width is a trap.
- **Tolerances reintroduce bar-count bias**: `_BUCKET_MIN_FRAC=0.8` let 10-bar
  hours be ranked against 12-bar hours; ER falls with n so they read *more
  trendy*. Score only complete buckets.
- **Absence ≠ result**: NaN p-value was printing as "coin flip". "Untested" and
  "tested and not beaten" are different claims.
- **The sign-flip null has an atom at zero** (equal magnitudes → p_chop =
  C(12,6)/2¹² = 0.226), which is why the choppy tail is harder to clear than the
  trending one (sessions: 12.9% vs 7.6%).

## Editing note"""

s = s.replace(anchor, add)
io.open(p, "w", encoding="utf-8").write(s)
print("memory updated")
