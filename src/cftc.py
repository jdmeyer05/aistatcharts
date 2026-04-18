"""CFTC Commitments of Traders — wide-universe data layer.

Covers four report types via the CFTC Public Reporting API (Socrata):
  - Disaggregated Futures Only — Managed Money, Producer/Merchant, Swap Dealer
      dataset: 72hh-3qpy       (from ~2006)
  - Traders in Financial Futures (TFF) — Leveraged Funds, Asset Manager, Dealer
      dataset: gpe5-46if       (from ~2010)
  - Legacy Futures Only — Commercials, Non-Commercials, Non-Reportables
      dataset: 6dca-aqww       (from 1986)
  - Supplemental — Index Investors (for commodity-index-traded contracts)
      dataset: 4zgm-a668       (from 2007)

Contract universe: ~45 flagship contracts across equities, rates, FX, energy,
metals, grains, softs, meats. Each contract knows which report type is the
canonical "speculator" side (TFF Leveraged Funds for financials, Disaggregated
Managed Money for commodities) and whether to blend in a Legacy Commercial
view for the longest-history divergence signal.

The Socrata API is free, rate-limited but generous, and updates every Friday
3:30pm ET after the Tuesday-close snapshot.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd
import requests

from src._cache_util import result_cached as _result_cached

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Contract universe
# ═══════════════════════════════════════════════════════════════════

AssetClass = Literal["equity", "rates", "fx", "energy", "metals", "grains", "softs", "meats"]
ReportType = Literal["disaggregated", "tff", "legacy", "supplemental"]


@dataclass(frozen=True)
class ContractSpec:
    """A single CFTC-tracked contract."""
    code: str                   # CFTC contract market code (8-digit with hyphen stripped)
    name: str                   # Display name ("WTI Crude Oil")
    symbol: str                 # Trading symbol ("CL")
    asset_class: AssetClass
    # Which report has the speculator data we care about most.
    # Commodities → disaggregated (Managed Money). Financials → TFF (Leveraged Funds).
    spec_report: ReportType
    # If true, also fetch Legacy for the long-history commercial-vs-spec divergence
    # view. True for everything except VIX and newer contracts.
    track_legacy: bool = True
    # Higher priority = shown first in heatmaps and dashboards.
    priority: int = 0


CONTRACTS: list[ContractSpec] = [
    # ── Equities (TFF — Leveraged Funds) ──────────────────────────
    ContractSpec("13874A", "S&P 500 E-mini",     "ES",  "equity", "tff", True,  100),
    ContractSpec("20974A", "Nasdaq 100 E-mini",  "NQ",  "equity", "tff", True,  95),
    ContractSpec("124603", "Dow E-mini",         "YM",  "equity", "tff", True,  70),
    ContractSpec("239742", "Russell 2000 E-mini","RTY", "equity", "tff", True,  80),
    ContractSpec("1170E1", "VIX Futures",        "VX",  "equity", "tff", False, 75),

    # ── Rates (TFF — Leveraged Funds) ─────────────────────────────
    ContractSpec("042601", "2-Year Note",   "ZT",  "rates", "tff", True,  70),
    ContractSpec("044601", "5-Year Note",   "ZF",  "rates", "tff", True,  65),
    ContractSpec("043602", "10-Year Note",  "ZN",  "rates", "tff", True,  95),
    ContractSpec("020601", "30-Year Bond",  "ZB",  "rates", "tff", True,  75),
    ContractSpec("020604", "Ultra T-Bond",  "UB",  "rates", "tff", True,  55),
    ContractSpec("045601", "30-Day Fed Funds","ZQ","rates", "tff", False, 60),
    ContractSpec("134741", "SOFR 3-Month",  "SR3", "rates", "tff", False, 50),

    # ── FX (TFF — Leveraged Funds) ────────────────────────────────
    ContractSpec("098662", "US Dollar Index","DX", "fx", "tff", True,  90),
    ContractSpec("099741", "Euro FX",       "6E",  "fx", "tff", True,  85),
    ContractSpec("097741", "Japanese Yen",  "6J",  "fx", "tff", True,  80),
    ContractSpec("096742", "British Pound", "6B",  "fx", "tff", True,  70),
    ContractSpec("232741", "Australian Dollar","6A","fx","tff", True,  65),
    ContractSpec("090741", "Canadian Dollar","6C", "fx", "tff", True,  60),
    ContractSpec("095741", "Mexican Peso",  "6M",  "fx", "tff", True,  55),
    ContractSpec("092741", "Swiss Franc",   "6S",  "fx", "tff", True,  50),
    ContractSpec("112741", "NZ Dollar",     "6N",  "fx", "tff", True,  45),

    # ── Energy (Disaggregated — Managed Money) ────────────────────
    ContractSpec("067651", "WTI Crude Oil", "CL",  "energy", "disaggregated", True,  95),
    ContractSpec("023651", "Natural Gas",   "NG",  "energy", "disaggregated", True,  85),
    ContractSpec("111659", "RBOB Gasoline", "RB",  "energy", "disaggregated", True,  65),
    ContractSpec("022651", "Heating Oil",   "HO",  "energy", "disaggregated", True,  60),

    # ── Metals (Disaggregated — Managed Money) ────────────────────
    ContractSpec("088691", "Gold",          "GC",  "metals", "disaggregated", True,  90),
    ContractSpec("084691", "Silver",        "SI",  "metals", "disaggregated", True,  80),
    ContractSpec("085692", "Copper",        "HG",  "metals", "disaggregated", True,  85),
    ContractSpec("076651", "Platinum",      "PL",  "metals", "disaggregated", True,  55),
    ContractSpec("075651", "Palladium",     "PA",  "metals", "disaggregated", True,  45),

    # ── Grains (Disaggregated — Managed Money) ────────────────────
    ContractSpec("002602", "Corn",          "ZC",  "grains", "disaggregated", True,  75),
    ContractSpec("005602", "Soybeans",      "ZS",  "grains", "disaggregated", True,  75),
    ContractSpec("001602", "Chicago Wheat", "ZW",  "grains", "disaggregated", True,  70),
    ContractSpec("001612", "KC Wheat",      "KE",  "grains", "disaggregated", True,  55),
    ContractSpec("026603", "Soybean Meal",  "ZM",  "grains", "disaggregated", True,  50),
    ContractSpec("007601", "Soybean Oil",   "ZL",  "grains", "disaggregated", True,  50),

    # ── Softs (Disaggregated — Managed Money) ─────────────────────
    ContractSpec("080732", "Sugar #11",     "SB",  "softs",  "disaggregated", True,  60),
    ContractSpec("083731", "Coffee C",      "KC",  "softs",  "disaggregated", True,  60),
    ContractSpec("073732", "Cocoa",         "CC",  "softs",  "disaggregated", True,  55),
    ContractSpec("033661", "Cotton #2",     "CT",  "softs",  "disaggregated", True,  55),
    ContractSpec("040701", "Orange Juice",  "OJ",  "softs",  "disaggregated", True,  35),
    ContractSpec("058643", "Lumber",        "LBR", "softs",  "disaggregated", False, 40),

    # ── Meats (Disaggregated — Managed Money) ─────────────────────
    ContractSpec("057642", "Live Cattle",   "LE",  "meats",  "disaggregated", True,  55),
    ContractSpec("061641", "Feeder Cattle", "GF",  "meats",  "disaggregated", True,  45),
    ContractSpec("054642", "Lean Hogs",     "HE",  "meats",  "disaggregated", True,  55),
]

CONTRACTS_BY_CODE: dict[str, ContractSpec] = {c.code: c for c in CONTRACTS}
CONTRACTS_BY_SYMBOL: dict[str, ContractSpec] = {c.symbol: c for c in CONTRACTS}


# ═══════════════════════════════════════════════════════════════════
# Socrata dataset endpoints + field mappings
# ═══════════════════════════════════════════════════════════════════

DATASETS = {
    "disaggregated": "https://publicreporting.cftc.gov/resource/72hh-3qpy.json",
    "tff":           "https://publicreporting.cftc.gov/resource/gpe5-46if.json",
    "legacy":        "https://publicreporting.cftc.gov/resource/6dca-aqww.json",
    "supplemental":  "https://publicreporting.cftc.gov/resource/4zgm-a668.json",
}

# Schema fields per report. The API is quirky — casing and prefix conventions
# differ between datasets. These mappings normalize to a common internal shape.
_DISAGG_FIELDS = {
    "spec_long":  "m_money_positions_long_all",
    "spec_short": "m_money_positions_short_all",
    "spec_spread":"m_money_positions_spread_all",
    "spec_n_traders_long":  "traders_m_money_long_all",
    "spec_n_traders_short": "traders_m_money_short_all",
    "comm_long":  "prod_merc_positions_long",      # Producer/Merchant as commercial
    "comm_short": "prod_merc_positions_short",
    "swap_long":  "swap_positions_long_all",
    "swap_short": "swap__positions_short_all",     # Note: field has double underscore in Socrata
    "other_long": "other_rept_positions_long",
    "other_short":"other_rept_positions_short",
    "oi": "open_interest_all",
    "conc_gross_lt4":  "conc_gross_le_4_tdr_long", # top-4 gross long share
    "conc_gross_st4":  "conc_gross_le_4_tdr_short",
    "conc_gross_lt8":  "conc_gross_le_8_tdr_long",
    "conc_gross_st8":  "conc_gross_le_8_tdr_short",
    "traders_total":   "traders_tot_all",
}

_TFF_FIELDS = {
    "spec_long":  "lev_money_positions_long",      # Leveraged Funds = CTAs + HFs
    "spec_short": "lev_money_positions_short",
    "spec_spread":"lev_money_positions_spread",
    "spec_n_traders_long":  "traders_lev_money_long_all",
    "spec_n_traders_short": "traders_lev_money_short_all",
    "asset_mgr_long":  "asset_mgr_positions_long",
    "asset_mgr_short": "asset_mgr_positions_short",
    "dealer_long": "dealer_positions_long_all",
    "dealer_short":"dealer_positions_short_all",
    "other_long":  "other_rept_positions_long",
    "other_short": "other_rept_positions_short",
    "oi": "open_interest_all",
    "conc_gross_lt4":  "conc_gross_le_4_tdr_long_all",
    "conc_gross_st4":  "conc_gross_le_4_tdr_short_all",
    "conc_gross_lt8":  "conc_gross_le_8_tdr_long_all",
    "conc_gross_st8":  "conc_gross_le_8_tdr_short_all",
    "traders_total":   "traders_tot_all",
}

_LEGACY_FIELDS = {
    "spec_long":  "noncomm_positions_long_all",
    "spec_short": "noncomm_positions_short_all",
    "spec_spread":"noncomm_postions_spread_all",   # CFTC typo preserved in schema
    "comm_long":  "comm_positions_long_all",
    "comm_short": "comm_positions_short_all",
    "nonrept_long":  "nonrept_positions_long_all",
    "nonrept_short": "nonrept_positions_short_all",
    "oi": "open_interest_all",
    "conc_gross_lt4":  "conc_gross_le_4_tdr_long",
    "conc_gross_st4":  "conc_gross_le_4_tdr_short",
    "traders_total":   "traders_tot_all",
}


# ═══════════════════════════════════════════════════════════════════
# In-memory cache — weekly data, forgiving TTL
# ═══════════════════════════════════════════════════════════════════

# CFTC releases Friday 3:30pm ET. Safe to cache for most of the following week.
# Cache key: (report_type, contract_code) → (fetched_at, DataFrame)
_CACHE: dict[tuple[str, str], tuple[datetime, pd.DataFrame]] = {}
_CACHE_TTL = timedelta(hours=24)

# Circuit breaker for the Socrata API — during outages it hard-fails every
# request and spams logs. After 3 consecutive failures we pause Socrata for
# SOCRATA_BACKOFF and serve from direct-file fallback. Once the backoff window
# elapses we retry — so when CFTC brings the API back we pick it up automatically
# without a restart.
_SOCRATA_FAIL_COUNT = 0
_SOCRATA_FAIL_THRESHOLD = 3
_SOCRATA_DISABLED_UNTIL: datetime | None = None
_SOCRATA_BACKOFF = timedelta(minutes=30)


def _cache_key(report: ReportType, code: str) -> tuple[str, str]:
    return (report, code)


# ═══════════════════════════════════════════════════════════════════
# Raw fetchers — one per report type. Normalize field names.
# ═══════════════════════════════════════════════════════════════════

def _safe_int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fetch_socrata(url: str, cftc_code: str, limit: int = 5000) -> list[dict]:
    """Pull raw rows for one contract from a Socrata dataset."""
    params = {
        "$limit": limit,
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "cftc_contract_market_code": cftc_code,
    }
    r = requests.get(url, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def _cached_fetch(report: ReportType, code: str, limit: int = 5000) -> pd.DataFrame:
    key = _cache_key(report, code)
    cached = _CACHE.get(key)
    if cached and (datetime.utcnow() - cached[0]) < _CACHE_TTL:
        return cached[1]

    global _SOCRATA_FAIL_COUNT, _SOCRATA_DISABLED_UNTIL
    url = DATASETS[report]
    rows: list[dict] = []
    now = datetime.utcnow()
    socrata_ok = _SOCRATA_DISABLED_UNTIL is None or now >= _SOCRATA_DISABLED_UNTIL
    if socrata_ok:
        try:
            rows = _fetch_socrata(url, code, limit)
            # Successful Socrata call — reset the breaker completely.
            if _SOCRATA_FAIL_COUNT > 0 or _SOCRATA_DISABLED_UNTIL is not None:
                logger.info("CFTC Socrata recovered — resuming primary path")
            _SOCRATA_FAIL_COUNT = 0
            _SOCRATA_DISABLED_UNTIL = None
        except Exception as e:
            _SOCRATA_FAIL_COUNT += 1
            if _SOCRATA_FAIL_COUNT <= _SOCRATA_FAIL_THRESHOLD:
                logger.warning(f"CFTC {report} Socrata fetch failed for {code}: {e}")
            if _SOCRATA_FAIL_COUNT >= _SOCRATA_FAIL_THRESHOLD:
                _SOCRATA_DISABLED_UNTIL = now + _SOCRATA_BACKOFF
                logger.warning(
                    "CFTC Socrata paused for %d min after %d failures; direct-file fallback active",
                    int(_SOCRATA_BACKOFF.total_seconds() / 60),
                    _SOCRATA_FAIL_THRESHOLD,
                )
            rows = []

    # Socrata path produced data — normalize and cache.
    if rows:
        df = _normalize(report, rows)
        _CACHE[key] = (datetime.utcnow(), df)
        return df

    # Socrata empty or down — try the direct-file fallback (cftc.gov archives).
    # Supplemental not yet supported there; only disaggregated / tff / legacy.
    if report in ("disaggregated", "tff", "legacy"):
        try:
            from src.cftc_direct import fetch_history
            df = fetch_history(report, code, years=5)
            if not df.empty:
                logger.info(f"CFTC {report} served via direct-file fallback for {code}: {len(df)} rows")
                _CACHE[key] = (datetime.utcnow(), df)
                return df
        except Exception as e:
            logger.warning(f"CFTC {report} direct-file fallback failed for {code}: {e}")

    # Nothing worked — serve stale if we have any, else empty.
    if cached:
        return cached[1]
    df = pd.DataFrame()
    _CACHE[key] = (datetime.utcnow(), df)
    return df


def _normalize(report: ReportType, rows: list[dict]) -> pd.DataFrame:
    """Flatten Socrata rows into a common internal schema."""
    if report == "disaggregated":
        fields = _DISAGG_FIELDS
    elif report == "tff":
        fields = _TFF_FIELDS
    elif report == "legacy":
        fields = _LEGACY_FIELDS
    else:
        # Supplemental — not a primary path yet.
        fields = _LEGACY_FIELDS

    out: list[dict] = []
    for r in rows:
        row = {
            "date": pd.to_datetime(r.get("report_date_as_yyyy_mm_dd"), errors="coerce"),
            "contract_name": r.get("contract_market_name", ""),
            "oi": _safe_int(r.get(fields.get("oi", "open_interest_all"))),
        }
        for internal, src in fields.items():
            if internal == "oi":
                continue
            if "conc_" in internal:
                row[internal] = _safe_float(r.get(src))
            else:
                row[internal] = _safe_int(r.get(src))
        out.append(row)
    df = pd.DataFrame(out).dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    # Derived columns
    if "spec_long" in df.columns and "spec_short" in df.columns:
        df["spec_net"] = df["spec_long"] - df["spec_short"]
        df["spec_gross"] = df["spec_long"] + df["spec_short"]
        df["spec_pct_oi"] = np.where(df["oi"] > 0, df["spec_net"] / df["oi"] * 100, np.nan)
    if "comm_long" in df.columns and "comm_short" in df.columns:
        df["comm_net"] = df["comm_long"] - df["comm_short"]
        df["comm_pct_oi"] = np.where(df["oi"] > 0, df["comm_net"] / df["oi"] * 100, np.nan)
    if {"spec_n_traders_long", "spec_n_traders_short"}.issubset(df.columns):
        df["spec_n_traders"] = df["spec_n_traders_long"] + df["spec_n_traders_short"]
    return df


# Public single-report fetchers
def fetch_disaggregated(code: str) -> pd.DataFrame:
    return _cached_fetch("disaggregated", code)


def fetch_tff(code: str) -> pd.DataFrame:
    return _cached_fetch("tff", code)


def fetch_legacy(code: str) -> pd.DataFrame:
    return _cached_fetch("legacy", code)


# ═══════════════════════════════════════════════════════════════════
# Derived metrics
# ═══════════════════════════════════════════════════════════════════

def rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    """Percentile rank of each value within its rolling window (0..1)."""
    return series.rolling(window, min_periods=max(4, window // 4)).apply(
        lambda w: (w.rank(pct=True).iloc[-1]) if len(w) else np.nan, raw=False
    )


def cot_index(series: pd.Series, window: int = 156) -> pd.Series:
    """COT Index: (value − rolling_min) / (rolling_max − rolling_min), 0..1.
    Default 156 weeks ≈ 3 years."""
    rmin = series.rolling(window, min_periods=max(4, window // 4)).min()
    rmax = series.rolling(window, min_periods=max(4, window // 4)).max()
    span = (rmax - rmin).replace(0, np.nan)
    return (series - rmin) / span


def rolling_zscore(series: pd.Series, window: int = 156) -> pd.Series:
    r_mean = series.rolling(window, min_periods=max(4, window // 4)).mean()
    r_std = series.rolling(window, min_periods=max(4, window // 4)).std().replace(0, np.nan)
    return (series - r_mean) / r_std


def weekly_change(series: pd.Series, lag: int = 1) -> pd.Series:
    return series - series.shift(lag)


# ═══════════════════════════════════════════════════════════════════
# Unified contract view — the main per-contract API
# ═══════════════════════════════════════════════════════════════════

# Cache for contract_history outputs (the rolling percentile computations are
# the real expense, not the raw fetch). Keyed by (code, lookback_weeks).
_HISTORY_CACHE: dict[tuple[str, int], tuple[datetime, pd.DataFrame]] = {}
_HISTORY_TTL = timedelta(hours=24)


def contract_history(code: str, lookback_weeks: int = 260) -> pd.DataFrame:
    """One DataFrame per contract with speculator + commercial signals,
    percentiles, COT Index, z-score, weekly/4w changes, divergence Z.

    Speculator side = Managed Money (commodities) or Leveraged Funds (financials).
    Commercial side = Producer/Merchant (commodities) or Legacy Commercials (if enabled).
    """
    # Result-level cache — rolling percentile/z-score computations dominate
    # runtime for the cross-contract analog match and the P&L reconstruction.
    cache_key = (code, lookback_weeks)
    cached_entry = _HISTORY_CACHE.get(cache_key)
    if cached_entry and (datetime.utcnow() - cached_entry[0]) < _HISTORY_TTL:
        return cached_entry[1]

    spec = CONTRACTS_BY_CODE.get(code)
    if not spec:
        return pd.DataFrame()

    spec_df = _cached_fetch(spec.spec_report, code)
    if spec_df.empty:
        return pd.DataFrame()

    out = spec_df[["date", "oi", "spec_long", "spec_short", "spec_spread",
                   "spec_net", "spec_gross", "spec_pct_oi",
                   "conc_gross_lt4", "conc_gross_lt8",
                   "spec_n_traders_long", "spec_n_traders_short", "spec_n_traders"]].copy()

    # Commercial side — prefer Legacy (longer history) if enabled, else Disaggregated producer/merchant
    if spec.track_legacy:
        leg = _cached_fetch("legacy", code)
        if not leg.empty:
            leg_sub = leg[["date", "comm_net", "comm_pct_oi", "comm_long", "comm_short"]].copy()
            out = out.merge(leg_sub, on="date", how="left")
    elif "comm_net" in spec_df.columns:
        out["comm_net"] = spec_df["comm_net"]
        out["comm_pct_oi"] = spec_df.get("comm_pct_oi", pd.Series(index=spec_df.index, dtype=float))

    out = out.sort_values("date").reset_index(drop=True)

    # Derived — compute on full history, then trim
    out["spec_pctile_3y"] = rolling_percentile(out["spec_net"], 156)
    out["spec_pctile_1y"] = rolling_percentile(out["spec_net"], 52)
    out["cot_index_3y"] = cot_index(out["spec_net"], 156)
    out["spec_zscore_3y"] = rolling_zscore(out["spec_net"], 156)
    out["spec_chg_1w"] = weekly_change(out["spec_net"], 1)
    out["spec_chg_4w"] = weekly_change(out["spec_net"], 4)

    if "comm_net" in out.columns:
        out["comm_pctile_3y"] = rolling_percentile(out["comm_net"], 156)
        # Divergence spread: spec z − comm z. Large positive = specs long, commercials short.
        out["spec_vs_comm_z"] = (
            rolling_zscore(out["spec_net"], 156)
            - rolling_zscore(out["comm_net"], 156)
        )
    else:
        out["comm_pctile_3y"] = np.nan
        out["spec_vs_comm_z"] = np.nan

    # Concentration change — whale activity detector
    out["conc_lt4_chg_4w"] = weekly_change(out["conc_gross_lt4"], 4)

    # Trader-count signal: whale vs consensus. High net + low trader count = whale.
    # Normalize per-contract: trader count z-score.
    out["traders_zscore_3y"] = rolling_zscore(out["spec_n_traders"].astype(float), 156)

    if lookback_weeks and len(out) > lookback_weeks:
        out = out.tail(lookback_weeks).reset_index(drop=True)
    _HISTORY_CACHE[cache_key] = (datetime.utcnow(), out)
    return out


# ═══════════════════════════════════════════════════════════════════
# Heatmap snapshot — latest percentile for every contract
# ═══════════════════════════════════════════════════════════════════

def _heatmap_row(spec: ContractSpec) -> dict | None:
    """Compute one heatmap tile for a contract."""
    h = contract_history(spec.code, lookback_weeks=260)
    if h.empty:
        return None
    last = h.iloc[-1]
    return {
        "code": spec.code,
        "symbol": spec.symbol,
        "name": spec.name,
        "asset_class": spec.asset_class,
        "report_type": spec.spec_report,
        "date": last["date"].strftime("%Y-%m-%d") if pd.notna(last["date"]) else None,
        "spec_net": int(last["spec_net"]) if pd.notna(last["spec_net"]) else None,
        "spec_pct_oi": round(float(last["spec_pct_oi"]), 2) if pd.notna(last["spec_pct_oi"]) else None,
        "pctile_3y": round(float(last["spec_pctile_3y"]), 3) if pd.notna(last["spec_pctile_3y"]) else None,
        "pctile_1y": round(float(last["spec_pctile_1y"]), 3) if pd.notna(last["spec_pctile_1y"]) else None,
        "cot_index_3y": round(float(last["cot_index_3y"]), 3) if pd.notna(last["cot_index_3y"]) else None,
        "zscore_3y": round(float(last["spec_zscore_3y"]), 2) if pd.notna(last["spec_zscore_3y"]) else None,
        "chg_1w": int(last["spec_chg_1w"]) if pd.notna(last["spec_chg_1w"]) else None,
        "chg_4w": int(last["spec_chg_4w"]) if pd.notna(last["spec_chg_4w"]) else None,
        "chg_1w_sign": "up" if (pd.notna(last["spec_chg_1w"]) and last["spec_chg_1w"] > 0) else "down",
        "comm_pctile_3y": round(float(last["comm_pctile_3y"]), 3) if pd.notna(last["comm_pctile_3y"]) else None,
        "divergence_z": round(float(last["spec_vs_comm_z"]), 2) if pd.notna(last["spec_vs_comm_z"]) else None,
        "oi": int(last["oi"]) if pd.notna(last["oi"]) else None,
        "conc_lt4": round(float(last["conc_gross_lt4"]), 1) if pd.notna(last["conc_gross_lt4"]) else None,
    }


@_result_cached("heatmap_snapshot")
def heatmap_snapshot() -> list[dict]:
    """One row per contract with today's positioning percentiles + deltas.
    Parallel fan-out across 45 contracts — cold time ~6× faster than serial."""
    specs = sorted(CONTRACTS, key=lambda c: -c.priority)
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [r for r in pool.map(_heatmap_row, specs) if r is not None]
    return rows


# ═══════════════════════════════════════════════════════════════════
# Divergence scanner — ranked table of extreme spec-vs-commercial spreads
# ═══════════════════════════════════════════════════════════════════

def _divergence_row(spec: ContractSpec, min_abs_z: float) -> dict | None:
    if not spec.track_legacy:
        return None
    h = contract_history(spec.code, lookback_weeks=260)
    if h.empty or "spec_vs_comm_z" not in h.columns:
        return None
    last = h.iloc[-1]
    if pd.isna(last["spec_vs_comm_z"]):
        return None
    z = float(last["spec_vs_comm_z"])
    if abs(z) < min_abs_z:
        return None
    return {
        "code": spec.code,
        "symbol": spec.symbol,
        "name": spec.name,
        "asset_class": spec.asset_class,
        "date": last["date"].strftime("%Y-%m-%d"),
        "divergence_z": round(z, 2),
        "spec_pctile_3y": round(float(last["spec_pctile_3y"]), 3) if pd.notna(last["spec_pctile_3y"]) else None,
        "comm_pctile_3y": round(float(last["comm_pctile_3y"]), 3) if pd.notna(last["comm_pctile_3y"]) else None,
        "spec_net": int(last["spec_net"]),
        "comm_net": int(last["comm_net"]) if pd.notna(last["comm_net"]) else None,
    }


@_result_cached("divergence_scan")
def divergence_scan(min_abs_z: float = 1.0) -> list[dict]:
    """Rank contracts by |spec_vs_comm_z|. Parallel fan-out."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [r for r in pool.map(lambda s: _divergence_row(s, min_abs_z), CONTRACTS) if r is not None]
    rows.sort(key=lambda r: -abs(r["divergence_z"]))
    return rows


# ═══════════════════════════════════════════════════════════════════
# Regime composites — aggregated positioning signals
# ═══════════════════════════════════════════════════════════════════

def _latest_z(code: str) -> float | None:
    h = contract_history(code, lookback_weeks=260)
    if h.empty or pd.isna(h.iloc[-1].get("spec_zscore_3y")):
        return None
    return float(h.iloc[-1]["spec_zscore_3y"])


@_result_cached("regime_composites")
def regime_composites() -> dict:
    """Four synthesized signals that don't exist per-contract:

    - risk_on_off:   mean spec z across risk assets (ES + NQ + oil + copper + HYG-proxy via Russell)
    - reflation:     oil + copper z − dollar z − bond z (positive = reflation positioning)
    - safe_haven:    gold z + bond z + JPY z (positive = flight-to-safety buildup)
    - dollar:        short-EUR + short-JPY + short-GBP + short-AUD + short-CAD + short-MXN
                     (positive = specs positioned for strong USD)
    """
    # Lookup helpers
    def z(symbol: str) -> float:
        spec = CONTRACTS_BY_SYMBOL.get(symbol)
        if not spec:
            return 0.0
        zz = _latest_z(spec.code)
        return 0.0 if zz is None else zz

    risk_on = np.nanmean([z("ES"), z("NQ"), z("CL"), z("HG"), z("RTY")])
    # VIX positioning is inverted for risk-on: long VIX = risk-off
    vix_z = z("VX")
    reflation = np.nanmean([z("CL"), z("HG"), -z("DX"), -z("ZN")])
    safe_haven = np.nanmean([z("GC"), z("ZN"), z("6J")])
    # Dollar: spec long USD ≈ spec short the other majors
    dollar = np.nanmean([-z("6E"), -z("6J"), -z("6B"), -z("6A"), -z("6C"), -z("6M")])

    return {
        "risk_on_off": round(float(risk_on - vix_z * 0.5), 2),
        "reflation":   round(float(reflation), 2),
        "safe_haven":  round(float(safe_haven), 2),
        "dollar":      round(float(dollar), 2),
        "interpretation": {
            "risk_on_off": "Positive = specs risk-on across equities/oil/copper. Negative = risk-off posture.",
            "reflation":   "Positive = commodity specs long + short USD + short bonds. Classic reflation trade.",
            "safe_haven":  "Positive = specs are building gold/bond/JPY exposure. Flight-to-safety.",
            "dollar":      "Positive = specs positioned for USD strength. Contrarian at extremes.",
        },
    }


# ═══════════════════════════════════════════════════════════════════
# CTA analytics — unwind risk, reconstructed P&L, trend-chase beta
# ═══════════════════════════════════════════════════════════════════

def _unwind_row(spec: ContractSpec, vol_pctile: dict[str, float] | None) -> dict | None:
    h = contract_history(spec.code, lookback_weeks=260)
    if h.empty:
        return None
    last = h.iloc[-1]
    pctile = last.get("spec_pctile_3y")
    if pd.isna(pctile):
        return None
    extremity = abs(float(pctile) - 0.5) * 2.0
    vol_p = (vol_pctile or {}).get(spec.symbol, 0.5)
    unwind_score = round(float(extremity * max(vol_p, 0.5)), 3)
    direction = "long" if float(pctile) > 0.5 else "short"
    return {
        "code": spec.code,
        "symbol": spec.symbol,
        "name": spec.name,
        "asset_class": spec.asset_class,
        "pctile_3y": round(float(pctile), 3),
        "vol_pctile": round(float(vol_p), 3),
        "unwind_score": unwind_score,
        "direction": direction,
        "extremity": round(extremity, 3),
    }


@_result_cached("cta_unwind_risk")
def cta_unwind_risk() -> list[dict]:
    """Unwind-risk score = positioning_extreme × realized_vol_regime.
    Fetches realized-vol percentiles internally (cta_model is cached) so
    the cache key stays clean. Parallel fan-out across contracts."""
    try:
        from src.cta_model import all_vol_percentiles
        vol_pctile = all_vol_percentiles()
    except Exception:
        vol_pctile = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [r for r in pool.map(lambda s: _unwind_row(s, vol_pctile), CONTRACTS) if r is not None]
    rows.sort(key=lambda r: -r["unwind_score"])
    return rows


# ═══════════════════════════════════════════════════════════════════
# Flow radar — biggest weekly changes
# ═══════════════════════════════════════════════════════════════════

def _flow_row(spec: ContractSpec, min_abs_chg_pct: float) -> dict | None:
    h = contract_history(spec.code, lookback_weeks=260)
    if h.empty or len(h) < 2:
        return None
    last = h.iloc[-1]
    oi = float(last["oi"]) if pd.notna(last["oi"]) and last["oi"] > 0 else np.nan
    chg_1w = float(last["spec_chg_1w"]) if pd.notna(last["spec_chg_1w"]) else np.nan
    if pd.isna(oi) or pd.isna(chg_1w):
        return None
    chg_pct_oi = (chg_1w / oi) * 100
    if abs(chg_pct_oi) < min_abs_chg_pct / 100 and abs(chg_pct_oi) < 3.0:
        return None
    return {
        "code": spec.code,
        "symbol": spec.symbol,
        "name": spec.name,
        "asset_class": spec.asset_class,
        "date": last["date"].strftime("%Y-%m-%d"),
        "chg_1w": int(chg_1w),
        "chg_1w_pct_oi": round(float(chg_pct_oi), 2),
        "chg_4w": int(last["spec_chg_4w"]) if pd.notna(last["spec_chg_4w"]) else None,
        "conc_lt4_chg_4w": round(float(last["conc_lt4_chg_4w"]), 2) if pd.notna(last["conc_lt4_chg_4w"]) else None,
        "spec_net": int(last["spec_net"]),
        "pctile_3y": round(float(last["spec_pctile_3y"]), 3) if pd.notna(last["spec_pctile_3y"]) else None,
    }


@_result_cached("flow_radar")
def flow_radar(min_abs_chg_pct: float = 10.0) -> list[dict]:
    """Biggest WoW position changes, parallel fan-out."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [r for r in pool.map(lambda s: _flow_row(s, min_abs_chg_pct), CONTRACTS) if r is not None]
    rows.sort(key=lambda r: -abs(r["chg_1w_pct_oi"]))
    return rows


# ═══════════════════════════════════════════════════════════════════
# Public aggregator for the landing view
# ═══════════════════════════════════════════════════════════════════

@_result_cached("positioning_dashboard")
def positioning_dashboard() -> dict:
    """Bundle of everything the landing tab needs in one payload."""
    return {
        "regime": regime_composites(),
        "heatmap": heatmap_snapshot(),
        "divergence_top": divergence_scan(min_abs_z=0.5)[:12],
        "flow_radar_top": flow_radar()[:15],
        "cta_unwind_top": cta_unwind_risk()[:10],
    }
