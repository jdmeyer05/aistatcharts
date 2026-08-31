"""Append the hourly + track-record findings to the chop memory."""
import io

p = r"C:\Users\jdmey\.claude\projects\C--Windows-System32\memory\project_chop_trend.md"
s = io.open(p, encoding="utf-8").read()

anchor = "Research: `research/chop/`"
assert s.count(anchor) == 1

add = """## Per-hour character (added same day, 17af3de)

User asked for the chop/trend read broken down by hour in the SESSION PATH
table. Chose "each hour on its own" over "the cumulative read sampled hourly".

**THE PAYOFF, seen live on 2026-08-31**: 09:30 mixed(65) / 10:30 likely
trendy(72) / 11:30 CONFIDENT choppy(8) / 12:30 likely choppy(32) / 13:30 likely
trendy(71), while the cumulative read sat at the 11th percentile = confident
choppy. **The day went nowhere because consecutive hours trended in OPPOSITE
directions and cancelled**, not because any hour was internally noisy. Nothing
else on the card can show that.

**Hourly character is memoryless.** An hour predicts neither the next hour
(|corr| ≤ 0.074 across all six adjacent pairs; the one nominal hit, 10:30→11:30
t=-2.61, is 1 of 6 and negative) nor the session's final class (choppy hour →
choppy day 31-41% vs 33% base; only 09:30 has any lift at 41%/48%).

**Hourly distributions are near-identical 09:30-14:30** (median ~0.26-0.28) but
**15:30 is 0.408** — it is a HALF-WIDTH bucket and fewer bars raises ER
mechanically. Always give it its own cuts.

### What "confidence" can mean for a COMPLETED hour

Not what it means for a forecast. A finished hour has **no sampling
uncertainty**: ER is deterministic in its returns and **invariant to permuting
them**, so a permutation null is degenerate and there is nothing to bootstrap.
Only the CLASSIFICATION can be wrong → leave-one-out (closed form:
`|S-r_i|/(A-|r_i|)`).

**THE TRAP**: leave-one-out agreement is 1.00 for **52% of trendy hours and 0%
of choppy ones** — a straight line stays straight when you drop a bar, a choppy
ratio is a small difference of large numbers. An absolute threshold makes
"confident" a synonym for "trendy". Fix: score each class against **its own**
typical robustness. Gate = outer decile AND agreement ≥ that class's median.

## Walk-forward track record — `src/es_chop_record.py`, `/es-chop-record`

**Scored walk-forward, not replayed against the shipped fit** — 500-session
minimum window, refit every 21, class cuts refit too (cutting them on the whole
sample leaks the future into the definition of the outcome). Replaying against
the whole-sample fit asks the read how it does on the sessions that taught it.

742 sessions / 7,420 obs / 2023-09-05→2026-08-28. **Every label clears its floor
OUT OF SAMPLE**: confident trendy 77.2% vs 65 (n=898), confident choppy 74.3% vs
65 (n=417), likely choppy 56.5% vs 45, likely trendy 51.6% vs 45. Declines to
call 46.2%. Era 73.1%→79.3% (improving, not drifting).

**Improvement lever PRINTED, NOT APPLIED**: confident trendy has 12pp headroom,
so the threshold is conservative. Retuning against the window that scored it
would spend the out-of-sample evidence that makes the score worth reading —
that is a decision to take with fresh data.

**The hourly rows are deliberately UNSCORED**: they make no prediction, and a
finished hour has no outcome left to be right about. Scoring them would dress a
measurement as a forecast.

## Editing note

`patch_*.py` scripts in `research/chop/` exist because bash heredocs kept
breaking on apostrophes in long JSX/prose blocks — write the patch to a FILE
with the Write tool and run it. Also: after moving a JSX block, every
indentation-based anchor in later patches shifts. Cf. [[feedback_file_editing]].

Research: `research/chop/`"""

s = s.replace(anchor, add)
io.open(p, "w", encoding="utf-8").write(s)
print("memory updated")
