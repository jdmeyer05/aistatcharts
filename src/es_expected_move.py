"""How far ES can reasonably travel today — the sizing layer.

Levels say where price is. This says how much room there is, which is what
actually governs size, target selection and whether a level is even reachable
before the bell.

THREE ESTIMATES, DELIBERATELY NOT BLENDED. They disagree, and the disagreement
is information:

  straddle    The market's own price for today's move — the 0DTE ATM straddle
              on SPX. The only forward-looking number here, and the one desks
              quote. It embeds today's known catalysts.
  VIX1D       Cboe's 1-day implied volatility, converted to a one-session move.
              A cleaner index-level read, but it is a model output rather than
              a tradeable price.
  ATR         What ES has actually been doing, over 14 sessions. Backward
              looking, and it ignores today's calendar entirely.

Implied sitting well above realised means the market is paying up for a
catalyst; well below means today is priced as a nothing day, and breakouts
have less fuel than usual.

THE MOST USEFUL NUMBER HERE IS `consumed_pct` — how much of the expected move
the session has already spent. Late in the day with most of it used, extensions
have historically been the wrong thing to chase; early with little used, the
range is still ahead. It is a range budget, not a prediction.

ES vs SPX: expected moves are computed on SPX (that is where the options are)
and converted to ES handles one-for-one. The two track within a basis that
moves slowly, so a same-day PERCENTAGE move is interchangeable; the absolute
levels are not, which is why nothing here quotes an SPX price as an ES level.
"""

from __future__ import annotations

import logging
from datetime import date as _date, time as dtime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_TZ = "America/New_York"
_TRADING_DAYS = 252

# A one-sigma move and a day's high-low range are NOT the same number, and
# mixing them is the easy way to make this whole module lie. For driftless
# Brownian motion over one session, E[range] = sigma * sqrt(8/pi) ~= 1.596.
#
# It matters concretely: a 58-handle one-sigma move against a 113-handle
# session range reads as "196% of the expected move consumed", which sounds
# like a once-a-quarter extension. Against the expected RANGE of ~93 handles
# it is 122% — a wide day, not an aberration. Every estimate below therefore
# carries both figures, and each consumer uses the one it actually means.
_RANGE_OVER_SIGMA = 1.5958


# ── Inputs ────────────────────────────────────────────────────────

def _spx_spot() -> float | None:
    try:
        import yfinance as yf
        h = yf.Ticker("^SPX").history(period="5d", interval="1d", auto_adjust=False)
        return float(h["Close"].iloc[-1]) if len(h) else None
    except Exception as e:
        logger.warning(f"SPX spot failed: {e}")
        return None


def _vix1d() -> tuple[float, str] | None:
    """Cboe 1-day implied vol, with the ticker it came from.

    Falls back to VIX9D then VIX — each a worse proxy for a single session, so
    which one was used is returned and shown rather than silently substituted.
    """
    import yfinance as yf
    for sym in ("^VIX1D", "^VIX9D", "^VIX"):
        try:
            h = yf.Ticker(sym).history(period="5d", interval="1d", auto_adjust=False)
            if len(h):
                return float(h["Close"].iloc[-1]), sym
        except Exception:
            continue
    return None


def _atr_handles(periods: int = 14) -> dict | None:
    """True-range ATR on daily ES bars, in handles.

    True range rather than high-low, because ES gaps between sessions and a
    plain range understates a day that opened away from the prior close.
    """
    try:
        import yfinance as yf
        h = yf.Ticker("ES=F").history(period="3mo", interval="1d", auto_adjust=False)
        if len(h) < periods + 1:
            return None
        prev_close = h["Close"].shift(1)
        tr = pd.concat([
            h["High"] - h["Low"],
            (h["High"] - prev_close).abs(),
            (h["Low"] - prev_close).abs(),
        ], axis=1).max(axis=1).dropna()
        if len(tr) < periods:
            return None
        atr = float(tr.tail(periods).mean())
        return {
            "atr": round(atr, 2),
            "periods": periods,
            "median_range": round(float((h["High"] - h["Low"]).tail(periods).median()), 2),
        }
    except Exception as e:
        logger.warning(f"ATR failed: {e}")
        return None


def _atm_straddle(spot: float, expiry: str) -> dict | None:
    """Price of the at-the-money straddle for an expiry, from the SPX chain.

    The straddle price IS the market's expected move to that expiry — a
    breakeven, not a forecast. Mid of bid/ask where quotes exist, last trade
    otherwise, because a stale last on a fast-moving 0DTE strike can be far off.
    """
    try:
        import requests
        from src.api_keys import get_secret
        key = get_secret("POLYGON_API_KEY") or get_secret("MASSIVE_API_KEY")
        if not key:
            return None

        r = requests.get(
            "https://api.polygon.io/v3/snapshot/options/I:SPX",
            params={
                "expiration_date": expiry,
                "strike_price.gte": spot * 0.97,
                "strike_price.lte": spot * 1.03,
                "limit": 250, "apiKey": key,
            }, timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            return None

        sources: set[str] = set()

        def px(c: dict) -> float | None:
            """Mid, then last trade, then the session's settled close.

            Outside market hours Polygon returns no quote and no trade at all,
            so without the third fallback this whole estimate silently vanishes
            exactly when someone is planning the next session.
            """
            q = c.get("last_quote") or {}
            bid, ask = q.get("bid"), q.get("ask")
            if bid and ask and ask >= bid > 0:
                sources.add("quote")
                return (bid + ask) / 2
            trade = (c.get("last_trade") or {}).get("price")
            if trade:
                sources.add("trade")
                return float(trade)
            close = (c.get("day") or {}).get("close")
            if close:
                sources.add("settled")
                return float(close)
            return None

        calls: dict[float, float] = {}
        puts: dict[float, float] = {}
        for c in results:
            d = c.get("details") or {}
            k, typ = d.get("strike_price"), d.get("contract_type")
            p = px(c)
            if k is None or p is None:
                continue
            (calls if typ == "call" else puts)[float(k)] = p

        both = sorted(set(calls) & set(puts), key=lambda k: abs(k - spot))
        if not both:
            return None
        strike = both[0]
        straddle = calls[strike] + puts[strike]
        if straddle <= 0:
            return None

        return {
            "expiry": expiry,
            "strike": strike,
            "straddle": round(straddle, 2),
            "call": round(calls[strike], 2),
            "put": round(puts[strike], 2),
            "strike_offset": round(strike - spot, 2),
            # "settled" means the market was shut and this is the last session's
            # settlement, not a live price for the session ahead.
            "quote_source": ("quote" if "quote" in sources
                             else "trade" if "trade" in sources else "settled"),
        }
    except Exception as e:
        logger.warning(f"ATM straddle {expiry} failed: {e}")
        return None


def _next_expiry(session_day: pd.Timestamp) -> str:
    """SPX has daily expirations on weekdays, so the session day itself is the
    0DTE expiry."""
    return str(pd.Timestamp(session_day).date())


def _realized_vol(bars: pd.DataFrame, sessions: int = 10) -> float | None:
    """Annualised realised vol from 5-minute RTH returns.

    Compared against a 1-day implied, this is what tells you whether options
    are pricing more movement than ES has actually been delivering.
    """
    try:
        rth = bars[[dtime(9, 30) <= t.time() < dtime(16, 0) for t in bars.index]]
        if rth.empty:
            return None
        days = sorted({d for d in rth.index.normalize().unique()})[-sessions:]
        sel = rth[rth.index.normalize().isin(days)]
        r = np.log(sel["Close"] / sel["Close"].shift(1)).dropna()
        # Drop the overnight jump between sessions — it is not a 5-minute return.
        r = r[np.abs(r) < 0.02]
        if len(r) < 30:
            return None
        bars_per_day = 78                      # 6.5h of 5-minute bars
        return float(r.std() * np.sqrt(bars_per_day * _TRADING_DAYS) * 100)
    except Exception as e:
        logger.warning(f"realized vol failed: {e}")
        return None


# ── Assembly ──────────────────────────────────────────────────────

def expected_move(bars: pd.DataFrame | None, session_day: pd.Timestamp,
                  es_last: float, session_high: float | None = None,
                  session_low: float | None = None,
                  overnight_range: float | None = None) -> dict:
    """The day's range budget, from three independent estimates."""
    spot = _spx_spot()
    v = _vix1d()
    vix_val, vix_sym = (v if v else (None, None))
    atr = _atr_handles()

    estimates: list[dict] = []

    def est(source: str, sigma: float, detail: str, forward: bool, **extra) -> dict:
        return {
            "source": source,
            "sigma_handles": round(sigma, 2),
            "range_handles": round(sigma * _RANGE_OVER_SIGMA, 2),
            "pct": round(sigma / (spot or es_last) * 100, 3) if (spot or es_last) else None,
            "detail": detail,
            "forward_looking": forward,
            **extra,
        }

    # 1. The market's own price for today's move.
    straddle = _atm_straddle(spot, _next_expiry(session_day)) if spot else None
    if straddle and spot:
        # The straddle is a breakeven at expiry; the conventional approximation
        # takes ~0.85 of it as the one-sigma equivalent.
        settled = straddle.get("quote_source") == "settled"
        estimates.append(est(
            "0DTE straddle", straddle["straddle"] * 0.85,
            f"SPX {straddle['expiry']} {straddle['strike']:.0f} straddle at "
            f"{straddle['straddle']:.2f}"
            + (" (last settlement — market was closed)" if settled else ""),
            True, quote_source=straddle.get("quote_source"), strike=straddle["strike"],
        ))

    # 2. VIX1D converted to one session.
    if vix_val and spot:
        daily_sigma_pct = vix_val / np.sqrt(_TRADING_DAYS)
        estimates.append(est(
            f"{vix_sym.lstrip('^')} implied", spot * daily_sigma_pct / 100,
            f"{vix_sym.lstrip('^')} at {vix_val:.2f}, annualised to one session", True,
        ))

    # 3. What ES has actually done. ATR is already a RANGE measure, so it is
    #    divided back to a sigma rather than multiplied up.
    if atr:
        estimates.append(est(
            "ATR(14)", atr["atr"] / _RANGE_OVER_SIGMA,
            f"14-session true range {atr['atr']:.2f}; median high-low {atr['median_range']:.2f}",
            False,
        ))

    if not estimates:
        return {"available": False, "reason": "no expected-move inputs available"}

    # Headline is the forward-looking estimate when there is one — the market's
    # price for today beats an average of the last fortnight.
    # Headline preference: a LIVE straddle is the best number here — it is the
    # market's own price for today, with today's catalysts in it. A SETTLED one
    # is not: with the market shut, each leg's "close" is a last trade from a
    # different moment, and the two legs can be minutes apart. Observed giving
    # a 36-handle sigma against 58 from VIX1D and 62 from ATR — the two that
    # agree. So settled straddles are still shown, never used as the headline.
    live = [e for e in estimates
            if e["forward_looking"] and e.get("quote_source") not in ("settled",)]
    fwd = [e for e in estimates if e["forward_looking"]]
    headline = (live[0] if live
                else next((e for e in fwd if "VIX" in e["source"]), None)
                or (fwd[0] if fwd else estimates[0]))
    em_handles = headline["sigma_handles"]
    em_range = headline["range_handles"]

    # How much of the budget the session has already spent. Compared against the
    # expected RANGE, not the one-sigma move — a high-low range measured against
    # a close-to-close sigma overstates by ~60% and would flag an ordinary wide
    # day as a historic extension.
    consumed = None
    if session_high is not None and session_low is not None and em_range:
        realized_range = session_high - session_low
        consumed = {
            "range": round(float(realized_range), 2),
            "expected_range": em_range,
            "pct": round(float(realized_range / em_range * 100), 1),
        }
        pct = consumed["pct"]
        if pct >= 100:
            consumed["note"] = ("The session has already covered its whole expected move. "
                                "Continuation from here is the tail, not the base case.")
        elif pct >= 75:
            consumed["note"] = ("Most of the expected range is spent — late entries are paying "
                                "up for what is left.")
        elif pct >= 40:
            consumed["note"] = "Roughly half the expected range used; a normal day so far."
        else:
            consumed["note"] = ("Little of the expected range used — the day is coiled and the "
                                "move is still ahead.")

    rv = _realized_vol(bars) if bars is not None else None
    vol_regime = None
    if rv and vix_val:
        ratio = vix_val / rv if rv > 0 else None
        if ratio:
            if ratio >= 1.25:
                lbl, note = "implied rich", ("Options are pricing more movement than ES has been "
                                             "delivering — the market expects a catalyst.")
            elif ratio <= 0.8:
                lbl, note = "implied cheap", ("ES has been moving more than options are pricing. "
                                              "Breakouts have more fuel than the premium suggests.")
            else:
                lbl, note = "in line", "Implied and realised are broadly agreeing."
            vol_regime = {
                "implied": round(vix_val, 2), "realized": round(rv, 2),
                "ratio": round(ratio, 2), "label": lbl, "note": note,
            }

    on_ctx = None
    if overnight_range is not None and em_range:
        on_ctx = {
            "range": round(float(overnight_range), 2),
            "pct_of_expected": round(float(overnight_range / em_range * 100), 1),
        }

    return {
        "available": True,
        "session_date": str(pd.Timestamp(session_day).date()),
        "spx_spot": round(spot, 2) if spot else None,
        "es_last": round(es_last, 2) if es_last else None,
        "headline": headline,
        # One-sigma close-to-close move; the bands below are built from it.
        "expected_handles": em_handles,
        # Expected high-low for the session; what `consumed` measures against.
        "expected_range": em_range,
        "upper": round(es_last + em_handles, 2) if es_last and em_handles else None,
        "lower": round(es_last - em_handles, 2) if es_last and em_handles else None,
        "estimates": estimates,
        "consumed": consumed,
        "vol_regime": vol_regime,
        "overnight": on_ctx,
    }


# ── Event premium ─────────────────────────────────────────────────

def _next_session_expiry(session_day: pd.Timestamp) -> str:
    """The SPX expiry one trading day past the session day."""
    d = pd.Timestamp(session_day).normalize() + pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d += pd.Timedelta(days=1)
    return str(d.date())


def event_premium(session_day: pd.Timestamp | str, now: pd.Timestamp | None = None) -> dict:
    """What SPX options charge for spanning tonight's close-to-close segment.

    THE PROBLEM THIS SOLVES. An after-the-close event — a megacap earnings
    report, most often — cannot be sized from the card's usual numbers. The
    session's own expected move deliberately stops at this bell, and no index
    weight is available on this stack to convert a single name's expected gap
    into index handles. So the question "how much does this actually matter to
    ES" had no measured answer, only an adjective.

    It does have one, and the options market quotes it. Two SPX straddles:
    one expiring at this session's close, one expiring at the next. Variance is
    additive over non-overlapping periods, so the segment BETWEEN them — the
    overnight that contains the event, plus the session after it — prices at

        sigma_segment = sqrt(sigma_next**2 - sigma_today**2)

    Dividing that by the plain session ahead of it gives the number the card
    wants: how many ordinary sessions of movement the market is paying for in
    one overnight. It needs no constituent feed, no weight and no assumption —
    it is a price, read off two quotes.

    THE RATIO IS ONLY PUBLISHED BEFORE THE OPEN, and this is the subtle part.
    The denominator is the near straddle, which is not a fixed quantity — it is
    what is left of the current period, and it shrinks all day. Before the open
    it spans a whole session and the comparison is like-for-like. At 10:00 it
    spans six hours while the numerator still spans a full close-to-close
    segment, so the ratio silently becomes "24 hours over 6 hours" and reports
    ~3.7x on a night that is priced at 1.7x. That is not a caveat, it is a wrong
    number, so in-session the segment is still returned and the multiple is
    withheld.

    Two caveats that are stated rather than hidden:

    - Even pre-open the match is not exact. Read at 21:00 the near straddle has
      already lost the first hours of its overnight while the segment has all of
      its own, and overnight carries roughly 40% of a session's variance here —
      so the denominator is slightly short and the ratio slightly generous.
      Small at that hour, and in the direction of caution, but real.
    - With the market shut both straddles are settlement-based, and this module
      does not trust a settled straddle as a LEVEL (one was observed at 36
      handles against 58 and 62 from the two estimates that agreed). A RATIO of
      two of them is sounder, because both legs are mispriced in the same
      direction by the same closed book and much of the error divides out — but
      it is not immune, so `quote_source` rides along and the caller should
      hedge the wording when it says "settled".
    """
    spot = _spx_spot()
    if not spot:
        return {"available": False, "reason": "no SPX spot"}

    today_exp = _next_expiry(session_day)
    next_exp = _next_session_expiry(session_day)
    s_today = _atm_straddle(spot, today_exp)
    s_next = _atm_straddle(spot, next_exp)
    if not s_today or not s_next:
        missing = next_exp if s_today else today_exp
        return {"available": False, "reason": f"no SPX straddle for {missing}"}

    a, b = s_today["straddle"], s_next["straddle"]
    if b <= a:
        return {"available": False,
                "reason": "next expiry prices no more than this one — no measurable event premium"}

    segment = float(np.sqrt(b * b - a * a))
    settled = "settled" in (s_today["quote_source"], s_next["quote_source"])

    # Is the denominator still a whole session? See the docstring — after the
    # bell rings the near straddle is a stub and the ratio measures elapsed time
    # rather than event risk.
    now = now if now is not None else pd.Timestamp.now(tz=_TZ)
    if now.tzinfo is None:
        now = now.tz_localize(_TZ)
    session_open = pd.Timestamp.combine(
        pd.Timestamp(session_day).normalize(), dtime(9, 30)).tz_localize(_TZ)
    baseline_is_a_full_session = now < session_open

    out = {
        "available": True,
        "session_expiry": today_exp,
        "next_expiry": next_exp,
        "this_session_straddle": a,
        "next_session_straddle": b,
        # Straddle points for the close-to-close segment that contains the event.
        # Same units as the card's other handle numbers; SPX and ES move together
        # in percentage terms, which is what makes the one-for-one carry legal.
        # Valid at any hour — only the RATIO below depends on the clock.
        "segment_handles": round(segment, 2),
        "segment_pct": round(segment / spot * 100, 2),
        "quote_source": "settled" if settled else "live",
        "baseline_is_full_session": baseline_is_a_full_session,
    }

    if not baseline_is_a_full_session:
        out["vs_session"] = None
        out["vs_session_withheld"] = (
            "the session is already under way, so the near straddle covers only "
            "the hours that are left — a multiple against it would measure the "
            "clock, not the event. The priced segment above still stands."
        )
        out["note"] = (
            f"SPX prices {segment:.0f} handles for the segment from the {today_exp} "
            f"close to the {next_exp} close. No multiple while the session is "
            f"running — the baseline it would divide by has already decayed."
        )
        return out

    out["vs_session"] = round(segment / a, 2)
    out["note"] = (
        f"SPX prices {segment:.0f} handles for the segment from the {today_exp} "
        f"close to the {next_exp} close — {segment / a:.2f}x the {a:.0f} handles "
        f"it prices for the session itself."
        + (" Both straddles are settlement-based with the market shut; the ratio "
           "survives that better than either level does, but treat it as indicative."
           if settled else "")
    )
    return out
