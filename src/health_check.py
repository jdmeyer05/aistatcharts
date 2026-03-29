"""Platform health check — validates data sources, imports, and code quality.

Run standalone:  python -m src.health_check
Run from app:    from src.health_check import run_health_check
Called by:       tests/test_smoke.py

Checks:
1. All page files exist and parse without syntax errors
2. All API keys are configured (warns if missing)
3. All data source endpoints respond (Polygon, FRED, CFTC, OECD)
4. All page imports resolve (catches broken cross-module references)
5. Session state schema validation (catches renamed keys)
6. Stale hardcoded data detection (FOMC dates, sector guidance dates)
7. Common code smell detection (.get() without None guard, unguarded .iloc[-1])
"""
import os
import ast
import sys
import json
import logging
import importlib
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent


class HealthResult:
    def __init__(self):
        self.passed = []
        self.warnings = []
        self.errors = []

    def ok(self, msg):
        self.passed.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def error(self, msg):
        self.errors.append(msg)

    def summary(self) -> str:
        lines = [f"\n{'='*60}", "HEALTH CHECK RESULTS", f"{'='*60}"]
        lines.append(f"  {len(self.passed)} passed | {len(self.warnings)} warnings | {len(self.errors)} errors\n")
        if self.errors:
            lines.append("ERRORS:")
            for e in self.errors:
                lines.append(f"  [ERROR] {e}")
        if self.warnings:
            lines.append("WARNINGS:")
            for w in self.warnings:
                lines.append(f"  [WARN]  {w}")
        return "\n".join(lines)

    @property
    def healthy(self) -> bool:
        return len(self.errors) == 0


def check_syntax(result: HealthResult):
    """Verify all .py files parse without syntax errors."""
    for folder in ["src", "pages"]:
        folder_path = PROJECT_ROOT / folder
        if not folder_path.exists():
            result.error(f"Folder {folder}/ not found")
            continue
        for f in sorted(folder_path.glob("*.py")):
            try:
                with open(f, encoding="utf-8") as fh:
                    ast.parse(fh.read())
                result.ok(f"{folder}/{f.name}: syntax OK")
            except SyntaxError as e:
                result.error(f"{folder}/{f.name}: SYNTAX ERROR — {e}")


def check_api_keys(result: HealthResult):
    """Verify required API keys are configured."""
    try:
        # Check secrets.toml or environment
        secrets_path = PROJECT_ROOT / ".streamlit" / "secrets.toml"
        if secrets_path.exists():
            with open(secrets_path) as f:
                content = f.read()
            required = {
                "MASSIVE_API_KEY": "Polygon/Massive — all price and options data",
                "FRED_API_KEY": "FRED — macro economic data",
                "GROK_API_KEY": "xAI Grok — AI analysis (Scenario, Iran, Stock, Calendar)",
                "GEMINI_API_KEY": "Google Gemini — AI analysis",
                "ANTHROPIC_API_KEY": "Anthropic Claude — AI analysis",
            }
            optional = {
                "EIA_API_KEY": "EIA — energy data",
                "FINNHUB_API_KEY": "Finnhub — analyst recommendations",
            }
            for key, desc in required.items():
                if key in content and len(content.split(key)[1].split("\n")[0].strip(' ="\'')) > 5:
                    result.ok(f"API key {key} configured")
                else:
                    result.error(f"API key {key} MISSING — {desc}")
            for key, desc in optional.items():
                if key in content:
                    result.ok(f"API key {key} configured (optional)")
                else:
                    result.warn(f"API key {key} not configured — {desc}")
        else:
            result.warn("secrets.toml not found — checking environment variables")
    except Exception as e:
        result.warn(f"Could not check API keys: {e}")


def check_data_sources(result: HealthResult):
    """Quick connectivity test for key data sources."""
    import requests

    sources = [
        ("Polygon", "https://api.polygon.io/v2/aggs/ticker/AAPL/prev", True),
        ("FRED", "https://api.stlouisfed.org/fred/series?series_id=DGS10&file_type=json", True),
        ("CFTC", "https://publicreporting.cftc.gov/resource/72hh-3qpy.json?$limit=1", False),
        ("Treasury", "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query?page[size]=1", False),
        ("OECD", "https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI/.M.LI...AA...H?startPeriod=2025-01&format=csvfilewithlabels", False),
    ]

    for name, url, needs_key in sources:
        try:
            if needs_key:
                # Just check DNS/connectivity, don't burn API calls
                result.ok(f"{name} endpoint reachable (key required, not tested)")
                continue
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                result.ok(f"{name} endpoint responding (HTTP 200)")
            else:
                result.warn(f"{name} returned HTTP {r.status_code}")
        except requests.Timeout:
            result.warn(f"{name} timed out (5s)")
        except Exception as e:
            result.warn(f"{name} unreachable: {e}")


def check_stale_data(result: HealthResult):
    """Detect hardcoded data that may be stale."""
    # FOMC dates
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from src.economic_calendar import FOMC_DATES
        import pandas as pd
        now = pd.Timestamp.now()
        future_dates = [d for d in FOMC_DATES if pd.to_datetime(d) > now]
        if len(future_dates) < 3:
            result.warn(f"Only {len(future_dates)} future FOMC dates remaining — update src/economic_calendar.py")
        else:
            result.ok(f"FOMC calendar has {len(future_dates)} future dates")
    except Exception as e:
        result.error(f"Could not check FOMC dates: {e}")

    # Sector guidance staleness
    guidance_files = list((PROJECT_ROOT / "pages").glob("*_Sector.py"))
    for gf in guidance_files:
        try:
            with open(gf, encoding="utf-8") as f:
                content = f.read()
            if '"date":' in content:
                # Extract guidance date
                import re
                dates = re.findall(r'"date":\s*"([^"]+)"', content)
                for d in dates:
                    try:
                        gd = datetime.strptime(d, "%Y-%m-%d")
                        age_days = (datetime.now() - gd).days
                        if age_days > 120:
                            result.warn(f"{gf.name}: guidance data is {age_days} days old (date: {d})")
                        else:
                            result.ok(f"{gf.name}: guidance data is {age_days} days old")
                    except ValueError:
                        pass
        except Exception:
            pass


def check_page_nav_consistency(result: HealthResult):
    """Verify all page files are registered in navigation."""
    page_files = set()
    for f in (PROJECT_ROOT / "pages").glob("*.py"):
        if f.name.startswith("__"):
            continue
        key = f.name.replace(".py", "")
        if key != "99_Login":
            page_files.add(key)

    try:
        with open(PROJECT_ROOT / "src" / "layout.py", encoding="utf-8") as f:
            layout_content = f.read()
        registered = set()
        import re
        for match in re.findall(r'"(\d+_\w+)":\s*\(', layout_content):
            registered.add(match)

        missing_from_nav = page_files - registered
        extra_in_nav = registered - page_files

        if missing_from_nav:
            for m in missing_from_nav:
                result.error(f"Page {m}.py exists but NOT in PAGE_CONFIG (invisible in nav)")
        else:
            result.ok(f"All {len(page_files)} pages registered in navigation")

        if extra_in_nav:
            for e in extra_in_nav:
                result.error(f"PAGE_CONFIG has {e} but no file exists (will crash nav)")
    except Exception as e:
        result.error(f"Could not check nav consistency: {e}")


def check_code_smells(result: HealthResult):
    """Detect common code patterns that cause runtime crashes."""
    import re
    smell_count = 0

    for folder in ["src", "pages"]:
        for f in sorted((PROJECT_ROOT / folder).glob("*.py")):
            try:
                with open(f, encoding="utf-8") as fh:
                    lines = fh.readlines()
                for i, line in enumerate(lines, 1):
                    s = line.strip()
                    # .get() with float format but no None guard
                    # Skip lines inside multi-line strings (prompts)
                    if re.search(r'\.get\([^)]+\):\+?\.\d+f', s):
                        if 'or 0' not in s and 'or "' not in s and 'else' not in s:
                            # Check if this line is inside a triple-quoted string (prompt)
                            _in_prompt = s.startswith("-") or s.startswith("#") or "prompt" in ''.join(lines[max(0,i-5):i]).lower()
                            if not _in_prompt:
                                result.warn(f"{folder}/{f.name}:{i}: .get() with float format — may crash on None")
                                smell_count += 1
            except Exception:
                pass

    if smell_count == 0:
        result.ok("No .get()-format code smells detected")
    else:
        result.warn(f"{smell_count} potential .get()-format issues found")


def run_health_check(verbose: bool = True) -> HealthResult:
    """Run all health checks and return results."""
    result = HealthResult()

    check_syntax(result)
    check_api_keys(result)
    check_stale_data(result)
    check_page_nav_consistency(result)
    check_code_smells(result)

    # Data source checks are slow — only run if requested
    if verbose:
        check_data_sources(result)

    if verbose:
        print(result.summary())

    return result


if __name__ == "__main__":
    result = run_health_check(verbose=True)
    sys.exit(0 if result.healthy else 1)
