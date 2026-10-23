"""worker.update_options_metrics: the daily writer of metrics_history's options fields.

No network: daily bars and the chain scan are replaced with fixed values and the
database with a recorder.
"""
import math
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import worker  # noqa: E402
from src import cross_asset_vol as cav  # noqa: E402

ET = ZoneInfo("America/New_York")
AFTER_CLOSE = datetime(2026, 10, 22, 17, 45, tzinfo=ET)   # a Thursday


class _Query:
    def __init__(self, db):
        self.db, self.op = db, None

    def select(self, *_):
        self.op = "select"
        return self

    def eq(self, *_):
        return self

    def upsert(self, row, on_conflict=None):
        self.op = "upsert"
        self.db.upserts.append((row, on_conflict))
        return self

    def execute(self):
        return SimpleNamespace(data=list(self.db.existing) if self.op == "select" else [])


class _DB:
    def __init__(self, existing=()):
        self.existing, self.upserts = list(existing), []

    def table(self, name):
        assert name == "metrics_history"
        return _Query(self)


def _bars(last_day: str, n: int = 30, start: float = 100.0):
    """n daily bars ending on last_day, Polygon-shaped (t = ET midnight in ms)."""
    end = datetime.strptime(last_day, "%Y-%m-%d").replace(tzinfo=ET)
    return [{"t": int((end - timedelta(days=n - 1 - i)).timestamp() * 1000), "c": start * (1.01 if i % 2 else 0.995) ** i}
            for i in range(n)]


def _patch(monkeypatch, bars_by_ticker, metrics_rows, calls=None):
    monkeypatch.setenv("MASSIVE_API_KEY", "test")
    monkeypatch.setattr(worker, "METRICS_TICKERS", list(bars_by_ticker))

    def fake_bars(tk, api_key, end, days=100):
        if calls is not None:
            calls.append(tk)
        return bars_by_ticker[tk]

    monkeypatch.setattr(worker, "_daily_bars", fake_bars)
    monkeypatch.setattr(cav, "get_rfr", lambda: 0.04)
    monkeypatch.setattr(cav, "load_universe_data", lambda d, rfr: {tk: {} for tk in d})
    monkeypatch.setattr(cav, "compute_cross_asset_metrics",
                        lambda data, rfr: pd.DataFrame([r for r in metrics_rows if r["Ticker"] in data]))


def test_rms_vol_matches_the_old_hv20_formula():
    closes = [100 * 1.003 ** i * (1 + 0.01 * ((-1) ** i)) for i in range(40)]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    old = (sum(r ** 2 for r in rets[-20:]) / 20) ** 0.5 * (252 ** 0.5)
    assert worker._rms_vol(closes, 20) == old
    assert worker._rms_vol(closes[:20], 20) is None          # needs n + 1 closes


def test_rows_carry_options_fields_against_the_rows_own_hv20(monkeypatch):
    bars = {"SPY": _bars("2026-10-22"), "TLT": _bars("2026-10-22", start=90.0)}
    rows = [{"Ticker": "SPY", "Front_IV": 0.18, "Put_Skew": 1.12, "TS_Slope": 0.004, "PC_Ratio": 1.4},
            {"Ticker": "TLT", "Front_IV": 0.11, "Put_Skew": np.nan, "TS_Slope": -0.002, "PC_Ratio": 0.8}]
    _patch(monkeypatch, bars, rows)
    db = _DB()
    worker.update_options_metrics(db, now_et=AFTER_CLOSE)

    got = {r["ticker"]: r for r, conflict in db.upserts}
    assert set(got) == {"SPY", "TLT"}
    assert all(conflict == "user_id,ticker,date" for _, conflict in db.upserts)
    spy, tlt = got["SPY"], got["TLT"]
    hv = worker._rms_vol([b["c"] for b in bars["SPY"]], 20)
    assert spy["user_id"] == "worker" and spy["date"] == "2026-10-22"
    assert spy["atm_iv"] == 0.18 and spy["put_skew"] == 1.12 and spy["pc_ratio"] == 1.4
    assert math.isclose(spy["vrp"], 0.18 - hv) and math.isclose(spy["iv_hv_ratio"], 0.18 / hv)
    assert tlt["put_skew"] is None                            # unmeasurable stays empty, not 1.0
    # the hourly task owns spot/HV20/HV60; this writer must not overwrite them
    assert not {"spot", "hv20", "hv60"} & set(spy)


def test_nothing_before_the_close_or_on_a_weekend(monkeypatch):
    calls = []
    _patch(monkeypatch, {"SPY": _bars("2026-10-22")}, [{"Ticker": "SPY", "Front_IV": 0.18}], calls)
    for now in (datetime(2026, 10, 22, 16, 29, tzinfo=ET), datetime(2026, 10, 24, 18, 0, tzinfo=ET)):
        db = _DB()
        worker.update_options_metrics(db, now_et=now)
        assert db.upserts == [] and calls == []


def test_a_ticker_without_todays_bar_is_skipped(monkeypatch):
    # holiday / feed lag: the newest bar is yesterday's, so today's chains are stale
    _patch(monkeypatch, {"SPY": _bars("2026-10-21")}, [{"Ticker": "SPY", "Front_IV": 0.18}])
    db = _DB()
    worker.update_options_metrics(db, now_et=AFTER_CLOSE)
    assert db.upserts == []


def test_tickers_already_filled_today_are_not_recomputed(monkeypatch):
    calls = []
    _patch(monkeypatch, {"SPY": _bars("2026-10-22"), "TLT": _bars("2026-10-22")},
           [{"Ticker": "SPY", "Front_IV": 0.18}, {"Ticker": "TLT", "Front_IV": 0.11}], calls)
    db = _DB(existing=[{"ticker": "SPY", "atm_iv": 0.17}, {"ticker": "TLT", "atm_iv": None}])
    worker.update_options_metrics(db, now_et=AFTER_CLOSE)
    assert calls == ["TLT"]
    assert [r["ticker"] for r, _ in db.upserts] == ["TLT"]
