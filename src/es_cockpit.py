"""Assembles the ES cockpit from one bar fetch and one session model.

Every module here needs the same 5-minute bars and the same answer to "which
session is this". Fetching and deciding separately in each is how they drift
apart, so `es_levels.session_frames` does both once and everything downstream
is handed the result.

It also produces the one thing none of the individual modules can: the
CONDITIONS GATE — a read on whether this session suits intraday trading at all.
Most intraday damage comes from trading a session that offered nothing rather
than from reading a level wrong, and no single module can see that, because it
is a conjunction: a quiet expected move AND midday AND long dealer gamma AND
thin participation is a very different session from any one of those alone.

The gate is deliberately about CONDITIONS, never direction. It says whether the
session is worth engaging, not which way to lean.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

logger = logging.getLogger(__name__)

_TZ = "America/New_York"


def _level(levels: list[dict], key: str) -> dict | None:
    return next((l for l in levels if l.get("key") == key), None)


def _normal_range(frames: dict) -> float | None:
    """ES's own trailing median RTH session range, excluding the live one.

    The denominator for the session-character multiplier. Computed on ES rather
    than borrowed from the SPY study because the multiplier only transfers
    between instruments if each is measured against ITSELF — the point of a
    unit-free ratio is that neither the basis nor the contract size enters it.
    """
    try:
        rth = frames.get("rth")
        if rth is None or rth.empty:
            return None
        g = rth.groupby("session").agg(hi=("High", "max"), lo=("Low", "min"))
        rng = (g["hi"] - g["lo"]).dropna()
        # Drop the developing session — a half-formed range would drag the
        # median down and inflate every multiplier measured against it.
        prior = rng.iloc[-21:-1]
        return float(prior.median()) if len(prior) >= 5 else None
    except Exception as e:
        logger.warning(f"es-cockpit: normal range failed: {e}")
        return None


def conditions_gate(levels: dict, intraday: dict, em: dict, gamma: dict,
                    session: dict, schedule: list[dict],
                    breadth: dict | None = None, candles: dict | None = None) -> dict:
    """Is this session worth trading, on conditions alone?

    Scored from independent factors, each of which can only ever ADD or SUBTRACT
    a stated amount, so the verdict is traceable to the reasons beside it.
    Nothing here is directional.
    """
    reasons: list[dict] = []
    score = 0

    phase = (session or {}).get("phase") or ""
    mode = (levels or {}).get("mode")

    # Session phase. The opening hour and the closing drive carry the ranges;
    # midday is where intraday traders bleed on chop.
    if phase == "rth_open":
        score += 2
        reasons.append({"factor": "Opening hour", "effect": +2,
                        "why": "Widest ranges and heaviest volume of the session."})
    elif phase == "rth_close":
        score += 1
        reasons.append({"factor": "Closing drive", "effect": +1,
                        "why": "MOC imbalances build into 15:50; directional but fast."})
    elif phase == "rth_midday":
        score -= 2
        reasons.append({"factor": "Midday", "effect": -2,
                        "why": "Volume fades and ranges compress — the highest chop risk of the day."})
    elif phase == "post":
        score -= 2
        reasons.append({"factor": "Post-settlement", "effect": -2,
                        "why": "Cash session is over; ES stays open but thin, and earnings land here."})
    elif phase in ("overnight", "europe"):
        score -= 1
        reasons.append({"factor": "Globex session", "effect": -1,
                        "why": "Outside cash hours — thinner liquidity, wider spreads, more false breaks."})
    elif mode == "premarket":
        reasons.append({"factor": "Pre-open", "effect": 0,
                        "why": "The cash session hasn't started; this is planning time, not trading time."})
    elif phase == "closed":
        reasons.append({"factor": "Market closed", "effect": 0,
                        "why": "Nothing to trade — this is preparation for the next session."})

    # THE TWO EXPECTED-MOVE FACTORS SHARE ONE INPUT, so they can charge the
    # session twice for a single observation. `consumed.pct` is the realised
    # range divided by the same implied range that "narrow expected move" tests:
    # an implied figure that is too small makes the day look both airless AND
    # already spent. Observed 2026-08-03 — VIX1D priced 54 handles, the
    # bar-conditioned study priced 71, and the pair supplied -4 of a -7 that
    # tripped the stand-aside band on a session the second estimator called
    # ordinary (63 of 71 handles, 89% spent).
    #
    # `range_divergence` is the check. It is a second estimate of the SAME
    # quantity — the session's high-low — built from unrelated inputs: what
    # options are paying versus how bars conditioned like today's actually
    # resolved. Neither is the truth, and this module has no claim to which is
    # the better forecast. So when they disagree materially the shared input is
    # CONTESTED, and the factors resting on it abstain rather than score. That is
    # the call the card already makes for credit-vs-equity and NYSE TICK: an
    # honest abstention beats a confident -4 built on whichever estimator happens
    # to be wired in as the headline.
    div = ((candles or {}).get("vs_implied") or {})
    empirical_range = div.get("empirical_p50")
    contested = div.get("label") in ("implied cheap", "implied rich")
    contest_why = (
        f"Options price {div.get('implied_range', 0):.0f} handles for the session; bars "
        f"conditioned like today's have delivered {div.get('empirical_p50', 0):.0f} "
        f"({div.get('ratio', 0):.2f}x, {div.get('label')}). Both room-to-run factors rest on "
        "this one number, so they abstain rather than score a contested input."
    ) if contested else None

    # Expected move. A day priced for nothing rarely delivers a trend.
    headline = (em or {}).get("headline") or {}
    em_pct = headline.get("pct")
    if contested and em_pct is not None:
        # `surface` — an abstention that changes the verdict has to be readable on
        # the card, not parked in a tooltip. A factor scoring 0 for a REASON is
        # different information from one scoring 0 because it agreed.
        reasons.append({"factor": "Expected move contested", "effect": 0,
                        "why": contest_why, "surface": True})
    elif em_pct is not None:
        if em_pct >= 1.0:
            score += 2
            reasons.append({"factor": "Wide expected move", "effect": +2,
                            "why": f"Priced for {em_pct:.2f}% — enough room for a trade to work."})
        elif em_pct <= 0.5:
            score -= 2
            reasons.append({"factor": "Narrow expected move", "effect": -2,
                            "why": f"Priced for only {em_pct:.2f}% — little room before the day is done."})

    # How much of it is already spent — measured against BOTH estimators, and
    # scored only where they agree. Against the implied range alone this fires
    # whenever the implied range is small, which is the coupling described above.
    consumed = (em or {}).get("consumed") or {}
    cpct = consumed.get("pct")
    realised = consumed.get("range")
    cpct_emp = (realised / empirical_range * 100) if (realised and empirical_range) else None
    if cpct is not None and phase.startswith("rth"):
        both = [p for p in (cpct, cpct_emp) if p is not None]
        emp_note = (f" (against the bar-conditioned {empirical_range:.0f}-handle estimate it is "
                    f"{cpct_emp:.0f}%)") if cpct_emp is not None else ""
        if min(both) >= 110:
            score -= 2
            reasons.append({"factor": "Range spent", "effect": -2,
                            "why": f"{cpct:.0f}% of the expected range already covered{emp_note} — "
                                   "chasing here pays up."})
        elif max(both) <= 40:
            score += 1
            reasons.append({"factor": "Range still available", "effect": +1,
                            "why": f"Only {cpct:.0f}% of the expected range used{emp_note}."})
        elif cpct_emp is not None and (cpct >= 110 or cpct <= 40):
            # One estimator clears the threshold and the other does not. The
            # session is only "spent" or "coiled" if both say so.
            # Wording stays direction-neutral: this branch fires both when the
            # implied range is the smaller estimate and when it is the larger,
            # and "but only 130%" reads as nonsense in the second case.
            reasons.append({"factor": "Range spent — estimators disagree", "effect": 0,
                            "why": f"{cpct:.0f}% of the options-implied range is covered against "
                                   f"{cpct_emp:.0f}% of the range bars like today's have delivered. "
                                   "The two estimators disagree, and one reading is not enough to "
                                   "score the session on.",
                            "surface": True})

    # Dealer gamma regime. The layer that actually drives intraday hedging is the
    # 0DTE book, and on a weekend or holiday reopen it is not populated yet —
    # observed 2026-08-02, where the block read "LONG GAMMA · 0DTE 0%" off Friday's
    # later expiries and still paid -1 into a score with only two readable factors.
    # The profile is worth SHOWING; it is not worth SCORING an intraday session on
    # when the dominant layer is absent from it.
    if (gamma or {}).get("available") and not (gamma or {}).get("zero_dte_share"):
        reasons.append({"factor": "Dealer gamma — no 0DTE book", "effect": 0,
                        "why": "The chain carries no 0DTE open interest for this session, so the "
                               "layer that dominates intraday hedging is missing. The regime shown "
                               "is built on later expiries and does not score the session.",
                        "surface": True})
    elif (gamma or {}).get("available"):
        if gamma.get("regime") == "short":
            score += 2
            reasons.append({"factor": "Short gamma", "effect": +2,
                            "why": "Dealer hedging amplifies moves — trends extend and follow-through is real."})
        else:
            score -= 1
            reasons.append({"factor": "Long gamma", "effect": -1,
                            "why": "Dealer hedging dampens moves — breakouts tend to fail and price rotates."})

    # Participation.
    rv = (intraday or {}).get("relative_volume") or {}
    if rv.get("available"):
        ratio = rv.get("ratio")
        if ratio and ratio >= 1.3:
            score += 1
            reasons.append({"factor": "Heavy volume", "effect": +1,
                            "why": f"{ratio:.2f}x normal participation — moves have backing."})
        elif ratio and ratio <= 0.75:
            score -= 2
            reasons.append({"factor": "Thin volume", "effect": -2,
                            "why": f"{ratio:.2f}x normal participation — breaks fail more often."})

    # Scheduled risk still ahead.
    upcoming = [e for e in (schedule or [])
                if e.get("status") == "upcoming" and e.get("impact") == "high"]
    imminent = [e for e in upcoming if 0 < (e.get("minutes_away") or 9999) <= 45]
    if imminent:
        score -= 2
        reasons.append({"factor": "High-impact print imminent", "effect": -2,
                        "why": f"{imminent[0]['name']} in {imminent[0]['minutes_away']} min — "
                               "liquidity thins and spreads widen into it."})
    elif upcoming:
        reasons.append({"factor": "High-impact print later", "effect": 0,
                        "why": f"{upcoming[0]['name']} at {upcoming[0]['time_et']} — the session "
                               "before it is usually a holding pattern."})

    # Breadth. An index grinding higher on negative net advancers is a
    # low-quality tape: the move is carried by a handful of names and does not
    # follow through. That is a CONDITIONS statement, not a directional one — it
    # says moves are unreliable, not which way they go — so it belongs here.
    # It is also not double-counting the volume factor above: that measures
    # participation, this measures dispersion, and they disagree often.
    #
    # DELIBERATELY ASYMMETRIC. Only the divergent case scores, and only -1. The
    # bands below were tuned in an audit pass, and a factor that fired on every
    # session would shift the whole score distribution under them. Confirmation
    # is the common case and carries 0, so the gate reads exactly as it did
    # before except on the sessions where breadth is actually telling you
    # something. One consequence is intended and worth stating: a session
    # already sitting at -1 tips to "poor" on a divergence alone, which is the
    # right answer — mildly awkward conditions plus a narrow tape is worse than
    # either by itself.
    if (breadth or {}).get("available") and (breadth or {}).get("live"):
        div = (breadth or {}).get("divergence") or {}
        net = breadth.get("net_advancers_pct")
        if div.get("label") == "divergent":
            score -= 1
            reasons.append({"factor": "Breadth diverging", "effect": -1,
                            "why": f"Net advancers {net:+.0f}% against the index — the move is "
                                   "carried by a few names and follow-through is unreliable."})
        elif div.get("label") == "confirmed":
            reasons.append({"factor": "Breadth confirms", "effect": 0,
                            "why": f"Net advancers {net:+.0f}% agree with the index — the move "
                                   "has the market behind it rather than a handful of names."})

    # Day type.
    dt = (intraday or {}).get("day_type") or {}
    if dt.get("available") and dt.get("label") == "trend":
        score += 2
        reasons.append({"factor": "Trend day", "effect": +2,
                        "why": "Range is 2x the initial balance and holding near the extreme."})
    elif dt.get("available") and dt.get("label") == "balance":
        score -= 1
        reasons.append({"factor": "Balancing day", "effect": -1,
                        "why": "Contained inside the initial balance — rotational until an edge breaks."})

    # Scoring a session that isn't happening is meaningless — a weekend is not
    # a "poor" day to trade, it is no day at all. The factors are still worth
    # showing as preparation for the next open.
    if phase == "closed":
        return {
            "available": bool(reasons),
            "score": None,
            "verdict": "market closed",
            "note": ("Nothing to trade. The factors below are what the next session is "
                     "setting up with, as it stands now."),
            "reasons": sorted(reasons, key=lambda r: r["effect"]),
            # Same shape as the live return below — the card reads these fields
            # unconditionally, and an early return that omits them makes a
            # closed session look like an older API build.
            "factors_scored": sum(1 for r in reasons if r["effect"] != 0),
            "factors_zero_effect": sum(1 for r in reasons if r["effect"] == 0),
            "disclaimer": ("Conditions only — this says whether the session suits intraday "
                           "trading, never which way to lean."),
        }

    # Bands. The score is a sum of independent effects, so zero means neutral
    # and has to read as workable — an earlier cut labelled a 0 "poor", which
    # is simply wrong. One mild negative is likewise not a bad session; it
    # takes two or more to be genuinely working against you. Long gamma is the
    # more common regime and midday is a third of the session, so the strongest
    # warning is reserved for -5 or worse, where several factors are hostile at
    # once, rather than firing on an ordinary quiet afternoon.
    n_scored = sum(1 for r in reasons if r["effect"] != 0)

    if score >= 4:
        verdict, note = "favourable", ("Conditions line up for intraday work. This is when to take "
                                       "the setups you actually wait for.")
    elif score >= -1:
        verdict, note = "workable", "Nothing exceptional either way. Be selective."
    # "Several conditions are hostile at once" is a statement about a CONJUNCTION,
    # so it needs several conditions to have been readable. A Sunday reopen scores
    # two factors and a live midday session scores five or six, and they should
    # not be able to reach the same verdict off the same number.
    #
    # THIS FLOOR CANNOT CURRENTLY BIND, and that is deliberate rather than an
    # oversight: no single factor is worth more than 2, so two factors bottom out
    # at -4, which lands in "poor" on the score alone. It is here so that adding a
    # factor worth -3 later cannot silently let a two-reading card issue the
    # strongest warning on the page. Do not delete it as dead code.
    elif score >= -4 or n_scored < 3:
        verdict, note = "poor", ("Conditions are working against you. Smaller size, or wait for "
                                 "a specific level rather than trading the middle.")
    else:
        verdict, note = "stand aside", ("Several conditions are hostile at once. The cost of "
                                        "trading this session usually exceeds the opportunity.")

    return {
        "available": bool(reasons),
        "score": score,
        "verdict": verdict,
        "note": note,
        "reasons": sorted(reasons, key=lambda r: r["effect"]),
        # How much of the gate was actually readable. A -2 from two factors and a
        # -2 from six are not the same statement, and the card should be able to
        # say which one it is showing. The zero-effect count mixes true
        # abstentions with neutral confirmations ("breadth confirms") — it counts
        # factors that did not move the score, not factors that could not be read.
        "factors_scored": n_scored,
        "factors_zero_effect": sum(1 for r in reasons if r["effect"] == 0),
        "disclaimer": ("Conditions only — this says whether the session suits intraday trading, "
                       "never which way to lean."),
    }


def _levels_independent(reason: str, now: pd.Timestamp | None,
                        with_base_rates: bool, with_breadth: bool,
                        with_candles: bool, with_overnight: bool = True,
                        with_gamma: bool = True) -> dict:
    """What the cockpit can still say when the ES feed is the thing that failed.

    Breadth reads a Polygon snapshot; the candle context and the base rates read
    cash-index history. None of them need an ES bar, a session frame or a level,
    so none of them should disappear because ES=F timed out. They are computed
    without a `last` price, which only costs the gap conditioning on the base
    rates and the reachability bands on levels that do not exist anyway.
    """
    from src.es_baserates import base_rates
    from src.es_breadth import market_breadth
    from src.es_overnight import overnight_base_rates
    from src.candle_context import candle_context
    from src.dealer_gamma import dealer_gamma

    def _safe(fn, label):
        try:
            return fn()
        except Exception as e:
            logger.warning(f"es-cockpit(degraded): {label} failed: {e}")
            return None

    clock_et = now if now is not None else pd.Timestamp.now(tz=_TZ)
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_br = pool.submit(_safe, lambda: base_rates(now=None), "base_rates")             if with_base_rates else None
        f_bd = pool.submit(_safe, lambda: market_breadth(now=clock_et), "breadth")             if with_breadth else None
        f_cx = pool.submit(_safe, lambda: candle_context("^GSPC"), "candles")             if with_candles else None
        # Gamma reads the SPX OPTION CHAIN and sources its own spot from ^SPX.
        # `es_last` is optional and only sharpens the ES-equivalent level, so it
        # never needed an ES bar — blanking it here repeated exactly the bug that
        # once erased four modules over a single ES=F hiccup.
        f_gx = pool.submit(_safe, dealer_gamma, "gamma") if with_gamma else None
        rates = f_br.result() if f_br else None
        breadth = f_bd.result() if f_bd else None
        candles = f_cx.result() if f_cx else None
        gamma = f_gx.result() if f_gx else None
        # The overnight STUDY is two years of history and does not depend on the
        # live bars that just failed, so it still answers. Only the live read is
        # lost, and the module already reports that as `live: None`.
        overnight_ctx = _safe(overnight_base_rates, "overnight") if with_overnight else None

    # The dispersion half of session character is cross-asset DAILY data and
    # touches no ES bar, so it survives a levels outage intact. The path half
    # cannot — it is measured from the developing range that just went missing —
    # and reports itself unavailable rather than being faked.
    from src.es_regime import session_character
    regime = _safe(lambda: session_character(range_so_far=None, normal_range=None),
                   "regime")

    return {
        "available": True,
        "levels_unavailable_reason": reason,
        "levels": {"available": False, "reason": reason},
        # These two genuinely need ES bars — intraday structure and the expected
        # move are both computed FROM them, so there is nothing to serve.
        "intraday": None,
        "expected_move": None,
        "gamma": gamma,
        "base_rates": rates,
        "breadth": breadth,
        "candles": candles,
        "overnight": overnight_ctx,
        "regime": regime,
        "gap_pct": None,
        # Only what is ACTUALLY missing. Gamma used to be listed here
        # unconditionally alongside the two that genuinely need bars, which is
        # how it stayed on the degraded list after it started working.
        "degraded": ["levels", "intraday", "expected_move"]
                    + [k for k, v in (("gamma", gamma), ("base_rates", rates),
                                      ("breadth", breadth), ("candles", candles),
                                      ("overnight", overnight_ctx),
                                      ("regime", regime)) if not v],
    }


def es_cockpit(now: pd.Timestamp | None = None,
               with_gamma: bool = True,
               with_base_rates: bool = True,
               with_breadth: bool = True,
               with_candles: bool = True,
               with_overnight: bool = True) -> dict:
    """The full intraday picture, from one bar fetch and one session model."""
    from src.es_levels import session_frames, es_levels

    frames = session_frames(now=now)
    levels = es_levels(frames=frames) if frames else {"available": False,
                                                      "reason": "no intraday ES data"}
    if not frames or not levels.get("available"):
        # LEVELS FAILING MUST NOT TAKE DOWN THE WHOLE CARD. This used to return
        # `available: False` outright, which blanked expected move, gamma,
        # structure, breadth AND the candle read together. Three of those do not
        # touch ES levels or even the ES feed: breadth is a Polygon snapshot,
        # the candle context and the base rates are cash-index history. A single
        # yfinance hiccup on ES=F was erasing four modules that had no reason to
        # care, and the card rendered "unavailable" four times over while every
        # underlying source was healthy.
        logger.warning(f"es-cockpit: levels unavailable ({levels.get('reason')}) — "
                       "serving the modules that do not depend on them")
        return _levels_independent(levels.get("reason") or "levels unavailable",
                                   now=now, with_base_rates=with_base_rates,
                                   with_breadth=with_breadth, with_candles=with_candles,
                                   with_overnight=with_overnight, with_gamma=with_gamma)

    bars = frames["bars"]
    session_day = frames["session_day"]
    anchor = frames["anchor"]
    overnight = frames["overnight"]
    last = levels["last"]

    lv = levels.get("levels", [])
    py_high = (_level(lv, "py_high") or {}).get("value")
    py_low = (_level(lv, "py_low") or {}).get("value")
    py_close = (_level(lv, "py_close") or {}).get("value")
    s_high = (_level(lv, "today_high") or {}).get("value")
    s_low = (_level(lv, "today_low") or {}).get("value")
    s_open = (_level(lv, "today_open") or {}).get("value")
    on_range = None
    on_hi, on_lo = (_level(lv, "on_high") or {}).get("value"), (_level(lv, "on_low") or {}).get("value")
    if on_hi is not None and on_lo is not None:
        on_range = on_hi - on_lo

    # The gap that matters for base rates is the CASH open against the prior
    # cash close. Before the bell there is no open yet, so the live price stands
    # in for it — that is the gap as it currently stands into the open.
    gap_ref = s_open if s_open is not None else last
    gap_pct = ((gap_ref - py_close) / py_close * 100) if py_close else None

    def _safe(fn, label):
        try:
            return fn()
        except Exception as e:
            logger.warning(f"es-cockpit: {label} failed: {e}")
            return None

    from src.es_intraday import es_intraday
    from src.es_expected_move import expected_move
    from src.dealer_gamma import dealer_gamma
    from src.es_baserates import base_rates
    from src.es_breadth import market_breadth
    from src.candle_context import candle_context, range_divergence
    from src.es_overnight import overnight_read
    from src.es_regime import path_implied_range
    from src.es_rest_of_session import rest_of_session
    from src.es_attribution import price_attribution
    from src.es_macro_setup import macro_setup
    from src.es_level_clusters import cluster_levels
    from src.es_chop import session_chop

    # The expected move is always computed for the SESSION AHEAD. So a range
    # already in the books may belong to a different session than the estimate,
    # and comparing them produces nonsense — a completed Friday range measured
    # against Monday's implied move reads as "123% of today's range spent" on a
    # day that hasn't started. Only feed the realised range when the session
    # being measured is the one actually trading.
    live = levels.get("mode") == "rth"
    developing = levels.get("mode") in ("rth", "premarket")

    # Sized to the number of submissions below, which grew from seven to
    # eleven as session character, rest-of-session, attribution and the macro
    # setup were added. Every one of them is network-bound rather than CPU-
    # bound, so leaving the cap at 4 simply queued the new work behind the old
    # and serialised what was meant to be parallel.
    with ThreadPoolExecutor(max_workers=11) as pool:
        f_intra = pool.submit(_safe, lambda: es_intraday(
            bars.drop(columns=[c for c in ("session", "rth") if c in bars.columns]),
            anchor, last, overnight=overnight,
            prior_high=py_high, prior_low=py_low), "intraday")
        f_em = pool.submit(_safe, lambda: expected_move(
            bars, session_day, last,
            session_high=s_high if live else None,
            session_low=s_low if live else None,
            overnight_range=on_range if developing else None), "expected_move")
        f_gamma = pool.submit(_safe, lambda: dealer_gamma(session_day, es_last=last),
                              "gamma") if with_gamma else None
        # The path statistics need the wall clock, not the session date, to say
        # how much of the range is typically still ahead. Only hand it over when
        # the cash session is actually trading: `mode` is the authoritative
        # answer (it knows holidays and half-days, which a clock does not), and
        # a "70% of the range is spent" pointer on a closed day is a lie about
        # the frame in exactly the way the levels bug was.
        clock_et = now if now is not None else pd.Timestamp.now(tz=_TZ)
        clock = clock_et if live else None
        # `session_high`/`session_low` are handed over only when the session is
        # actually trading — they answer "has today's gap already filled", and a
        # completed Friday range would answer it about the wrong day.
        f_br = pool.submit(_safe, lambda: base_rates(
            last=last, gap_pct=gap_pct, now=clock, prev_close=py_close,
            session_high=s_high if live else None,
            session_low=s_low if live else None), "base_rates") if with_base_rates else None
        # Breadth derives its own index comparison so it always describes the
        # same window as its counts — see the note in `market_breadth`. It is
        # handed an EXCHANGE-LOCAL clock, never `now` as passed: Cloud Run is
        # UTC, so after 20:00 ET a bare `date.today()` is already tomorrow and
        # the walk back to the last traded session starts a day too far out.
        f_bd = pool.submit(_safe, lambda: market_breadth(now=clock_et), "breadth") \
            if with_breadth else None
        # Cash index, NEVER ES=F. An ES daily bar opens at the 18:00 Globex open,
        # so its body and shadows measure a different session than the one the
        # study was built on — the same trap that makes ES gap statistics
        # meaningless.
        f_cx = pool.submit(_safe, lambda: candle_context("^GSPC"), "candles") \
            if with_candles else None
        # Handed the SAME frames the levels came from, so the overnight high it
        # reasons about is the one on the ladder beside it — and so it costs no
        # extra bar fetch against the futures tier's 5 calls a minute.
        f_on = pool.submit(_safe, lambda: overnight_read(frames=frames), "overnight") \
            if with_overnight else None
        # SESSION CHARACTER. Every other range estimator here is fixed at the
        # open — this one is measured from the range the session has actually
        # delivered, which is the only way an UNSCHEDULED event shows up while
        # it is still running. Handed ES's own trailing median so the multiplier
        # is unit-free; see the module docstring for the measured error.
        from src.es_regime import session_character
        dev_range = (s_high - s_low) if (live and s_high is not None
                                         and s_low is not None) else None
        _normal = _normal_range(frames)
        f_rg = pool.submit(_safe, lambda: session_character(
            range_so_far=dev_range, normal_range=_normal, now=now), "regime")
        # WHAT THE REST OF THE SESSION LOOKED LIKE from a state like this one.
        # Every other module answers "should I engage"; once a position is on,
        # the card had nothing. Conditioned on the half-hour mark, where price
        # sits in the range built so far, and the character read.
        _pos = ((last - s_low) / dev_range
                if (dev_range and dev_range > 0 and s_low is not None) else None)
        f_ros = pool.submit(_safe, lambda: rest_of_session(
            _pos, (path_implied_range(dev_range, _normal, now) or {}).get("multiplier"),
            _normal, now), "rest_of_session")
        # WHAT MOVED THE TAPE, ranked from the tape rather than from the feed.
        # Session character says the day is unusual; this says where the
        # unusualness actually happened and what was on the clock at the time.
        f_at = pool.submit(_safe, lambda: price_attribution(frames=frames, now=now),
                           "attribution") if live else None
        # THE SETUP. Named drivers with their MEASURED range lift, the direction
        # null stated with its p-value, and the transmission chain checked
        # against the tape. Cross-asset daily data only, so it survives a levels
        # outage and it is the one block that speaks before the open.
        f_ms = pool.submit(_safe, lambda: macro_setup(now=now), "macro_setup")

        intraday = f_intra.result()
        em = f_em.result()
        gamma = f_gamma.result() if f_gamma else None
        rates = f_br.result() if f_br else None
        breadth = f_bd.result() if f_bd else None
        candles = f_cx.result() if f_cx else None
        overnight_ctx = f_on.result() if f_on else None
        regime = f_rg.result()
        ros = f_ros.result()
        attribution = f_at.result() if f_at else None
        setup = f_ms.result()

    # Levels that price cannot tell apart are ONE reference. Computed after the
    # pool because it needs the gamma walls, and it is pure arithmetic over
    # numbers already in hand — no fetch, so no reason to occupy a worker.
    #
    # `cur_rth` lives on `frames`, not in this scope. Reading it as a bare name
    # raised NameError, a blanket `except Exception` swallowed it, and the
    # clustering quietly fell back to 4% of the normal range — the tolerance
    # still worked, so nothing looked broken, and the median-bar basis this was
    # argued on was simply never in effect. Caught only because the live payload
    # reported `tolerance_basis` and it read the wrong one. A blanket catch
    # around a name lookup hides typos rather than data problems, so the lookup
    # is now explicit and only the numeric work is guarded.
    # HOW STRAIGHT, as against how big. Every other estimator on this card sizes
    # the session; none of them could tell a wide rotation from a narrow drift.
    # Computed after the pool on purpose: it reads the same 5-minute frame the
    # base-rate study has just cached, so running it here costs no fetch, while
    # submitting it alongside would race that fetch and double it on a cold
    # instance. Live sessions only — "this session has been choppy" is a claim
    # about a tape that is trading, the same reason the path read stays silent
    # outside the cash hours.
    chop = _safe(lambda: session_chop(now=clock), "chop_trend") if live else None

    _rth = (frames or {}).get("cur_rth")
    _bar = None
    if _rth is not None and not _rth.empty:
        try:
            _b = (_rth["High"] - _rth["Low"]).median()
            _bar = float(_b) if _b and _b > 0 else None
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"es-cockpit: median bar failed: {e}")
    clusters = _safe(lambda: cluster_levels(lv, gamma, median_bar=_bar,
                                            normal_range=_normal), "level_clusters")

    # REACHABILITY. The ladder quotes every level as a distance in handles, which
    # answers "how far" but not the question actually being asked at the open:
    # can price even GET there in a session? Thirty handles is a routine walk on
    # a day priced for ninety and most of a trend day on one priced for forty.
    # Expressing each distance against the expected range turns a raw number into
    # a decision, and it is the arithmetic a trader does in their head anyway.
    #
    # Denominated in the expected RANGE (high-low), not sigma, because this is
    # about whether price TOUCHES the level intraday rather than where it closes.
    # Kept qualitative on purpose: a real touch probability needs its own study,
    # and inventing one here would be the fabricated precision this cockpit
    # exists to keep out.
    exp_range = (em or {}).get("expected_range")
    if exp_range and levels.get("levels"):
        for lv_row in levels["levels"]:
            dist = lv_row.get("distance")
            if dist is None:
                continue
            share = abs(dist) / exp_range * 100
            lv_row["pct_of_expected_range"] = round(share)
            if share <= 25:
                lv_row["reach"] = "routine"
            elif share <= 60:
                lv_row["reach"] = "reachable"
            elif share <= 100:
                lv_row["reach"] = "a stretch"
            else:
                lv_row["reach"] = "beyond a typical session"

    # The study measures the CASH INDEX and reports a bare point figure. Attach
    # what that means for ES — index points are ES points, and the forecast is
    # only interpretable against ES's own measured session range.
    if candles:
        from src.candle_es_read import candle_es_read
        candles["es_read"] = _safe(lambda: candle_es_read(candles), "candle_es_read")

    # Two independent estimates of tomorrow's high-low: what options are paying
    # for, and what bars conditioned like today's have actually delivered.
    if candles and (candles.get("tomorrow_range") or {}).get("p50") and (em or {}).get("expected_range"):
        candles["vs_implied"] = range_divergence(
            candles["tomorrow_range"]["p50"], em.get("expected_range"),
            atr=(candles.get("tomorrow_range") or {}).get("atr"))

    return {
        "available": True,
        "levels": levels,
        "intraday": intraday,
        "expected_move": em,
        "gamma": gamma,
        "base_rates": rates,
        "breadth": breadth,
        "candles": candles,
        "overnight": overnight_ctx,
        "regime": regime,
        # The orthogonal axis to `regime`: that one says how big, this says how
        # straight. Measured at corr +0.37, so they are close to independent.
        "chop_trend": chop,
        "rest_of_session": ros,
        "attribution": attribution,
        "macro_setup": setup,
        "level_clusters": clusters,
        "gap_pct": round(gap_pct, 3) if gap_pct is not None else None,
        "degraded": [k for k, v in (("intraday", intraday), ("expected_move", em),
                                    ("gamma", gamma), ("base_rates", rates),
                                    ("breadth", breadth), ("candles", candles),
                                    ("overnight", overnight_ctx),
                                    ("regime", regime),
                                    ("rest_of_session", ros)) if not v],
    }
