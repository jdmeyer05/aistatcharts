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


def conditions_gate(levels: dict, intraday: dict, em: dict, gamma: dict,
                    session: dict, schedule: list[dict]) -> dict:
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

    # Expected move. A day priced for nothing rarely delivers a trend.
    headline = (em or {}).get("headline") or {}
    em_pct = headline.get("pct")
    if em_pct is not None:
        if em_pct >= 1.0:
            score += 2
            reasons.append({"factor": "Wide expected move", "effect": +2,
                            "why": f"Priced for {em_pct:.2f}% — enough room for a trade to work."})
        elif em_pct <= 0.5:
            score -= 2
            reasons.append({"factor": "Narrow expected move", "effect": -2,
                            "why": f"Priced for only {em_pct:.2f}% — little room before the day is done."})

    # How much of it is already spent.
    consumed = (em or {}).get("consumed") or {}
    cpct = consumed.get("pct")
    if cpct is not None and phase.startswith("rth"):
        if cpct >= 110:
            score -= 2
            reasons.append({"factor": "Range spent", "effect": -2,
                            "why": f"{cpct:.0f}% of the expected range already covered — chasing here pays up."})
        elif cpct <= 40:
            score += 1
            reasons.append({"factor": "Range still available", "effect": +1,
                            "why": f"Only {cpct:.0f}% of the expected range used."})

    # Dealer gamma regime.
    if (gamma or {}).get("available"):
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
    if score >= 4:
        verdict, note = "favourable", ("Conditions line up for intraday work. This is when to take "
                                       "the setups you actually wait for.")
    elif score >= -1:
        verdict, note = "workable", "Nothing exceptional either way. Be selective."
    elif score >= -4:
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
        "disclaimer": ("Conditions only — this says whether the session suits intraday trading, "
                       "never which way to lean."),
    }


def es_cockpit(now: pd.Timestamp | None = None,
               with_gamma: bool = True,
               with_base_rates: bool = True,
               with_breadth: bool = True,
               with_candles: bool = True) -> dict:
    """The full intraday picture, from one bar fetch and one session model."""
    from src.es_levels import session_frames, es_levels

    frames = session_frames(now=now)
    if not frames:
        return {"available": False, "reason": "no intraday ES data"}

    levels = es_levels(frames=frames)
    if not levels.get("available"):
        return {"available": False, "reason": levels.get("reason", "levels unavailable")}

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

    # The expected move is always computed for the SESSION AHEAD. So a range
    # already in the books may belong to a different session than the estimate,
    # and comparing them produces nonsense — a completed Friday range measured
    # against Monday's implied move reads as "123% of today's range spent" on a
    # day that hasn't started. Only feed the realised range when the session
    # being measured is the one actually trading.
    live = levels.get("mode") == "rth"
    developing = levels.get("mode") in ("rth", "premarket")

    with ThreadPoolExecutor(max_workers=4) as pool:
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
        f_br = pool.submit(_safe, lambda: base_rates(last=last, gap_pct=gap_pct, now=clock),
                           "base_rates") if with_base_rates else None
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

        intraday = f_intra.result()
        em = f_em.result()
        gamma = f_gamma.result() if f_gamma else None
        rates = f_br.result() if f_br else None
        breadth = f_bd.result() if f_bd else None
        candles = f_cx.result() if f_cx else None

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
        "gap_pct": round(gap_pct, 3) if gap_pct is not None else None,
        "degraded": [k for k, v in (("intraday", intraday), ("expected_move", em),
                                    ("gamma", gamma), ("base_rates", rates),
                                    ("breadth", breadth), ("candles", candles)) if not v],
    }
