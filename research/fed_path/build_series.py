"""A daily history of the number on the home page: bp priced by the last meeting
on the board, and the per-meeting deltas behind it.

WHY RECONSTRUCT RATHER THAN LOG FORWARD. The card computes the path from today's
settlements and stores nothing, so studying it by logging would mean waiting
months for a sample. Massive carries ZQ daily bars from roughly 2024-08, and the
construction is a pure function of settlements, so the whole series can be
rebuilt instead of waited for.

THE LOOK-AHEAD THIS MODULE EXISTS TO AVOID. `fed_probabilities(asof=...)` selects
which meetings are upcoming as of a date but prices them with TODAY'S board — it
was written for the live card, where those are the same thing. Reconstruction has
to rewind the prices too, so this module supplies its own as-of settles and calls
`path_from_settles`, which is the identical maths. It does not reimplement the
chaining or the next-month fix: the module docstring in src/fed_probabilities.py
records that small differences there were worth 180bp.

TWO TIMING RULES, both about not knowing things early:

  1. A session's settlement is published after that session closes, so the path
     computed from date D's settles is only ACTIONABLE from D+1. `settle_date` is
     kept distinct from `tradable_from` in the output for exactly this reason —
     any forward-return test must use the latter.

  2. EFFR for day D is published by the New York Fed the following morning, so
     the as-of anchor reads the last EFFR print STRICTLY BEFORE D. In practice
     the anchor usually comes off a meeting-free-month contract instead, but a
     fallback that quietly saw the future would be a silent one.
"""

from __future__ import annotations

import logging
import os
from datetime import date

import pandas as pd

from src.fed_probabilities import (FOMC_DATES, months_needed,
                                   path_from_settles, zq_ticker)

logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
BARS_PARQUET = os.path.join(HERE, "zq_bars.parquet")
EFFR_PARQUET = os.path.join(HERE, "effr.parquet")
SERIES_PARQUET = os.path.join(HERE, "priced_path.parquet")

# The strip to ingest. Starts before the first bar Massive carries so the gap is
# visible in the data rather than assumed, and runs past the last encoded FOMC
# date so the anchor and next-month contracts are always available.
FIRST_MONTH = (2024, 6)
LAST_MONTH = (2027, 6)

N_MEETINGS = 3          # what the card shows


def _month_range(first: tuple[int, int], last: tuple[int, int]) -> list[tuple[int, int]]:
    y, m = first
    out = []
    while (y, m) <= last:
        out.append((y, m))
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return out


def ingest_bars(refresh: bool = False) -> pd.DataFrame:
    """Daily bars for every ZQ contract in the window, cached to parquet.

    One request per contract, so this is slow and deliberately cached: the
    history of a settled contract cannot change, and only the live months at the
    front of the strip ever gain rows.
    """
    if os.path.exists(BARS_PARQUET) and not refresh:
        return pd.read_parquet(BARS_PARQUET)

    from src.futures_data import _get

    frames = []
    for y, m in _month_range(FIRST_MONTH, LAST_MONTH):
        tk = zq_ticker(y, m)
        j = _get(f"/futures/v1/aggs/{tk}", resolution="1day", limit=50000)
        rows = (j or {}).get("results") or []
        if not rows:
            logger.info(f"{tk}: no bars (outside the vendor's history)")
            continue
        df = pd.DataFrame(rows)
        df["contract"] = tk
        df["delivery"] = f"{y:04d}-{m:02d}"
        frames.append(df)
        logger.info(f"{tk}: {len(df)} bars")

    if not frames:
        raise RuntimeError("no ZQ bars returned for any contract")

    bars = pd.concat(frames, ignore_index=True)
    bars["session_end_date"] = pd.to_datetime(bars["session_end_date"]).dt.date

    # An UNSETTLED session returns settlement_price = 0.0, which is falsy but not
    # None — the trap that once had the whole board reading 100.0%. Prefer the
    # settlement, fall back to the close, and drop anything still non-positive
    # rather than letting a zero reach the maths.
    px = pd.to_numeric(bars.get("settlement_price"), errors="coerce")
    close = pd.to_numeric(bars.get("close"), errors="coerce")
    bars["px"] = px.where(px > 0, close)
    bars = bars[bars["px"] > 0].copy()

    bars = bars[["contract", "delivery", "session_end_date", "px"]]
    bars = bars.sort_values(["contract", "session_end_date"]).reset_index(drop=True)
    bars.to_parquet(BARS_PARQUET, index=False)
    return bars


def ingest_effr(refresh: bool = False) -> pd.Series:
    """Daily EFFR, indexed by the day the rate APPLIES to (not its publication)."""
    if os.path.exists(EFFR_PARQUET) and not refresh:
        cached = pd.read_parquet(EFFR_PARQUET)
        return pd.Series(cached["effr"].values,
                         index=pd.to_datetime(cached["date"]).dt.date)

    from src.data_engine import _fred_history
    df = _fred_history("EFFR", days=1500)
    if df is None or df.empty:
        raise RuntimeError("no EFFR history from FRED")
    out = pd.DataFrame({
        "date": pd.to_datetime(df.index).date,
        "effr": pd.to_numeric(df["Close"], errors="coerce").values,
    }).dropna()
    out.to_parquet(EFFR_PARQUET, index=False)
    return pd.Series(out["effr"].values, index=out["date"])


def settles_asof(bars: pd.DataFrame, d: date, months: list) -> dict:
    """Last positive settlement on or before `d`, per contract month.

    On-or-before rather than exact, because a contract does not print every
    session and an exact-date lookup would silently thin the strip — and a
    missing contract breaks the chain, which is reported but changes the answer.

    DO NOT ADD A STALENESS GUARD HERE. It looks like it needs one: 13% of
    sessions read a contract more than three days old, up to eighteen. Every one
    of those is the ANCHOR, and the anchor is by construction a MEETING-FREE
    month before the first upcoming decision — which, once that month has ended,
    is an expired contract whose final settlement is the average EFFR over a
    month in which the rate never changed. That is not a stale quote, it is the
    prevailing rate stated exactly, and it stays true until the next meeting
    moves it. Rejecting it on age would push the anchor onto spot EFFR and lose
    the cleaner read. Measured 2026-08-23: ZQG5 read on 2025-03-04..18, ZQQ4 on
    2024-09-03..17, and six more of the same shape — all anchors, no exceptions.
    """
    want = {zq_ticker(y, m) for (y, m) in months}
    sl = bars[(bars["contract"].isin(want)) & (bars["session_end_date"] <= d)]
    if sl.empty:
        return {}
    last = sl.groupby("contract")["session_end_date"].idxmax()
    return {r.contract: float(r.px) for r in sl.loc[last].itertuples()}


def build(refresh: bool = False) -> pd.DataFrame:
    """One row per session: what the board priced, and from when it was tradable."""
    bars = ingest_bars(refresh=refresh)
    effr = ingest_effr(refresh=refresh)

    sessions = sorted(bars["session_end_date"].unique())
    effr_dates = sorted(effr.index)

    rows = []
    for d in sessions:
        upcoming = [x for x in FOMC_DATES if x > d][:N_MEETINGS]
        if len(upcoming) < N_MEETINGS:
            continue                       # calendar runs out at the far end

        # STRICTLY before d: EFFR for day d is published the next morning.
        prior = [x for x in effr_dates if x < d]
        spot = float(effr.loc[prior[-1]]) if prior else None

        months = months_needed(d, upcoming)
        s = settles_asof(bars, d, months)
        if not s:
            continue

        path = path_from_settles(d, s, spot, n_meetings=N_MEETINGS)
        if not path.get("available"):
            continue

        # WHICH ANCHOR, recorded rather than inferred. The estimator switches
        # between an expired meeting-free contract and spot EFFR as the calendar
        # rolls, and the two disagree by a fraction of a bp, so a switch is a
        # small artificial step in the series. Inferring it from
        # `anchor_rate == spot_effr` does not work — the contract-implied rate
        # lands exactly on spot often enough that the proxy reports switches on
        # days when nothing switched.
        rec = {
            "settle_date": d,
            "cumulative_bp": path.get("cumulative_bp"),
            "anchor_rate": path.get("anchor_rate"),
            "anchor_source": "spot" if path.get("anchor") == "spot EFFR" else "contract",
            "spot_effr": path.get("spot_effr"),
            "n_priced": sum(1 for m in path["meetings"] if "delta_bp" in m),
        }
        for i, m in enumerate(path["meetings"], start=1):
            rec[f"m{i}_date"] = m.get("date")
            rec[f"m{i}_delta_bp"] = m.get("delta_bp")
            rec[f"m{i}_method"] = m.get("method")
            rec[f"m{i}_leverage"] = m.get("leverage")
        rows.append(rec)

    out = pd.DataFrame(rows).sort_values("settle_date").reset_index(drop=True)

    # A BROKEN CHAIN SHORTENS THE HORIZON WITHOUT SAYING SO. `cumulative_bp` is
    # measured to the last PRICED meeting, so a session that could only price two
    # of the three would contribute a two-meeting number to a three-meeting
    # series — a level shift that looks like repricing. None occur in the current
    # sample (501 of 501 price all three), which is exactly why this has to be a
    # guard rather than a note: the day it stops being true, nothing else would
    # complain.
    full = out["n_priced"] == N_MEETINGS
    if not full.all():
        logger.warning(f"dropping {int((~full).sum())} sessions that priced fewer "
                       f"than {N_MEETINGS} meetings — mixing horizons would look "
                       f"like a move in the level")
        out = out[full].reset_index(drop=True)

    # The settlement lands after the close, so the earliest session that could
    # have traded on it is the next one in the series. Derived here rather than
    # left to each study, because "off by one session" is the whole difference
    # between a forward test and a fitted one.
    out["tradable_from"] = out["settle_date"].shift(-1)

    out.to_parquet(SERIES_PARQUET, index=False)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    df = build(refresh=bool(os.environ.get("REFRESH")))
    print(f"{len(df)} sessions, {df['settle_date'].min()} to {df['settle_date'].max()}")
    print(df.tail(5).to_string(index=False))
