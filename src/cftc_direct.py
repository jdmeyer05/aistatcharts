"""CFTC direct-file fallback.

When the Socrata API (publicreporting.cftc.gov) is down — which it is on
2026-04-18 — we fall back to CFTC's own weekly file distribution at
cftc.gov/files/dea/history/. Same underlying data; different schema.

Strategy: pull annual ZIP archives for the last 3 years per report, parse
once, cache as in-memory DataFrames. Enough history for rolling 3Y percentile
windows. Historical years are fetched lazily only if data older than we have
is requested.

URLs:
  - Disaggregated:  https://www.cftc.gov/files/dea/history/fut_disagg_txt_{y}.zip → f_year.txt
  - TFF:            https://www.cftc.gov/files/dea/history/fut_fin_txt_{y}.zip    → FinFutYY.txt
  - Legacy:         https://www.cftc.gov/files/dea/history/deacot{y}.zip           → annual.txt

Column-name conventions differ across reports and (for legacy) contain
spaces + parentheses. Each loader normalizes into a common internal schema
matching the Socrata-path shape in src/cftc.py so `_normalize()` there can
consume both uniformly.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

ReportType = Literal["disaggregated", "tff", "legacy"]

# ── URLs ─────────────────────────────────────────────────────────────────
_ANNUAL_URL = {
    "disaggregated": "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip",
    "tff":           "https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip",
    "legacy":        "https://www.cftc.gov/files/dea/history/deacot{year}.zip",
}
_ZIP_MEMBER = {
    "disaggregated": "f_year.txt",
    "tff":           "FinFutYY.txt",
    "legacy":        "annual.txt",
}

# ── Column maps: direct-file → internal schema ──────────────────────────
# Keep keys in sync with the shape produced by src/cftc.py:_normalize().
_DISAGG_COLS = {
    "date":            "Report_Date_as_YYYY-MM-DD",
    "contract_name":   "Market_and_Exchange_Names",
    "contract_code":   "CFTC_Contract_Market_Code",
    "oi":              "Open_Interest_All",
    "spec_long":       "M_Money_Positions_Long_All",
    "spec_short":      "M_Money_Positions_Short_All",
    "spec_spread":     "M_Money_Positions_Spread_All",
    "comm_long":       "Prod_Merc_Positions_Long_All",
    "comm_short":      "Prod_Merc_Positions_Short_All",
    "swap_long":       "Swap_Positions_Long_All",
    "swap_short":      "Swap__Positions_Short_All",
    "other_long":      "Other_Rept_Positions_Long_All",
    "other_short":     "Other_Rept_Positions_Short_All",
    "conc_gross_lt4":  "Conc_Gross_LE_4_TDR_Long_All",
    "conc_gross_st4":  "Conc_Gross_LE_4_TDR_Short_All",
    "conc_gross_lt8":  "Conc_Gross_LE_8_TDR_Long_All",
    "conc_gross_st8":  "Conc_Gross_LE_8_TDR_Short_All",
    "traders_total":   "Traders_Tot_All",
    "spec_n_traders_long":  "Traders_M_Money_Long_All",
    "spec_n_traders_short": "Traders_M_Money_Short_All",
}

_TFF_COLS = {
    "date":            "Report_Date_as_YYYY-MM-DD",
    "contract_name":   "Market_and_Exchange_Names",
    "contract_code":   "CFTC_Contract_Market_Code",
    "oi":              "Open_Interest_All",
    "spec_long":       "Lev_Money_Positions_Long_All",
    "spec_short":      "Lev_Money_Positions_Short_All",
    "spec_spread":     "Lev_Money_Positions_Spread_All",
    "asset_mgr_long":  "Asset_Mgr_Positions_Long_All",
    "asset_mgr_short": "Asset_Mgr_Positions_Short_All",
    "dealer_long":     "Dealer_Positions_Long_All",
    "dealer_short":    "Dealer_Positions_Short_All",
    "other_long":      "Other_Rept_Positions_Long_All",
    "other_short":     "Other_Rept_Positions_Short_All",
    "conc_gross_lt4":  "Conc_Gross_LE_4_TDR_Long_All",
    "conc_gross_st4":  "Conc_Gross_LE_4_TDR_Short_All",
    "conc_gross_lt8":  "Conc_Gross_LE_8_TDR_Long_All",
    "conc_gross_st8":  "Conc_Gross_LE_8_TDR_Short_All",
    "traders_total":   "Traders_Tot_All",
    "spec_n_traders_long":  "Traders_Lev_Money_Long_All",
    "spec_n_traders_short": "Traders_Lev_Money_Short_All",
}

# Legacy uses SPACES and PARENTHESES in column names. Shocking but true.
_LEGACY_COLS = {
    "date":            "As of Date in Form YYYY-MM-DD",
    "contract_name":   "Market and Exchange Names",
    "contract_code":   "CFTC Contract Market Code",
    "oi":              "Open Interest (All)",
    "spec_long":       "Noncommercial Positions-Long (All)",
    "spec_short":      "Noncommercial Positions-Short (All)",
    "spec_spread":     "Noncommercial Positions-Spreading (All)",
    "comm_long":       "Commercial Positions-Long (All)",
    "comm_short":      "Commercial Positions-Short (All)",
    "nonrept_long":    "Nonreportable Positions-Long (All)",
    "nonrept_short":   "Nonreportable Positions-Short (All)",
}

_COLS_BY_REPORT = {
    "disaggregated": _DISAGG_COLS,
    "tff":           _TFF_COLS,
    "legacy":        _LEGACY_COLS,
}


# ── Year cache: parsed DataFrame per (report, year) ──────────────────────
# DataFrames are NOT filtered by contract. Filter happens at query time.
_YEAR_CACHE: dict[tuple[str, int], pd.DataFrame] = {}
_MISSING: set[tuple[str, int]] = set()


def _tracked_codes() -> set[str]:
    """Contract codes we actually care about — filter to these to keep memory
    in check. Imported lazily so cftc_direct stays a pure data module."""
    try:
        from src.cftc import CONTRACTS_BY_CODE
        return set(CONTRACTS_BY_CODE.keys())
    except Exception:
        return set()


def _fetch_annual_dataframe(report: ReportType, year: int) -> pd.DataFrame:
    """Download + parse one annual archive. Cached in _YEAR_CACHE."""
    key = (report, year)
    if key in _YEAR_CACHE:
        return _YEAR_CACHE[key]
    if key in _MISSING:
        return pd.DataFrame()

    url = _ANNUAL_URL[report].format(year=year)
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"CFTC direct fetch failed {url}: {e}")
        _MISSING.add(key)
        return pd.DataFrame()

    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            member = _ZIP_MEMBER[report]
            # Some years the file inside the ZIP has a different casing — pick
            # the first .txt member if our expected name isn't there.
            names = z.namelist()
            if member not in names:
                candidates = [n for n in names if n.endswith(".txt")]
                if not candidates:
                    _MISSING.add(key)
                    return pd.DataFrame()
                member = candidates[0]
            with z.open(member) as f:
                col_map = _COLS_BY_REPORT[report]
                # Only read the columns we actually consume — saves a ton of RAM
                # (disagg has 191 columns, we want ~20).
                usecols = list(col_map.values())
                # Legacy file has a leading-space column name (" Total Reportable...").
                # Strip all column names after read.
                df = pd.read_csv(f, usecols=lambda c: c.strip() in [x.strip() for x in usecols],
                                 low_memory=False, na_values=[".", ""])
        df.columns = [c.strip() for c in df.columns]
    except Exception as e:
        logger.warning(f"CFTC direct parse failed {url}: {e}")
        _MISSING.add(key)
        return pd.DataFrame()

    if df.empty:
        _MISSING.add(key)
        return df

    # Rename direct-file columns → internal names
    rename = {v.strip(): k for k, v in _COLS_BY_REPORT[report].items()}
    df = df.rename(columns=rename)

    # Normalize contract code: strip whitespace, zero-pad is already done by CFTC
    if "contract_code" in df.columns:
        df["contract_code"] = df["contract_code"].astype(str).str.strip()

    # Filter down to the contracts we actually track before doing any further
    # work. Annual archives contain ~260 contracts × 52 weeks; we only care
    # about 45. This cuts memory ~6x with no downstream impact.
    tracked = _tracked_codes()
    if tracked and "contract_code" in df.columns:
        df = df[df["contract_code"].isin(tracked)].reset_index(drop=True)

    # Parse date
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    # Numeric coerce — use int32 where possible to halve memory
    numeric_cols = [c for c in df.columns if c not in ("date", "contract_name", "contract_code")]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")

    _YEAR_CACHE[key] = df
    logger.info(f"CFTC direct loaded {report} {year}: {len(df)} rows (filtered)")
    return df


def fetch_history(report: ReportType, contract_code: str, years: int = 5) -> pd.DataFrame:
    """Pull {years} years of history for one contract from direct files.
    Returns a DataFrame with the same internal schema that src/cftc.py expects.
    """
    now_year = datetime.utcnow().year
    frames = []
    for y in range(now_year - years + 1, now_year + 1):
        df = _fetch_annual_dataframe(report, y)
        if df.empty:
            continue
        match = df[df["contract_code"] == contract_code]
        if not match.empty:
            frames.append(match)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)

    # Add derived columns that _normalize() in src/cftc.py would have added.
    if {"spec_long", "spec_short"}.issubset(out.columns):
        out["spec_net"] = out["spec_long"] - out["spec_short"]
        out["spec_gross"] = out["spec_long"] + out["spec_short"]
        out["spec_pct_oi"] = np.where(out["oi"] > 0, out["spec_net"] / out["oi"] * 100, np.nan)
    if {"comm_long", "comm_short"}.issubset(out.columns):
        out["comm_net"] = out["comm_long"] - out["comm_short"]
        out["comm_pct_oi"] = np.where(out["oi"] > 0, out["comm_net"] / out["oi"] * 100, np.nan)
    if {"spec_n_traders_long", "spec_n_traders_short"}.issubset(out.columns):
        out["spec_n_traders"] = out["spec_n_traders_long"] + out["spec_n_traders_short"]

    # Ensure the columns src/cftc.py's contract_history() slices always exist
    # so downstream DataFrame slicing doesn't KeyError when a field is missing
    # from a particular year/contract (e.g., legacy has no n_traders breakout).
    expected = [
        "date", "oi", "spec_long", "spec_short", "spec_spread",
        "spec_net", "spec_gross", "spec_pct_oi",
        "conc_gross_lt4", "conc_gross_lt8",
        "spec_n_traders_long", "spec_n_traders_short", "spec_n_traders",
        "comm_long", "comm_short", "comm_net", "comm_pct_oi",
    ]
    for col in expected:
        if col not in out.columns:
            out[col] = np.nan

    return out
