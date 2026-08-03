"""The macro setup: what is driving today, how much room it implies, and what it
does NOT tell you.

WHY THIS EXISTS. The card measured plenty and connected none of it. On
2026-08-03 it carried an Iran ceasefire, a coordinated yen intervention, softer
inflation and a Fed watching inflation closely — plus crude -6.5%, yen +6.4
sigma, and a session running 1.46x normal — and left the reader to assemble the
trade context by hand.

WHAT THE DATA ACTUALLY SUPPORTS, measured on overnight gaps over 1,193 sessions
so nothing here uses the session to predict itself:

    SIZE — supported.  crude |z| >= 2 in EITHER direction lifts P(range >= 1.3x)
    from a 27.5% base to 38.0%, p=0.043. Gold gapping >= 2 sigma lifts it to
    46.3%, p=0.013. The joint crude-plus-yen setup ran a 1.22x median range and
    44.4% wide (n=27, p=0.054).

    DIRECTION — NOT supported. Nothing survives:
        crude down >=2s   64.3% up   95% CI [49.2, 77.4]   p=0.216   n=42
        crude down >=1s   57.3%      [49.3, 65.0]          p=0.414   n=150
        yen up   >=2s     59.1%      [44.4, 72.7]          p=0.547   n=44
        crude<=-1s & yen>=1s 59.3%   [40.6, 76.1]          p=0.700   n=27
    against a 53.9% base rate. Every interval contains the base. The equity gap
    is no better: SPY gapping up >= 1 sigma closes up 54.0% against 53.9%.

Note WHICH size condition is significant: crude moving hard in EITHER direction,
not crude falling. The predictive content is the eventfulness, not the
disinflation channel — crude down alone is p=0.230. So a module that presented
"crude collapse -> disinflation -> equities up" as a forecast would be dressing a
null in a mechanism.

Hence the split this module enforces. The TRANSMISSION CHAIN is named because it
is standard economics and it explains what is moving. It is never used to
forecast direction. The SIZE expectation carries measured numbers. And the
direction is printed as an explicit null WITH its p-value, because a trader
acting on a narrative that feels directional when the data says it is only about
size is the specific error this is built to prevent.

The chain earns its place a different way: as a CONSISTENCY CHECK. It states what
each driver implies for other assets, and the tape is measured against that. When
crude collapses 6.5% and duration does not bid, the chain is broken and that
contradiction is the informative part — it was on the page today (TLT +0.07%) and
nothing pointed at it.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

logger = logging.getLogger(__name__)

_TZ = "America/New_York"

# Base rates over the same 1,193 sessions, so every lift below is quoted against
# the same denominator.
_BASE_UP_PCT = 53.9
_BASE_WIDE_PCT = 27.5

# One entry per driver the overnight tape can show. `wide_pct`, `median_x`, `n`
# and `p` are MEASURED; `mechanism` and `implies` are named standard economics
# and are used only for the consistency check, never to forecast.
#
# `implies` maps a symbol to the sign the chain expects. It is what makes the
# narrative falsifiable rather than decorative.
_DRIVERS = [
    {"key": "crude_down", "symbol": "USO", "test": lambda z: z <= -2,
     "label": "Crude breaking lower",
     "mechanism": ("A disinflationary impulse. Lower energy feeds headline inflation "
                   "within weeks, which relaxes the path the Fed is pricing."),
     "implies": {"TLT": +1, "XLE": -1},
     "wide_pct": 35.7, "median_x": 1.12, "n": 42, "p_size": 0.230},
    {"key": "crude_up", "symbol": "USO", "test": lambda z: z >= 2,
     "label": "Crude breaking higher",
     "mechanism": ("An inflationary supply impulse — the same channel in reverse, "
                   "and the one that tightens the Fed's path rather than easing it."),
     "implies": {"TLT": -1, "XLE": +1},
     "wide_pct": 40.5, "median_x": 1.20, "n": 37, "p_size": 0.230},
    {"key": "yen_up", "symbol": "FXY", "test": lambda z: z >= 2,
     "label": "Yen strengthening sharply",
     "mechanism": ("Intervention or a carry unwind. The yen funds a great deal of "
                   "global carry, so a violent move in it is a positioning event "
                   "before it is a macro one."),
     "implies": {"UUP": -1},
     "wide_pct": 36.4, "median_x": 1.19, "n": 44, "p_size": 0.500},
    {"key": "yen_down", "symbol": "FXY", "test": lambda z: z <= -2,
     "label": "Yen weakening sharply",
     "mechanism": "Carry re-established or intervention faded.",
     "implies": {"UUP": +1},
     "wide_pct": 30.8, "median_x": 1.10, "n": 39, "p_size": 0.600},
    {"key": "gold_up", "symbol": "GLD", "test": lambda z: z >= 2,
     "label": "Gold bid hard",
     "mechanism": ("Haven demand or a real-rate move. The single strongest measured "
                   "range signal in the basket."),
     "implies": {"TLT": +1},
     "wide_pct": 46.3, "median_x": 1.23, "n": 41, "p_size": 0.013},
    {"key": "credit_down", "symbol": "HYG", "test": lambda z: z <= -2,
     "label": "Credit under pressure",
     "mechanism": ("Credit leads equity at turns — high yield selling off before the "
                   "index is the classic sequence."),
     "implies": {"SPY": -1, "TLT": +1},
     "wide_pct": 45.6, "median_x": 1.20, "n": 57, "p_size": 0.010},
    {"key": "dollar_up", "symbol": "UUP", "test": lambda z: z >= 2,
     "label": "Dollar bid hard",
     "mechanism": "Tightening global financial conditions; a headwind for EM and commodities.",
     "implies": {"EEM": -1, "USO": -1},
     "wide_pct": 28.6, "median_x": 1.17, "n": 21, "p_size": 0.800},
]

# The one JOINT cell that was measured. Reported when both legs are present
# because correlated drivers must not have their individual lifts multiplied
# together — that would manufacture confidence out of the same observation
# counted twice.
_JOINT_CRUDE_FX = {
    "when": ("crude_down", "yen_up"),
    "label": "crude falling and the yen bid together",
    "wide_pct": 44.4, "median_x": 1.22, "n": 27, "p_size": 0.054,
    "up_pct": 59.3, "up_ci": (40.6, 76.1), "p_dir": 0.700,
}

# Printed verbatim whenever any driver is active. The numbers are the point.
_DIRECTION_NULL = {
    "base_up_pct": _BASE_UP_PCT,
    "tests": [
        {"label": "crude gaps down >= 2 sigma", "up_pct": 64.3, "ci": (49.2, 77.4),
         "p": 0.216, "n": 42},
        {"label": "crude gaps down >= 1 sigma", "up_pct": 57.3, "ci": (49.3, 65.0),
         "p": 0.414, "n": 150},
        {"label": "yen gaps up >= 2 sigma", "up_pct": 59.1, "ci": (44.4, 72.7),
         "p": 0.547, "n": 44},
        {"label": "crude down and yen up together", "up_pct": 59.3, "ci": (40.6, 76.1),
         "p": 0.700, "n": 27},
    ],
    "verdict": (
        "The setup does not condition direction. Every test above sits inside its "
        "confidence interval of the 53.9% base rate, and the equity gap adds nothing "
        "either — SPY gapping up more than a sigma closes up 54.0% against that same "
        "53.9%. What the setup DOES condition is how much room the session has."
    ),
}

_CHECK_SYMBOLS = ["TLT", "XLE", "UUP", "EEM", "SPY", "USO"]
_CONFIRM_MIN_PCT = 0.15      # below this a move is noise, not a confirmation


def _gap_and_day(symbol: str) -> dict | None:
    """Overnight gap in sigmas, and the move so far today."""
    try:
        from src.data_engine import polygon_history
        d = polygon_history(symbol, 200)
        if d is None or d.empty or len(d) < 62:
            return None
        gap = d["Open"] / d["Close"].shift(1) - 1
        sd = gap.shift(1).rolling(60).std()
        z = float(gap.iloc[-1] / sd.iloc[-1]) if sd.iloc[-1] else None
        day = float(d["Close"].iloc[-1] / d["Close"].iloc[-2] - 1) * 100
        return {"symbol": symbol, "z": z, "day_pct": round(day, 2)}
    except Exception as e:
        logger.debug(f"macro setup {symbol}: {e}")
        return None


def macro_setup(now: pd.Timestamp | None = None) -> dict:
    """Active drivers, the room they imply, and the direction null."""
    now = now or pd.Timestamp.now(tz=_TZ)

    symbols = sorted({d["symbol"] for d in _DRIVERS} | set(_CHECK_SYMBOLS))
    with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
        rows = [r for r in pool.map(_gap_and_day, symbols) if r]
    by = {r["symbol"]: r for r in rows}
    if len(by) < 4:
        return {"available": False, "reason": "too few cross-asset series priced"}

    active = []
    for d in _DRIVERS:
        r = by.get(d["symbol"])
        if not r or r.get("z") is None or not d["test"](r["z"]):
            continue

        # THE CHAIN, MEASURED. Each implication is checked against the tape, and
        # a failed one is the informative case — it means the mechanism everyone
        # is narrating is not the one the market is trading.
        checks = []
        for sym, sign in d["implies"].items():
            c = by.get(sym)
            if not c:
                continue
            moved = c["day_pct"]
            if abs(moved) < _CONFIRM_MIN_PCT:
                state = "flat"
            elif (moved > 0) == (sign > 0):
                state = "confirms"
            else:
                state = "contradicts"
            checks.append({
                "symbol": sym, "expected": "up" if sign > 0 else "down",
                "actual_pct": moved, "state": state,
            })

        active.append({
            **{k: d[k] for k in ("key", "label", "mechanism", "symbol")},
            "z": round(r["z"], 2), "day_pct": r["day_pct"],
            "wide_pct": d["wide_pct"], "median_x": d["median_x"],
            "n": d["n"], "p_size": d["p_size"],
            "size_significant": d["p_size"] < 0.05,
            "chain": checks,
            # A link that did NOT move is a failure of the mechanism just as much
            # as one that moved the wrong way. The chain predicts a move; no move
            # does not corroborate it. Counting only opposite moves suppressed the
            # most informative reading of 2026-08-03 — crude fell 5% and duration
            # sat at -0.04%, so bonds simply refused to price the disinflation the
            # equity market was celebrating, and the card said nothing.
            "broken_links": [c for c in checks if c["state"] in ("contradicts", "flat")],
        })

    if not active:
        return {
            "available": True, "drivers": [], "character": "no macro driver",
            "note": ("Nothing in the cross-asset tape gapped beyond two sigma overnight. "
                     "On a session like this the range expectation reverts to the base "
                     f"rate — {_BASE_WIDE_PCT:.0f}% of sessions run 1.3x normal or wider."),
            "direction": _DIRECTION_NULL,
        }

    active.sort(key=lambda a: (-a["wide_pct"], a["p_size"]))
    keys = {a["key"] for a in active}

    # Lifts are NOT combined. These drivers are correlated — a geopolitical
    # shock moves crude and the dollar and gold at once — so multiplying their
    # individual lifts counts one event several times. The strongest single
    # measured driver leads, and the joint cell is used only where one was
    # actually measured.
    lead = active[0]
    joint = (_JOINT_CRUDE_FX
             if set(_JOINT_CRUDE_FX["when"]).issubset(keys) else None)
    size = joint or lead

    broken = [b for a in active for b in a["broken_links"]]

    return {
        "available": True,
        "drivers": active,
        "n_drivers": len(active),
        "size": {
            "source": "joint cell" if joint else lead["label"],
            "median_x": size["median_x"],
            "wide_pct": size["wide_pct"],
            "base_wide_pct": _BASE_WIDE_PCT,
            "lift": round(size["wide_pct"] / _BASE_WIDE_PCT, 2),
            "n": size["n"], "p": size["p_size"],
            "significant": size["p_size"] < 0.05,
            "note": (
                f"Sessions with this setup ran a median {size['median_x']:.2f}x a normal "
                f"range and were 1.3x or wider {size['wide_pct']:.0f}% of the time "
                f"against a {_BASE_WIDE_PCT:.0f}% base (n={size['n']}, p={size['p_size']:.3f})."
            ),
            "combination_note": (
                "Not combined across drivers. A macro shock moves crude, the dollar and "
                "gold at once, so multiplying their individual lifts would count one "
                "event several times."
            ),
        },
        "direction": _DIRECTION_NULL,
        "broken_links": broken,
        "chain_note": (
            None if not broken else
            "The chain is not holding: "
            + "; ".join(
                (f"{b['symbol']} was expected {b['expected']} and has not moved "
                 f"({b['actual_pct']:+.2f}%)") if b["state"] == "flat" else
                (f"{b['symbol']} was expected {b['expected']} and is "
                 f"{b['actual_pct']:+.2f}%")
                for b in broken[:3])
            + ". The mechanism being narrated is not the one the tape is trading, "
              "which is worth more than a confirmation would be."
        ),
        "caveat": (
            "Drivers are detected from overnight gaps, so nothing here uses the session "
            "to predict itself. The mechanisms are standard economics, stated to explain "
            "what is moving and to be checked against the tape — never to forecast "
            "direction, which the measured tests below do not support."
        ),
    }
