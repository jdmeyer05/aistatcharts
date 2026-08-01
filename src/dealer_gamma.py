"""SPX dealer gamma — which hedging regime the index is in.

The single most useful intraday input for the S&P, and the one nothing else on
this platform provides. Options dealers hedge their books continuously, and the
sign of their aggregate gamma decides whether that hedging DAMPENS moves or
AMPLIFIES them:

  long gamma   Dealers sell rallies and buy dips to stay delta-neutral. Realised
               vol is suppressed, price rotates around the big strikes, and
               breakouts tend to fail. Fading extremes works here.
  short gamma  The hedge flips: they sell into weakness and buy into strength.
               Moves accelerate, ranges expand, stops cascade. Fading is what
               gets people hurt, and the same "overextended" reading that was a
               fade signal in long gamma is a continuation signal here.

The level where aggregate gamma crosses zero — the GAMMA FLIP — is therefore
the most important single number on this page. Which side of it price sits on
changes which playbook is correct.

────────────────────────────────────────────────────────────────────
THE ASSUMPTION THAT MATTERS, STATED PLAINLY

Nobody outside the dealers can see dealer inventory. Every public gamma model,
this one included, INFERS it from open interest with a sign convention:

    dealers are long calls and short puts

which follows from the usual customer behaviour — buying puts for protection
and selling calls for yield. It is a convention, not a measurement. It is
roughly right for index options most of the time and it is wrong at times,
particularly around large put-spread and collar structures.

So: treat the FLIP LEVEL and the SHAPE of the profile as the signal, and treat
the absolute GEX number as an index, not a quantity of anything. Nothing here
is calibrated to dollars-per-point of real dealer hedging, and any figure that
claims to be is making the same assumption with more decimal places.

0DTE gets its own treatment because its gamma is enormous and evaporates at the
close — including it in a multi-week profile drowns everything else, so the
expiries are reported separately as well as together.
"""

from __future__ import annotations

import logging
from datetime import date as _date, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CONTRACT_MULTIPLIER = 100
_SPX = "I:SPX"
# Strikes far from spot carry negligible gamma but a lot of OI noise, and each
# one costs payload. +/-6% covers every strike that can matter to a session.
_STRIKE_BAND = 0.06


def _fetch_chain(expiries: list[str], spot: float) -> list[dict]:
    """Snapshot the SPX chain for the given expiries, near the money."""
    import requests
    from concurrent.futures import ThreadPoolExecutor
    from src.api_keys import get_secret

    key = get_secret("POLYGON_API_KEY") or get_secret("MASSIVE_API_KEY")
    if not key:
        return []

    def one(expiry: str) -> list[dict]:
        try:
            r = requests.get(
                f"https://api.polygon.io/v3/snapshot/options/{_SPX}",
                params={
                    "expiration_date": expiry,
                    "strike_price.gte": spot * (1 - _STRIKE_BAND),
                    "strike_price.lte": spot * (1 + _STRIKE_BAND),
                    "limit": 250, "apiKey": key,
                }, timeout=25,
            )
            r.raise_for_status()
            return r.json().get("results") or []
        except Exception as e:
            logger.warning(f"gamma chain {expiry} failed: {e}")
            return []

    with ThreadPoolExecutor(max_workers=min(4, len(expiries) or 1)) as pool:
        return [c for batch in pool.map(one, expiries) for c in batch]


def _upcoming_expiries(session_day: pd.Timestamp, count: int = 3) -> list[str]:
    """The session's own expiry plus the next couple of weekday expiries.

    SPX lists dailies Monday to Friday, so the session day itself is 0DTE.
    """
    out: list[str] = []
    d = pd.Timestamp(session_day).normalize()
    while len(out) < count:
        if d.weekday() < 5:
            out.append(str(d.date()))
        d += pd.Timedelta(days=1)
    return out


def _gex_by_strike(contracts: list[dict], spot: float) -> dict[str, dict[float, float]]:
    """Gamma exposure per strike, split by expiry.

    GEX = gamma * OI * multiplier * spot^2 * 0.01 — the conventional
    formulation, giving the change in dealer delta (in notional) per 1% move.
    Calls positive, puts negative, per the sign convention documented above.
    """
    per_expiry: dict[str, dict[float, float]] = {}
    for c in contracts:
        d = c.get("details") or {}
        g = (c.get("greeks") or {}).get("gamma")
        oi = c.get("open_interest")
        strike, typ, exp = d.get("strike_price"), d.get("contract_type"), d.get("expiration_date")
        if not g or not oi or strike is None or exp is None:
            continue
        gex = float(g) * float(oi) * _CONTRACT_MULTIPLIER * (spot ** 2) * 0.01
        if typ == "put":
            gex = -gex
        per_expiry.setdefault(exp, {})
        per_expiry[exp][float(strike)] = per_expiry[exp].get(float(strike), 0.0) + gex
    return per_expiry


def _gamma_flip(contracts: list[dict], spot: float, ref_day: pd.Timestamp | None = None,
                lo_pct: float = 0.05, hi_pct: float = 0.05, steps: int = 81) -> dict | None:
    """The spot level at which aggregate dealer gamma crosses zero.

    Re-prices the whole book's gamma at each candidate spot, rather than just
    reading where the per-strike profile changes sign. Gamma is a function of
    the distance from spot to strike, so a strike's contribution changes as
    spot moves — the cheap version answers a different question and puts the
    flip in the wrong place.

    Gamma is re-estimated with a Black-Scholes-style bell around each strike,
    using each contract's own implied vol and time to expiry. Absolute values
    are approximate; the ZERO CROSSING is what this is for.
    """
    rows = []
    # Exchange-local date, not the server's. Cloud Run runs UTC, so after
    # 20:00 ET `_date.today()` has already rolled over and every contract would
    # be scored a day closer to expiry than it is.
    today = (pd.Timestamp(ref_day).date() if ref_day is not None
             else pd.Timestamp.now(tz="America/New_York").date())
    for c in contracts:
        d = c.get("details") or {}
        oi, strike = c.get("open_interest"), d.get("strike_price")
        exp, typ = d.get("expiration_date"), d.get("contract_type")
        iv = c.get("implied_volatility")
        if not oi or strike is None or not exp:
            continue
        try:
            dte = max((pd.Timestamp(exp).date() - today).days, 0) + 0.5
        except Exception:
            continue
        if not iv or iv <= 0:
            continue
        rows.append((float(strike), float(oi), float(iv), dte / 365.0,
                     -1.0 if typ == "put" else 1.0))

    if not rows:
        return None

    K = np.array([r[0] for r in rows])
    OI = np.array([r[1] for r in rows])
    IV = np.array([r[2] for r in rows])
    T = np.array([r[3] for r in rows])
    SGN = np.array([r[4] for r in rows])

    candidates = np.linspace(spot * (1 - lo_pct), spot * (1 + hi_pct), steps)
    totals = []
    for s in candidates:
        vt = IV * np.sqrt(T)
        vt = np.where(vt <= 0, 1e-6, vt)
        d1 = (np.log(s / K) + 0.5 * IV ** 2 * T) / vt
        # Gamma of a European option; the r/q terms are immaterial at these
        # horizons relative to the OI assumption already being made.
        gamma = np.exp(-0.5 * d1 ** 2) / (np.sqrt(2 * np.pi) * s * vt)
        totals.append(float(np.sum(gamma * OI * _CONTRACT_MULTIPLIER * (s ** 2) * 0.01 * SGN)))

    totals = np.array(totals)
    at_spot = float(np.interp(spot, candidates, totals))

    # Find the crossing nearest to spot.
    flip = None
    sign_changes = np.where(np.diff(np.sign(totals)) != 0)[0]
    if len(sign_changes):
        best = min(sign_changes, key=lambda i: abs((candidates[i] + candidates[i + 1]) / 2 - spot))
        x0, x1 = candidates[best], candidates[best + 1]
        y0, y1 = totals[best], totals[best + 1]
        flip = float(x0 - y0 * (x1 - x0) / (y1 - y0)) if (y1 - y0) != 0 else float(x0)

    return {
        "flip": round(flip, 2) if flip is not None else None,
        "gex_at_spot": at_spot,
        "profile": [{"spot": round(float(s), 2), "gex": float(t)}
                    for s, t in zip(candidates, totals)],
    }


def dealer_gamma(session_day: pd.Timestamp | None = None,
                 spot: float | None = None,
                 es_last: float | None = None) -> dict:
    """SPX dealer gamma: regime, flip level, and the walls that pin price."""
    if spot is None:
        try:
            import yfinance as yf
            h = yf.Ticker("^SPX").history(period="5d", interval="1d", auto_adjust=False)
            spot = float(h["Close"].iloc[-1]) if len(h) else None
        except Exception as e:
            logger.warning(f"SPX spot failed: {e}")
    if not spot:
        return {"available": False, "reason": "no SPX spot"}

    session_day = pd.Timestamp(session_day or pd.Timestamp.now(tz="America/New_York")).normalize()
    expiries = _upcoming_expiries(session_day)
    contracts = _fetch_chain(expiries, spot)
    if not contracts:
        return {"available": False, "reason": "no SPX chain data"}

    per_expiry = _gex_by_strike(contracts, spot)
    if not per_expiry:
        return {"available": False, "reason": "chain had no gamma or open interest"}

    # Aggregate across expiries.
    total_by_strike: dict[float, float] = {}
    for strikes in per_expiry.values():
        for k, v in strikes.items():
            total_by_strike[k] = total_by_strike.get(k, 0.0) + v

    total_gex = float(sum(total_by_strike.values()))
    zero_dte = str(session_day.date())
    zero_gex = float(sum(per_expiry.get(zero_dte, {}).values()))
    gross_zero = float(sum(abs(v) for v in per_expiry.get(zero_dte, {}).values()))
    gross_total = float(sum(abs(v) for strikes in per_expiry.values() for v in strikes.values()))

    flip_info = _gamma_flip(contracts, spot, ref_day=session_day) or {}
    flip = flip_info.get("flip")

    # Walls: the heaviest positive (call) gamma above spot and the heaviest
    # negative (put) gamma below. These are where hedging concentrates, so they
    # act as magnets into an expiry and as the levels price struggles through.
    above = {k: v for k, v in total_by_strike.items() if k >= spot and v > 0}
    below = {k: v for k, v in total_by_strike.items() if k <= spot and v < 0}
    call_wall = max(above, key=lambda k: above[k]) if above else None
    put_wall = min(below, key=lambda k: below[k]) if below else None

    top = sorted(total_by_strike.items(), key=lambda kv: -abs(kv[1]))[:8]

    # Derive the regime from the SAME recomputed profile that produces the flip.
    # `total_gex` uses Polygon's per-contract gammas; the flip re-prices the
    # book itself. Two different bases can disagree in sign, and a card that
    # says "long gamma" while also saying price is below the flip contradicts
    # itself on the one field that decides the playbook. Falls back to the
    # Polygon total only when the profile could not be built.
    gex_at_spot = flip_info.get("gex_at_spot")
    basis = gex_at_spot if gex_at_spot is not None else total_gex
    regime = "long" if basis > 0 else "short"
    if regime == "long":
        regime_note = ("Dealers are net long gamma, so their hedging LEANS AGAINST moves — "
                       "selling rallies, buying dips. Expect suppressed realised vol, rotation "
                       "around the big strikes, and breakouts that struggle to hold.")
    else:
        regime_note = ("Dealers are net short gamma, so their hedging AMPLIFIES moves — selling "
                       "weakness and buying strength. Expect range expansion, faster trends and "
                       "stop cascades. Fading extremes is the losing side of this regime.")

    # Convert SPX levels to ES using the observed basis, so the numbers can sit
    # on an ES ladder without pretending SPX and ES print the same price.
    basis = (es_last - spot) if (es_last and spot) else None

    def to_es(x: float | None) -> float | None:
        return round(x + basis, 2) if (x is not None and basis is not None) else None

    return {
        "available": True,
        "session_date": str(session_day.date()),
        "spx_spot": round(spot, 2),
        "es_last": round(es_last, 2) if es_last else None,
        "es_basis": round(basis, 2) if basis is not None else None,
        "expiries": sorted(per_expiry.keys()),
        "regime": regime,
        "regime_note": regime_note,
        "total_gex": total_gex,
        "gex_at_spot": gex_at_spot,
        "zero_dte_gex": zero_gex,
        # GROSS over GROSS. The question is "how much of the gamma on the board
        # evaporates at today's close", which is a magnitude, so netting calls
        # against puts — or one expiry against another at the same strike —
        # answers something else. The old net-denominator version reported 23%
        # where the gross figure is 48%, and could exceed 100% whenever expiries
        # happened to offset.
        "zero_dte_share": (round(gross_zero / gross_total * 100, 1)
                           if gross_total > 0 else None),
        "flip_spx": flip,
        "flip_es": to_es(flip),
        "distance_to_flip": round(spot - flip, 2) if flip is not None else None,
        "above_flip": bool(flip is not None and spot > flip),
        "call_wall_spx": call_wall,
        "call_wall_es": to_es(call_wall),
        "put_wall_spx": put_wall,
        "put_wall_es": to_es(put_wall),
        "top_strikes": [
            {"strike_spx": k, "strike_es": to_es(k), "gex": v,
             "side": "call" if v > 0 else "put"}
            for k, v in top
        ],
        "profile": flip_info.get("profile", []),
        "contracts": len(contracts),
    }
