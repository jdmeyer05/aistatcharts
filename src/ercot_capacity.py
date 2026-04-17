"""ERCOT capacity-pipeline data: discovers and parses the monthly Excel files
that ERCOT publishes at https://www.ercot.com/files/docs/{yyyy}/{mm}/{dd}/
Capacity-Changes-by-Fuel-Type-Charts_{Month_Year}[_PlannedMonthly].xlsx

Used by both the Streamlit page and the FastAPI backend for the Next.js site.
"""

import calendar
import io
import logging
import time
from datetime import date
from typing import Any

import pandas as pd
import requests

_log = logging.getLogger(__name__)

ERCOT_FILE_BASE = "https://www.ercot.com/files/docs"

# ERCOT's Excel sheets use positional columns; map the relevant indices.
_COL_MAP = {
    8: "inr",
    9: "project_name",
    10: "county",
    11: "projected_cod",
    12: "ia_signed",
    13: "fuel",
    14: "technology",
    15: "capacity_mw",
    16: "year",
    17: "financial_security",
}

_SHEET_FUEL_MAP = {
    "Wind Chart": "Wind",
    "Solar Chart": "Solar",
    "Battery Chart": "Battery",
    "Gas-Combined Cycle Chart": "Gas",
    "Gas-Other Chart": "Gas",
}

_SHEET_DETAIL_MAP = {
    "Gas-Combined Cycle Chart": "Gas-CC",
    "Gas-Other Chart": "Gas-CT/Other",
}

# Simple module-level cache: {cache_key: (expires_at, value)}
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl_s: int, loader):
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    val = loader()
    _cache[key] = (now + ttl_s, val)
    return val


def discover_months(lookback: int = 12) -> list[dict]:
    """Probe ERCOT's file server to find published capacity reports.

    ERCOT publishes around the 3rd-10th of each month; the report covers the
    previous month. Returns a list of {date_path, month_label} dicts, newest
    first. Cached 24h.
    """
    def _do() -> list[dict]:
        today = date.today()
        found: list[dict] = []
        seen: set[str] = set()  # dedupe by month_label
        for months_ago in range(0, lookback + 1):
            # Walk back one calendar month at a time — avoids the 30-day drift
            # that caused the original Streamlit version to skip months and emit
            # duplicates near year boundaries.
            pub_month = today.month - months_ago
            pub_year = today.year
            while pub_month <= 0:
                pub_month += 12
                pub_year -= 1
            if pub_month == 1:
                report_month, report_year = 12, pub_year - 1
            else:
                report_month, report_year = pub_month - 1, pub_year
            month_label = f"{calendar.month_name[report_month]}_{report_year}"
            if month_label in seen:
                continue
            for day in range(3, 11):
                date_path = f"{pub_year}/{pub_month:02d}/{day:02d}"
                url = f"{ERCOT_FILE_BASE}/{date_path}/Capacity-Changes-by-Fuel-Type-Charts_{month_label}.xlsx"
                try:
                    r = requests.head(url, timeout=5, allow_redirects=True)
                    if r.status_code == 200:
                        found.append({"date_path": date_path, "month_label": month_label})
                        seen.add(month_label)
                        break
                except Exception:
                    continue
        return found

    return _cached("months", ttl_s=86400, loader=_do)


def fetch_capacity_file(date_path: str, month_label: str, planned_only: bool = False) -> list[dict]:
    """Download and parse a single ERCOT capacity Excel file into a list of
    project dicts suitable for JSON serialization. Returns [] on failure.
    Cached 1h per (date_path, month_label, planned_only) combo.
    """
    key = f"file:{date_path}:{month_label}:{planned_only}"

    def _do() -> list[dict]:
        suffix = "_PlannedMonthly" if planned_only else ""
        url = f"{ERCOT_FILE_BASE}/{date_path}/Capacity-Changes-by-Fuel-Type-Charts_{month_label}{suffix}.xlsx"
        try:
            r = requests.get(url, timeout=30)
            if r.status_code != 200:
                return []

            xls = pd.ExcelFile(io.BytesIO(r.content))
            frames: list[pd.DataFrame] = []
            for sheet in xls.sheet_names:
                fuel_type = _SHEET_FUEL_MAP.get(sheet)
                if not fuel_type:
                    continue
                df = pd.read_excel(xls, sheet_name=sheet, header=None)
                keep_cols = [c for c in _COL_MAP if c in df.columns]
                if not keep_cols:
                    continue
                df = df[keep_cols].copy()
                df.columns = [_COL_MAP[c] for c in keep_cols]
                df = df.iloc[1:]  # drop header row
                df = df.dropna(subset=["project_name"])
                df["capacity_mw"] = pd.to_numeric(df["capacity_mw"], errors="coerce")
                df["projected_cod"] = pd.to_datetime(df["projected_cod"], errors="coerce")
                df["ia_signed"] = pd.to_datetime(df["ia_signed"], errors="coerce")
                df["year"] = pd.to_numeric(df["year"], errors="coerce")
                df["fuel_type"] = fuel_type
                df["fuel_detail"] = _SHEET_DETAIL_MAP.get(sheet, fuel_type)
                frames.append(df)

            if not frames:
                return []
            combined = pd.concat(frames, ignore_index=True).dropna(subset=["capacity_mw"])

            # Serialize to plain dicts with ISO date strings and nullable fields.
            out: list[dict] = []
            for row in combined.to_dict(orient="records"):
                out.append({
                    "inr": str(row.get("inr") or ""),
                    "project_name": str(row.get("project_name") or ""),
                    "county": str(row.get("county") or ""),
                    "projected_cod": row["projected_cod"].date().isoformat() if pd.notna(row.get("projected_cod")) else None,
                    "ia_signed": row["ia_signed"].date().isoformat() if pd.notna(row.get("ia_signed")) else None,
                    "fuel_type": row.get("fuel_type") or "Other",
                    "fuel_detail": row.get("fuel_detail") or row.get("fuel_type") or "Other",
                    "technology": str(row.get("technology") or ""),
                    "capacity_mw": float(row["capacity_mw"]) if pd.notna(row.get("capacity_mw")) else 0.0,
                    "year": int(row["year"]) if pd.notna(row.get("year")) else None,
                    "financial_security": str(row.get("financial_security") or ""),
                })
            return out
        except Exception as e:
            _log.error("Failed to fetch ERCOT capacity file %s: %s", month_label, e)
            return []

    return _cached(key, ttl_s=3600, loader=_do)
