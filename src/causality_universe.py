"""Macro universe for the /causality page.

A curated, ~50-series macro basket spanning equity, factor, FX, rates, credit,
commodity, vol, crypto, and FRED macro series. Each entry carries its preferred
data source and a default-stationarity transform (log_return / diff / level).

This is the *only* universe used by the causality page — the page is explicitly
macro-only, so single-name equities are excluded by design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Source = Literal["yfinance", "fred"]
Transform = Literal["log_return", "diff", "level"]
Category = Literal[
    "Equity", "Factor", "FX", "Rates", "Credit",
    "Commodity", "Vol", "Crypto", "Macro",
]


@dataclass(frozen=True)
class MacroSeries:
    symbol: str          # canonical key used by the API and UI
    label: str           # human-readable label
    category: Category
    source: Source
    ticker: str          # yfinance symbol or FRED series id
    transform: Transform # default stationarity transform
    description: str = ""


# ─────────────────────────────────────────────────────────────────
# CURATED MACRO UNIVERSE (v1)
#
# Selection logic (trader-first):
#  - Indexes broad enough to drive cross-asset narratives (SPX, NDX, RTY)
#  - Sector ETFs that anchor regime stories (XLK risk, XLE inflation, XLU rates)
#  - Factors that map to common quant tilts
#  - FX via TWI (DTWEXBGS) — broader signal than DXY alone
#  - Curve points (2/5/10/30) + slope (2s10s) — let users see flatteners directly
#  - Credit spreads as level series (BAMLH0A0HYM2, BAMLC0A0CM)
#  - Commodity heavyweights only — no obscure tickers that break in long history
#  - Vol: VIX, VVIX, OVX, MOVE
#  - Crypto: BTC, ETH
#  - Macro hard data: CPI YoY, NFP, ISM, M2, Fed balance sheet, jobless claims
#
# Each entry's transform is the default applied when the page auto-stationarizes.
# Prices → log returns. Rates / spreads / vol levels → first differences (the
# series is already a "level" but the change is what's tradeable). PMIs → level
# (already stationary by design).
# ─────────────────────────────────────────────────────────────────

MACRO_UNIVERSE: list[MacroSeries] = [
    # ── Equity indices ───────────────────────────────────────────
    MacroSeries("SPX",  "S&P 500",        "Equity", "yfinance", "^GSPC",  "log_return", "Broad US large-cap"),
    MacroSeries("NDX",  "Nasdaq 100",     "Equity", "yfinance", "^NDX",   "log_return", "US mega-cap tech"),
    MacroSeries("RTY",  "Russell 2000",   "Equity", "yfinance", "^RUT",   "log_return", "US small-cap"),
    MacroSeries("EFA",  "Developed ex-US","Equity", "yfinance", "EFA",    "log_return", "MSCI EAFE proxy"),
    MacroSeries("EEM",  "Emerging Mkts",  "Equity", "yfinance", "EEM",    "log_return", "MSCI EM proxy"),
    MacroSeries("FXI",  "China Lg-Cap",   "Equity", "yfinance", "FXI",    "log_return", "MSCI China large-cap"),

    # ── Sector ETFs (the 11 SPDRs) ───────────────────────────────
    MacroSeries("XLK", "Tech",           "Equity", "yfinance", "XLK",  "log_return"),
    MacroSeries("XLF", "Financials",     "Equity", "yfinance", "XLF",  "log_return"),
    MacroSeries("XLE", "Energy",         "Equity", "yfinance", "XLE",  "log_return"),
    MacroSeries("XLV", "Healthcare",     "Equity", "yfinance", "XLV",  "log_return"),
    MacroSeries("XLI", "Industrials",    "Equity", "yfinance", "XLI",  "log_return"),
    MacroSeries("XLU", "Utilities",      "Equity", "yfinance", "XLU",  "log_return"),
    MacroSeries("XLP", "Staples",        "Equity", "yfinance", "XLP",  "log_return"),
    MacroSeries("XLY", "Discretionary",  "Equity", "yfinance", "XLY",  "log_return"),
    MacroSeries("XLC", "Comms",          "Equity", "yfinance", "XLC",  "log_return"),
    MacroSeries("XLB", "Materials",      "Equity", "yfinance", "XLB",  "log_return"),
    MacroSeries("XLRE","Real Estate",    "Equity", "yfinance", "XLRE", "log_return"),

    # ── Factor ETFs ──────────────────────────────────────────────
    MacroSeries("MTUM", "Momentum",      "Factor", "yfinance", "MTUM", "log_return"),
    MacroSeries("QUAL", "Quality",       "Factor", "yfinance", "QUAL", "log_return"),
    MacroSeries("USMV", "Min Vol",       "Factor", "yfinance", "USMV", "log_return"),
    MacroSeries("IWF",  "Growth (R1G)",  "Factor", "yfinance", "IWF",  "log_return"),
    MacroSeries("IWD",  "Value (R1V)",   "Factor", "yfinance", "IWD",  "log_return"),

    # ── FX (trade-weighted dollar from FRED + key crosses) ───────
    MacroSeries("DXY",     "Dollar Index TWI",   "FX", "fred",     "DTWEXBGS", "log_return", "Broad TWI from Fed"),
    MacroSeries("EURUSD",  "EUR/USD",            "FX", "yfinance", "EURUSD=X", "log_return"),
    MacroSeries("USDJPY",  "USD/JPY",            "FX", "yfinance", "USDJPY=X", "log_return"),
    MacroSeries("USDCNY",  "USD/CNY",            "FX", "yfinance", "USDCNY=X", "log_return"),
    MacroSeries("USDBRL",  "USD/BRL",            "FX", "yfinance", "USDBRL=X", "log_return"),

    # ── US rates (yields are levels — diff for stationarity) ─────
    MacroSeries("UST2Y",   "2Y UST yield",       "Rates", "fred", "DGS2",  "diff"),
    MacroSeries("UST5Y",   "5Y UST yield",       "Rates", "fred", "DGS5",  "diff"),
    MacroSeries("UST10Y",  "10Y UST yield",      "Rates", "fred", "DGS10", "diff"),
    MacroSeries("UST30Y",  "30Y UST yield",      "Rates", "fred", "DGS30", "diff"),
    MacroSeries("REAL10Y", "10Y TIPS yield",     "Rates", "fred", "DFII10","diff"),
    # Slope is computed lazily as UST10Y - UST2Y inside the panel builder so
    # it stays consistent with whatever lookback was requested.

    # ── Credit spreads (option-adjusted, %) ──────────────────────
    MacroSeries("HY_OAS", "HY OAS",        "Credit", "fred", "BAMLH0A0HYM2", "diff", "ICE BofA US HY OAS"),
    MacroSeries("IG_OAS", "IG OAS",        "Credit", "fred", "BAMLC0A0CM",   "diff", "ICE BofA US Corp OAS"),
    MacroSeries("EM_OAS", "EM Sov Spread", "Credit", "fred", "BAMLEMCBPIOAS","diff", "ICE BofA EM Corp+ OAS"),

    # ── Commodities ──────────────────────────────────────────────
    MacroSeries("WTI",   "WTI Crude",     "Commodity", "fred", "DCOILWTICO",         "log_return"),
    MacroSeries("BRENT", "Brent Crude",   "Commodity", "fred", "DCOILBRENTEU",       "log_return"),
    MacroSeries("NG",    "Henry Hub Gas", "Commodity", "fred", "DHHNGSP",            "log_return"),
    MacroSeries("GOLD",  "Gold (futures)","Commodity", "yfinance","GC=F",            "log_return"),
    MacroSeries("COPPER","Copper",        "Commodity", "yfinance","HG=F",            "log_return"),

    # ── Volatility (levels — diff for stationarity) ──────────────
    MacroSeries("VIX",  "VIX (SPX 30d)", "Vol", "fred",     "VIXCLS", "diff"),
    MacroSeries("VVIX", "Vol of VIX",    "Vol", "yfinance", "^VVIX",  "diff"),
    MacroSeries("OVX",  "Oil Vol",       "Vol", "fred",     "OVXCLS", "diff"),
    MacroSeries("MOVE", "Rates Vol (MOVE)","Vol","yfinance","^MOVE",  "diff"),

    # ── Crypto ───────────────────────────────────────────────────
    MacroSeries("BTC", "Bitcoin",  "Crypto", "yfinance", "BTC-USD", "log_return"),
    MacroSeries("ETH", "Ethereum", "Crypto", "yfinance", "ETH-USD", "log_return"),

    # ── Macro hard data (FRED) ───────────────────────────────────
    # These have weekly/monthly cadence — daily-aligned panel will forward-fill.
    MacroSeries("CPI",     "CPI YoY %",         "Macro", "fred", "CPIAUCSL", "diff",  "All-items CPI; YoY computed downstream"),
    MacroSeries("NFP",     "Nonfarm Payrolls",  "Macro", "fred", "PAYEMS",   "diff"),
    MacroSeries("M2",      "M2 Money Supply",   "Macro", "fred", "M2SL",     "log_return"),
    MacroSeries("FED_BS",  "Fed Balance Sheet", "Macro", "fred", "WALCL",    "log_return"),
    MacroSeries("CLAIMS",  "Initial Jobless",   "Macro", "fred", "ICSA",     "diff"),
    MacroSeries("UMCSENT", "U-Mich Sentiment",  "Macro", "fred", "UMCSENT",  "diff"),
]


SYMBOLS_BY_KEY: dict[str, MacroSeries] = {s.symbol: s for s in MACRO_UNIVERSE}


def get_series(symbol: str) -> MacroSeries:
    s = SYMBOLS_BY_KEY.get(symbol)
    if s is None:
        raise KeyError(f"Unknown macro symbol: {symbol}")
    return s


def list_universe() -> list[dict]:
    """Flat dict list for the API/UI symbol picker."""
    return [
        {
            "symbol": s.symbol,
            "label": s.label,
            "category": s.category,
            "source": s.source,
            "transform": s.transform,
            "description": s.description,
        }
        for s in MACRO_UNIVERSE
    ]


def list_categories() -> dict[str, list[str]]:
    """Symbols grouped by category — drives the picker UI."""
    out: dict[str, list[str]] = {}
    for s in MACRO_UNIVERSE:
        out.setdefault(s.category, []).append(s.symbol)
    return out
