"""Anytime-valid evidence for the champion/challenger gate.

WHY THIS EXISTS. The old gate ran a fresh percentile bootstrap every night and
turned "p > 0.05" into a permanent reject after two strikes. Three things were
wrong with that, and all three were arithmetic rather than judgement:

1. IT COULD NOT SAY YES. Experiment 9 was 11 pairs: 3 challenger wins, 8 ties,
   0 losses. With 3 discordant pairs the exact sign test's floor is 0.5**3 =
   0.125 -- every legitimate paired test returns exactly that (Wilcoxon under
   both zero methods, the exact 2048-permutation test, the sign test). Three
   unanimous wins is the STRONGEST evidence 3 decisive comparisons can hold and
   it is still 2.5x away from 0.05. You need 5 straight wins to reach 0.031.
2. THE CONFIDENCE INTERVAL WAS FORCED. With 8 of 11 differences at zero, the
   chance a bootstrap resample draws none of the 3 non-zero pairs is
   (8/11)**11 = 3.01% > 2.5% -- so the 2.5th percentile IS 0.000. The recorded
   [0.000, 0.087] was arithmetic, not evidence. A percentile bootstrap needs at
   least 4 discordant pairs to EVER exclude zero, at any n.
3. IT THREW THE EVIDENCE AWAY. Each night started over, so a challenger that
   was 3-0 on Monday and 3-0 on Tuesday was two independent non-significant
   runs rather than 6-0. Meanwhile "two rejects and you're retired" destroys
   ~72% of challengers that never lose a single pair, while giving a worthless
   one two shots at a 5% fluke -- worse on BOTH error rates at once.

WHAT REPLACES IT. A test martingale on the discordant pairs. Under the null
"the challenger is no better", wins among decisive pairs are Bernoulli(1/2), and
the Beta-mixture (Robbins' method of mixtures) has a closed form because Beta is
conjugate to the binomial. Ville's inequality then gives

    P( there EVER exists a time t with E_t >= 1/alpha )  <=  alpha

so you may look every single night, forever, with no correction and no alpha
spending. That is the entire point: evidence accumulates instead of resetting,
and looking is free. `1/max_t E_t` is an anytime-valid p-value.

Ties are recorded but are NOT evidence -- a tied pair carries exactly zero
information about which prompt is better, which is why 8 of our 11 pairs did
nothing but inflate n.

STATE IS THREE INTEGERS: cumulative wins, cumulative losses, and the running
maximum of E. Nothing else needs to persist.

Sources: Ramdas/Gruenwald/Vovk/Shafer, "Game-Theoretic Statistics and Safe
Anytime-Valid Inference" (arXiv:2210.01948); Vovk & Wang (Ann. Statist. 49(3),
arXiv:1912.06116) for `min(1, 1/E)` being the unique admissible e-to-p
calibrator; Shafer/Shen/Vereshchagin/Vovk (arXiv:0912.4269) for the a=b=1 case.
Full derivation and the simulated operating characteristics are in
Desktop/ai_loop_research/05_smalln_sequential_gates.md.
"""
from __future__ import annotations

import math

# ── the prior ─────────────────────────────────────────────────────
# Beta(2,2) truncated to (1/2, 1]. CHOSEN, not estimated, and the simulation
# behind the choice is in the research file: it beat uniform, Jeffreys and
# Beta(5,5) on every simulated alternative (+5 to +13 points of power, 2-5
# nights faster) while holding type-I at 0.019-0.025 over 60 nights of
# continuous peeking. Jeffreys Beta(0.5,0.5) is the WORST option here -- it puts
# prior mass near theta=0 and theta=1, which is not where a real prompt
# improvement lives. Beta(2,2) crosses one pair later than uniform on a pure-win
# record (8 vs 7) and dominates on every mixed record, which is the realistic
# case.
_A = 2
_B = 2

# ── thresholds ────────────────────────────────────────────────────
# T_PROMOTE = k/alpha with k = 4 surfaces, alpha = 0.05. The union bound over
# e-processes is a THRESHOLD MULTIPLY, not an alpha divide, so this buys
# family-wise error <= 0.05 across all four surfaces FOR ALL TIME, at a cost of
# 8 -> 11 all-win discordant pairs. Compare Bonferroni over 120 nightly tests:
# 12 all-win pairs PER NIGHT with no carryover. E-values pay per surface;
# p-values pay per night.
_T_PROMOTE = 80.0
# Symmetric 20:1 evidence against "challenger is better".
_T_RETIRE = 0.05
# Minimum discordant pairs at which E can reach _T_PROMOTE even on a perfect
# record. BELOW THIS, NO VERDICT IS ARITHMETICALLY POSSIBLE -- so the honest
# answer is "insufficient data", never "reject". This is the direct fix for
# turning "p > 0.05" into a permanent retirement.
_ND_MIN = 11
# At 100 discordant pairs a challenger that has not crossed has q <= 0.60 with
# high confidence; 200 leaves margin. ~67 nights at 3 discordant/night.
_ND_BUDGET = 200
# Region of practical equivalence on the MEAN PAIRED DIFFERENCE. Same margin the
# old gate used as a superiority threshold -- but applied to the question it can
# actually answer at our n. The property that makes superiority unreachable
# (most pairs tie, so s_d is small) is the same property that makes equivalence
# reachable.
_ROPE = 0.02
_EQUIV_MIN_N = 22


def _log_beta(m: int, n: int) -> float:
    return math.lgamma(m) + math.lgamma(n) - math.lgamma(m + n)


def _log_upper_half(m: int, n: int) -> float:
    """log( 1 - I_{1/2}(m, n) ) for integer m, n >= 1, computed exactly.

    Uses the binomial identity I_x(m,n) = P(X >= m), X ~ Binomial(m+n-1, x),
    so 1 - I_{1/2}(m,n) = P(X <= m-1) = 2^-(N) * sum_{j<m} C(N, j), N = m+n-1.
    Python ints are exact and unbounded, so the sum cannot overflow or lose
    precision the way a float incomplete-beta would at large t.
    """
    N = m + n - 1
    total = sum(math.comb(N, j) for j in range(m))
    return math.log(total) - N * math.log(2.0)


def evalue(wins: int, losses: int, a: int = _A, b: int = _B) -> float:
    """One-sided Beta(a,b)-mixture test martingale on discordant pairs.

    E_H0[E] == 1 exactly at every t (verified in the tests against the
    Binomial(t, 1/2) null), so this is a genuine test martingale and not merely
    an e-value. Under H0 it drifts nowhere; under a real improvement it grows.
    """
    if wins < 0 or losses < 0:
        raise ValueError("wins and losses must be non-negative")
    t = wins + losses
    if t == 0:
        return 1.0
    m, n = a + wins, b + losses
    log_e = (t * math.log(2.0)
             + _log_beta(m, n) + _log_upper_half(m, n)
             - _log_beta(a, b) - _log_upper_half(a, b))
    return math.exp(log_e)


def anytime_p(e_max: float) -> float:
    """The anytime-valid p-value. Vovk & Wang: min(1, 1/E) is the unique
    admissible calibrator, so there is no better conversion available."""
    if e_max <= 0:
        return 1.0
    return min(1.0, 1.0 / e_max)


def tost_equivalent(n: int, sum_d: float, sum_d2: float,
                    rope: float = _ROPE, alpha: float = 0.05) -> bool:
    """Two one-sided tests: is the mean paired difference inside +/- rope?

    Uses a (1 - 2*alpha) interval, per Schuirmann/Lakens -- two one-sided tests
    at alpha each, not a 95% interval. This is what separates "these prompts are
    the same" from "we do not have enough data", which the old gate conflated
    into a single 'inconclusive' that then counted as a strike.
    """
    if n < _EQUIV_MIN_N:
        return False
    mean = sum_d / n
    var = (sum_d2 - n * mean * mean) / (n - 1)
    if var <= 0:
        # Every pair identical. Degenerate, and the t-statistic would be
        # (0 - rope)/0 = -inf, declaring equivalence for any rope > 0. It IS
        # equivalence -- but say so from the data, not from a division by zero.
        return abs(mean) < rope
    from scipy import stats
    se = math.sqrt(var / n)
    crit = stats.t.ppf(1 - alpha, n - 1)
    lo, hi = mean - crit * se, mean + crit * se
    return -rope < lo and hi < rope


def verdict(wins: int, losses: int, e_max: float, *,
            n: int = 0, sum_d: float = 0.0, sum_d2: float = 0.0) -> tuple[str, str]:
    """(verdict, why) on the CUMULATIVE record for one challenger.

    Four outcomes where the old gate had two, because "not significantly better"
    and "shown to be the same" and "not enough data yet" are three different
    facts and only one of them justifies retiring a challenger.
    """
    nd = wins + losses
    e_now = evalue(wins, losses)
    if nd < _ND_MIN:
        return ("insufficient_data",
                f"{nd} discordant pairs of the {_ND_MIN} at which any verdict "
                f"becomes arithmetically possible (E={e_now:.2f}, "
                f"{wins}W-{losses}L) — keep collecting, this is not a reject")

    # PRACTICAL EQUIVALENCE OUTRANKS STATISTICAL SIGNIFICANCE, and the two are
    # not alternatives -- a challenger can be both. The sign test detects
    # DIRECTION and says nothing about MAGNITUDE, so 30 straight wins of +0.005
    # each crosses any evidence threshold you like while being worth nothing.
    # That is the cell the old _MIN_MARGIN check was really guarding, and it has
    # to be tested BEFORE promotion or the gate ships trivia with high
    # confidence.
    equivalent = tost_equivalent(n, sum_d, sum_d2)
    if e_max >= _T_PROMOTE:
        if equivalent:
            return ("trivial",
                    f"real but too small to ship: E={e_max:.1f} on {wins}W-{losses}L, "
                    f"yet the mean paired difference sits inside +/-{_ROPE} over "
                    f"{n} pairs — reliably better by an amount that does not matter")
        return ("win",
                f"E={e_max:.1f} crossed {_T_PROMOTE:.0f} on {wins}W-{losses}L "
                f"(anytime p={anytime_p(e_max):.4f}, valid across all surfaces "
                f"for all time)")
    if e_now <= _T_RETIRE:
        return ("reject",
                f"E={e_now:.4f} fell to {_T_RETIRE} on {wins}W-{losses}L — "
                f"evidence against this challenger, not absence of evidence")
    if equivalent:
        return ("equivalent",
                f"mean paired difference is inside +/-{_ROPE} at {n} pairs — "
                f"these prompts are the same; keep the champion, stop testing")
    if nd >= _ND_BUDGET:
        return ("futile",
                f"{nd} discordant pairs without crossing (E={e_now:.2f}, "
                f"{wins}W-{losses}L) — the improvement, if any, is below what "
                f"this budget can resolve")
    return ("collecting",
            f"E={e_now:.2f} on {wins}W-{losses}L (need {_T_PROMOTE:.0f}); "
            f"anytime p={anytime_p(max(e_max, e_now)):.3f}")


def pairs_to_promote(losses: int = 0) -> int:
    """How many straight wins are still needed to cross. For the log, so a
    'collecting' verdict says what it is waiting for rather than just waiting."""
    w = max(0, losses)
    for _ in range(500):
        if evalue(w, losses) >= _T_PROMOTE and w + losses >= _ND_MIN:
            return w
        w += 1
    return -1
