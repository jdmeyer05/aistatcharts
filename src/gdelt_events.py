"""GDELT Events bulk download & processing for Iran conflict monitoring.

Downloads daily event CSV files from data.gdeltproject.org/events/,
filters to Iran-region conflict events, and caches locally.
No API rate limits — direct file downloads.
"""

import os
import io
import zipfile
import logging
import urllib.request
from datetime import date, timedelta

import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "gdelt_events")
PROCESSED_FILE = os.path.join(DATA_DIR, "iran_conflict_events.parquet")

GDELT_EVENTS_BASE = "http://data.gdeltproject.org/events/"

GDELT_COLUMNS = [
    "GLOBALEVENTID", "SQLDATE", "MonthYear", "Year", "FractionDate",
    "Actor1Code", "Actor1Name", "Actor1CountryCode", "Actor1KnownGroupCode",
    "Actor1EthnicCode", "Actor1Religion1Code", "Actor1Religion2Code",
    "Actor1Type1Code", "Actor1Type2Code", "Actor1Type3Code",
    "Actor2Code", "Actor2Name", "Actor2CountryCode", "Actor2KnownGroupCode",
    "Actor2EthnicCode", "Actor2Religion1Code", "Actor2Religion2Code",
    "Actor2Type1Code", "Actor2Type2Code", "Actor2Type3Code",
    "IsRootEvent", "EventCode", "EventBaseCode", "EventRootCode",
    "QuadClass", "GoldsteinScale", "NumMentions", "NumSources",
    "NumArticles", "AvgTone",
    "Actor1Geo_Type", "Actor1Geo_FullName", "Actor1Geo_CountryCode",
    "Actor1Geo_ADM1Code", "Actor1Geo_Lat", "Actor1Geo_Long",
    "Actor1Geo_FeatureID",
    "Actor2Geo_Type", "Actor2Geo_FullName", "Actor2Geo_CountryCode",
    "Actor2Geo_ADM1Code", "Actor2Geo_Lat", "Actor2Geo_Long",
    "Actor2Geo_FeatureID",
    "ActionGeo_Type", "ActionGeo_FullName", "ActionGeo_CountryCode",
    "ActionGeo_ADM1Code", "ActionGeo_Lat", "ActionGeo_Long",
    "ActionGeo_FeatureID", "DATEADDED", "SOURCEURL",
]

# Actor country codes are 3-letter (CAMEO/FIPS): IRN, IRQ, ISR, SAU, etc.
ACTOR_COUNTRY_CODES = {"IRN", "IRQ", "ISR", "SAU", "YEM", "SYR", "LBN", "ARE", "QAT", "KWT", "BHR"}

# ActionGeo country codes are 2-letter FIPS: IR, IZ, IS, SA, YM, SY, LE, AE, QA, KU, BA
GEO_COUNTRY_CODES = {"IR", "IZ", "IS", "SA", "YM", "SY", "LE", "AE", "QA", "KU", "BA"}

# CAMEO root codes: 14=protest, 17=coerce, 18=assault, 19=fight, 20=use military force
CONFLICT_ROOT_CODES = {"14", "17", "18", "19", "20"}

# All QuadClass 3 (verbal conflict) and 4 (material conflict) events
CONFLICT_QUAD_CLASSES = {"3", "4"}

# Map both 3-letter actor codes and 2-letter FIPS geo codes to readable names
COUNTRY_NAME_MAP = {
    "IRN": "Iran", "IR": "Iran",
    "IRQ": "Iraq", "IZ": "Iraq",
    "ISR": "Israel", "IS": "Israel",
    "SAU": "Saudi Arabia", "SA": "Saudi Arabia",
    "YEM": "Yemen", "YM": "Yemen",
    "SYR": "Syria", "SY": "Syria",
    "LBN": "Lebanon", "LE": "Lebanon",
    "ARE": "UAE", "AE": "UAE",
    "QAT": "Qatar", "QA": "Qatar",
    "KWT": "Kuwait", "KU": "Kuwait",
    "BHR": "Bahrain", "BA": "Bahrain",
}

CAMEO_DESCRIPTIONS = {
    "14": "Protest",
    "17": "Coerce",
    "18": "Assault",
    "19": "Fight",
    "20": "Military Force",
}


def _download_day(dt: date) -> pd.DataFrame | None:
    """Download and parse a single day's GDELT events file."""
    filename = f"{dt.strftime('%Y%m%d')}.export.CSV.zip"
    url = GDELT_EVENTS_BASE + filename
    try:
        response = urllib.request.urlopen(url, timeout=30)
        data = response.read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            csv_name = z.namelist()[0]
            with z.open(csv_name) as f:
                df = pd.read_csv(
                    f, sep="\t", header=None,
                    names=GDELT_COLUMNS[:58],
                    dtype=str, on_bad_lines="skip",
                )
        return df
    except Exception as e:
        logger.warning(f"GDELT download failed for {dt}: {e}")
        return None


def _filter_iran_conflict(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to Iran-region conflict events."""
    # Country filter: actor codes use 3-letter, geo codes use 2-letter FIPS
    country_mask = (
        df["Actor1CountryCode"].isin(ACTOR_COUNTRY_CODES) |
        df["Actor2CountryCode"].isin(ACTOR_COUNTRY_CODES) |
        df["ActionGeo_CountryCode"].isin(GEO_COUNTRY_CODES)
    )

    # Conflict filter: material conflict events or specific CAMEO codes
    conflict_mask = (
        df["EventRootCode"].isin(CONFLICT_ROOT_CODES) |
        df["QuadClass"].isin(CONFLICT_QUAD_CLASSES)
    )

    filtered = df[country_mask & conflict_mask].copy()

    # Clean up numeric columns
    for col in ["GoldsteinScale", "NumMentions", "NumSources", "NumArticles", "AvgTone",
                "ActionGeo_Lat", "ActionGeo_Long"]:
        filtered[col] = pd.to_numeric(filtered[col], errors="coerce")

    filtered["SQLDATE"] = pd.to_datetime(filtered["SQLDATE"], format="%Y%m%d", errors="coerce")

    # Add readable columns
    filtered["EventType"] = filtered["EventRootCode"].map(CAMEO_DESCRIPTIONS).fillna("Other Conflict")
    filtered["ActionCountry"] = filtered["ActionGeo_CountryCode"].map(COUNTRY_NAME_MAP).fillna(
        filtered["ActionGeo_CountryCode"])

    return filtered


@st.cache_data(ttl=7200, show_spinner=False)
def fetch_gdelt_bulk_events(days: int = 21) -> pd.DataFrame:
    """Download and process the last N days of GDELT events for the Iran conflict.

    Returns a DataFrame with filtered conflict events, cached for 2 hours.
    Data is also saved to parquet for fast reloads.
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    # Check if we have a recent cached file
    if os.path.exists(PROCESSED_FILE):
        try:
            cached = pd.read_parquet(PROCESSED_FILE)
            if not cached.empty and "SQLDATE" in cached.columns:
                latest = pd.to_datetime(cached["SQLDATE"]).max()
                if latest and (pd.Timestamp.now() - latest).days <= 1:
                    logger.info(f"Using cached GDELT data ({len(cached)} events, latest: {latest})")
                    return cached
        except Exception as e:
            logger.warning(f"Failed to read cached GDELT data: {e}")

    # Download day by day
    all_frames = []
    today = date.today()
    start = today - timedelta(days=days)

    for i in range(days + 1):
        dt = start + timedelta(days=i)
        if dt > today:
            break
        raw = _download_day(dt)
        if raw is not None:
            filtered = _filter_iran_conflict(raw)
            if not filtered.empty:
                all_frames.append(filtered)
                logger.info(f"GDELT {dt}: {len(filtered)} conflict events")

    if not all_frames:
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.sort_values("SQLDATE", ascending=False)

    # Remove exact duplicates by event ID
    combined = combined.drop_duplicates(subset=["GLOBALEVENTID"], keep="first")

    # Save to parquet for fast reloads
    try:
        combined.to_parquet(PROCESSED_FILE, index=False)
    except Exception as e:
        logger.warning(f"Failed to cache GDELT data: {e}")

    return combined


def summarize_gdelt_events(df: pd.DataFrame) -> dict:
    """Build a summary dict for display and AI prompt injection."""
    if df.empty:
        return {}

    summary = {
        "total_events": len(df),
        "date_range": {
            "start": df["SQLDATE"].min().strftime("%Y-%m-%d") if pd.notna(df["SQLDATE"].min()) else "?",
            "end": df["SQLDATE"].max().strftime("%Y-%m-%d") if pd.notna(df["SQLDATE"].max()) else "?",
        },
    }

    # Events by type
    summary["by_type"] = df["EventType"].value_counts().to_dict()

    # Events by country
    summary["by_country"] = df["ActionCountry"].value_counts().head(10).to_dict()

    # Daily counts
    daily = df.groupby(df["SQLDATE"].dt.date).size()
    summary["daily_avg"] = round(daily.mean(), 1) if len(daily) > 0 else 0
    summary["daily_peak"] = int(daily.max()) if len(daily) > 0 else 0
    summary["peak_date"] = str(daily.idxmax()) if len(daily) > 0 else "?"

    # Tone (sentiment) — more negative = more hostile
    summary["avg_tone"] = round(df["AvgTone"].mean(), 2) if "AvgTone" in df.columns else 0
    summary["tone_7d"] = round(
        df[df["SQLDATE"] >= (pd.Timestamp.now() - pd.Timedelta(days=7))]["AvgTone"].mean(), 2
    ) if len(df) > 0 else 0

    # Goldstein scale — more negative = more conflictual
    summary["avg_goldstein"] = round(df["GoldsteinScale"].mean(), 2) if "GoldsteinScale" in df.columns else 0

    # Total media mentions
    summary["total_mentions"] = int(df["NumMentions"].sum()) if "NumMentions" in df.columns else 0

    # Last 7 days detail
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=7)
    recent = df[df["SQLDATE"] >= cutoff]
    summary["last_7d"] = {
        "events": len(recent),
        "by_type": recent["EventType"].value_counts().to_dict() if not recent.empty else {},
        "by_country": recent["ActionCountry"].value_counts().head(5).to_dict() if not recent.empty else {},
        "mentions": int(recent["NumMentions"].sum()) if not recent.empty else 0,
    }

    return summary


def build_gdelt_ai_context(df: pd.DataFrame) -> str:
    """Build a text block for AI prompt injection from GDELT bulk data."""
    s = summarize_gdelt_events(df)
    if not s:
        return ""

    lines = ["\n--- GDELT Bulk Event Data (direct download, not API) ---"]
    lines.append(f"  Period: {s['date_range']['start']} to {s['date_range']['end']}")
    lines.append(f"  Total conflict events: {s['total_events']:,} ({s['daily_avg']}/day avg, peak {s['daily_peak']} on {s['peak_date']})")
    lines.append(f"  Avg tone: {s['avg_tone']} (negative=hostile), 7d avg: {s['tone_7d']}")
    lines.append(f"  Avg Goldstein: {s['avg_goldstein']} (negative=conflictual)")
    lines.append(f"  Total media mentions: {s['total_mentions']:,}")

    lines.append(f"  By event type: {', '.join(f'{k}={v}' for k, v in s['by_type'].items())}")
    lines.append(f"  By country: {', '.join(f'{k}={v}' for k, v in s['by_country'].items())}")

    last7 = s.get("last_7d", {})
    if last7.get("events"):
        lines.append(f"  Last 7 days: {last7['events']} events, {last7['mentions']:,} mentions")
        lines.append(f"    Types: {', '.join(f'{k}={v}' for k, v in last7['by_type'].items())}")
        lines.append(f"    Countries: {', '.join(f'{k}={v}' for k, v in last7['by_country'].items())}")

    lines.append("  (DATA FRESHNESS: GDELT bulk files updated daily, cached locally)")

    return "\n".join(lines)
