"""AI / data center infrastructure — compute layer.

Two questions this module answers from primary EIA data, both free:

1. **Grid reality** — is electricity demand actually growing in the balancing
   authorities where data centers are being announced? EIA-930 daily demand,
   aggregated to monthly, trailing 12m vs prior 12m.
2. **Supply response** — is generation actually being added in those same
   footprints? EIA-860 operable-generator inventory, grouped by the month each
   unit entered service.

The gap between those two is the point. Announced load is not demand and a
queue position is not a generator; both series here are *realised*, which is
what makes them worth charting against the announcements.

Deliberate omission: EIA's v2 API exposes only the **operable** generator
inventory (statuses OP/OS/SB/OA). Planned units live in the EIA-860M
spreadsheet, not the API, so `capacity_additions` is backward-looking by
construction. Anything forward-looking has to come from elsewhere and must be
labelled as such.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections import defaultdict
from datetime import date, datetime, timedelta

import requests

logger = logging.getLogger(__name__)

EIA_V2 = "https://api.eia.gov/v2"
_PAGE = 5000
_MAX_ROWS = 60_000


# ─────────────────────────────────────────────────────────────────
# Balancing authority universe
#
# `dc_note` is EDITORIAL — it records why a BA is worth watching for data
# center load, not a measured quantity. It is surfaced to the UI as prose so
# nobody mistakes it for a statistic. There is no public per-BA data center
# load series; if there were, this page would not need to exist.
# ─────────────────────────────────────────────────────────────────

BALANCING_AUTHORITIES: dict[str, dict] = {
    "PJM":  {"name": "PJM Interconnection",        "region": "Mid-Atlantic",  "dc_note": "Northern Virginia — largest data center market globally"},
    "ERCO": {"name": "ERCOT",                       "region": "Texas",         "dc_note": "Dallas, San Antonio, Abilene; largest large-load queue in the US"},
    "MISO": {"name": "Midcontinent ISO",            "region": "Midwest",       "dc_note": "Iowa, Ohio and greater Chicago clusters"},
    "SWPP": {"name": "Southwest Power Pool",        "region": "Central",       "dc_note": None},
    "CISO": {"name": "California ISO",              "region": "West",          "dc_note": "Santa Clara legacy cluster; limited new build"},
    "SOCO": {"name": "Southern Company",            "region": "Southeast",     "dc_note": "Atlanta metro — among the fastest-growing US markets"},
    "TVA":  {"name": "Tennessee Valley Authority",  "region": "Southeast",     "dc_note": "Memphis; large single-site AI load"},
    "DUK":  {"name": "Duke Energy Carolinas",       "region": "Southeast",     "dc_note": "Carolinas corridor"},
    "CPLE": {"name": "Duke Energy Progress East",   "region": "Southeast",     "dc_note": "Carolinas corridor"},
    "FPL":  {"name": "Florida Power & Light",       "region": "Southeast",     "dc_note": None},
    "ISNE": {"name": "ISO New England",             "region": "Northeast",     "dc_note": None},
    "NYIS": {"name": "New York ISO",                "region": "Northeast",     "dc_note": None},
    "BPAT": {"name": "Bonneville Power",            "region": "Northwest",     "dc_note": "Hillsboro and Prineville, Oregon"},
    "AZPS": {"name": "Arizona Public Service",      "region": "Southwest",     "dc_note": "Phoenix metro"},
    "SRP":  {"name": "Salt River Project",          "region": "Southwest",     "dc_note": "Phoenix metro"},
    "NEVP": {"name": "Nevada Power",                "region": "Southwest",     "dc_note": "Las Vegas and Reno"},
    "PACE": {"name": "PacifiCorp East",             "region": "Mountain",      "dc_note": "Utah cluster"},
    "PSCO": {"name": "Public Service Colorado",     "region": "Mountain",      "dc_note": "Denver metro"},
    "LDWP": {"name": "LA Dept of Water & Power",    "region": "West",          "dc_note": None},
    "PNM":  {"name": "Public Service New Mexico",   "region": "Southwest",     "dc_note": "New Mexico build-out"},
}

DC_FLAGGED = [k for k, v in BALANCING_AUTHORITIES.items() if v["dc_note"]]


# ─────────────────────────────────────────────────────────────────
# EIA transport
# ─────────────────────────────────────────────────────────────────

def _eia_key() -> str | None:
    from src.api_keys import get_secret
    return get_secret("EIA_API_KEY")


def _eia_paged(path: str, params: dict, max_rows: int = _MAX_ROWS) -> list[dict]:
    """Page through an EIA v2 data endpoint until exhausted or `max_rows`.

    Raises on transport failure rather than returning a partial set — a short
    read here would silently understate demand growth, which is exactly the
    kind of quiet wrong answer this page exists to avoid.
    """
    key = _eia_key()
    if not key:
        raise RuntimeError("EIA_API_KEY not configured")

    out: list[dict] = []
    offset = 0
    while True:
        q = {"api_key": key, "length": _PAGE, "offset": offset, **params}
        url = f"{EIA_V2}/{path}?{urllib.parse.urlencode(q, doseq=True)}"
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        resp = r.json()["response"]
        rows = resp.get("data", [])
        out.extend(rows)
        total = int(resp.get("total") or 0)
        offset = len(out)
        if not rows or offset >= total:
            break
        if offset >= max_rows:
            # Never truncate quietly. A short read here understates demand or
            # capacity by an unknown amount, which is worse than an error.
            raise RuntimeError(
                f"EIA {path} returned {total} rows, above the {max_rows} cap — "
                "raise _MAX_ROWS or narrow the query rather than serving a partial set"
            )
    return out


def _f(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────
# 1. Grid reality — realised demand growth by balancing authority
# ─────────────────────────────────────────────────────────────────

def _month_key(period: str) -> str:
    return period[:7]


def _complete_months(today: date, count: int) -> list[str]:
    """The last `count` *complete* calendar months, oldest first.

    The current month is excluded. A partial month compared against a full
    prior-year month would understate growth by roughly the fraction of the
    month elapsed — a large error early in a month, and a silent one.
    """
    y, m = today.year, today.month
    months = []
    for _ in range(count):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        months.append(f"{y:04d}-{m:02d}")
    return list(reversed(months))


def grid_load_growth(months_back: int = 25) -> dict:
    """Trailing-12m vs prior-12m electricity demand growth per balancing authority.

    Returns realised demand only. Coverage is reported per BA so a gap in the
    EIA feed shows up as low confidence rather than a wrong growth number.
    """
    today = datetime.utcnow().date()
    start = (today - timedelta(days=int(months_back * 31))).strftime("%Y-%m-01")

    rows = _eia_paged(
        "electricity/rto/daily-region-data/data",
        {
            "frequency": "daily",
            "data[]": "value",
            "facets[respondent][]": list(BALANCING_AUTHORITIES),
            "facets[type][]": "D",
            "facets[timezone][]": "Eastern",
            "start": start,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
        },
    )

    # ba -> month -> [mwh_total, day_count]
    agg: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    for r in rows:
        v = _f(r.get("value"))
        if v is None:
            continue
        bucket = agg[r["respondent"]][_month_key(r["period"])]
        bucket[0] += v
        bucket[1] += 1

    window = _complete_months(today, 24)
    recent, prior = window[12:], window[:12]

    def days_in(mk: str) -> int:
        y, m = int(mk[:4]), int(mk[5:7])
        nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        return (nxt - date(y, m, 1)).days

    expected = sum(days_in(mk) for mk in window)

    results = []
    for ba, meta in BALANCING_AUTHORITIES.items():
        by_month = agg.get(ba, {})
        observed = sum(by_month.get(mk, [0.0, 0])[1] for mk in window)
        coverage = observed / expected if expected else 0.0

        recent_mwh = sum(by_month.get(mk, [0.0, 0])[0] for mk in recent)
        prior_mwh = sum(by_month.get(mk, [0.0, 0])[0] for mk in prior)

        # Growth is only meaningful with near-complete coverage on both legs.
        recent_days = sum(by_month.get(mk, [0.0, 0])[1] for mk in recent)
        prior_days = sum(by_month.get(mk, [0.0, 0])[1] for mk in prior)
        usable = recent_days > 350 and prior_days > 350 and prior_mwh > 0
        growth = ((recent_mwh / prior_mwh) - 1.0) * 100 if usable else None

        monthly = [
            {
                "month": mk,
                "twh": round(by_month.get(mk, [0.0, 0])[0] / 1e6, 4),
                "days": by_month.get(mk, [0.0, 0])[1],
            }
            for mk in window
        ]

        results.append({
            "ba": ba,
            "name": meta["name"],
            "region": meta["region"],
            "dc_note": meta["dc_note"],
            "dc_flagged": meta["dc_note"] is not None,
            "trailing_12m_twh": round(recent_mwh / 1e6, 3),
            "prior_12m_twh": round(prior_mwh / 1e6, 3),
            "growth_pct": round(growth, 2) if growth is not None else None,
            "delta_twh": round((recent_mwh - prior_mwh) / 1e6, 3) if usable else None,
            "coverage": round(coverage, 3),
            "monthly": monthly,
        })

    scored = [r for r in results if r["growth_pct"] is not None]
    flagged = [r for r in scored if r["dc_flagged"]]
    unflagged = [r for r in scored if not r["dc_flagged"]]

    # Demand-weighted, not a simple mean: a simple average would let a small BA
    # swing the aggregate as hard as PJM.
    def weighted(group: list[dict]) -> float | None:
        p = sum(g["prior_12m_twh"] for g in group)
        rr = sum(g["trailing_12m_twh"] for g in group)
        return round(((rr / p) - 1.0) * 100, 2) if p > 0 else None

    return {
        "window": {"recent": [recent[0], recent[-1]], "prior": [prior[0], prior[-1]]},
        "rows": sorted(scored, key=lambda r: r["growth_pct"], reverse=True),
        "excluded": [
            {"ba": r["ba"], "name": r["name"], "coverage": r["coverage"]}
            for r in results if r["growth_pct"] is None
        ],
        "aggregate": {
            "all": weighted(scored),
            "dc_flagged": weighted(flagged),
            "not_flagged": weighted(unflagged),
            "spread_pp": (
                round(weighted(flagged) - weighted(unflagged), 2)
                if weighted(flagged) is not None and weighted(unflagged) is not None
                else None
            ),
            "n_flagged": len(flagged),
            "n_not_flagged": len(unflagged),
        },
        "source": "EIA-930 daily region demand (Form EIA-930), retrieved live",
        "caveat": (
            "Realised metered demand, complete calendar months only. Data center load is "
            "not separately metered in EIA-930; the flag marks BAs where data center "
            "activity is publicly concentrated and is editorial, not measured."
        ),
    }


# ─────────────────────────────────────────────────────────────────
# 2. Supply response — realised generation additions
# ─────────────────────────────────────────────────────────────────

def _latest_inventory_period() -> str:
    """Newest monthly snapshot of the operable-generator inventory."""
    key = _eia_key()
    if not key:
        raise RuntimeError("EIA_API_KEY not configured")
    r = requests.get(
        f"{EIA_V2}/electricity/operating-generator-capacity?api_key={key}", timeout=30
    )
    r.raise_for_status()
    end = r.json()["response"].get("endPeriod")
    if not end:
        raise RuntimeError("EIA inventory metadata missing endPeriod")
    return end


def _tech_bucket(technology: str | None) -> str:
    """Map an EIA `technology` string to a display bucket.

    Match on stems, not whole words. EIA's label is "Batteries", so a
    `"battery" in t` test silently routed every storage unit — 45.7 GW, the
    largest single addition category in ERCOT and CAISO — into "Other".
    """
    t = (technology or "").lower()
    if "solar" in t:
        return "Solar"
    if "batter" in t or "storage" in t or "flywheel" in t:
        return "Storage"
    if "wind" in t:
        return "Wind"
    if "nuclear" in t:
        return "Nuclear"
    if "combined cycle" in t:
        return "Gas — combined cycle"
    if "combustion turbine" in t or "gas turbine" in t:
        return "Gas — peaker"
    if "natural gas" in t or "gas" in t:
        return "Gas — other"
    if "coal" in t:
        return "Coal"
    if "hydro" in t or "pumped" in t:
        return "Hydro"
    if "petroleum" in t or "diesel" in t:
        return "Oil"
    if "geothermal" in t:
        return "Geothermal"
    if "biomass" in t or "wood" in t or "waste" in t:
        return "Biomass & waste"
    return "Other"


def capacity_additions(years_back: int = 4, retire_years_ahead: int = 5) -> dict:
    """Realised generation additions by balancing authority and technology.

    Derived from the operable-generator inventory by bucketing each unit on the
    month it entered service, so it counts steel that is actually running.

    Survivorship: units retired before the snapshot are absent, which slightly
    understates historical additions. The effect is negligible for recent years
    and grows with age — which is why the default window is short.
    """
    # Pin to the newest monthly snapshot first. Without this the endpoint
    # returns every historical snapshot back to 2008 — ~28k rows per month,
    # tens of seconds of paging, all but the last month discarded.
    snapshot = _latest_inventory_period()

    latest = _eia_paged(
        "electricity/operating-generator-capacity/data",
        {
            "frequency": "monthly",
            "data[]": ["nameplate-capacity-mw", "net-summer-capacity-mw",
                       "operating-year-month", "planned-retirement-year-month"],
            "facets[balancing_authority_code][]": list(BALANCING_AUTHORITIES),
            "start": snapshot,
            "end": snapshot,
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
        },
    )
    if not latest:
        raise RuntimeError("EIA operable-generator inventory returned no rows")

    snapshot_year = int(snapshot[:4])
    cutoff_year = snapshot_year - years_back
    retire_horizon_year = snapshot_year + retire_years_ahead

    by_ba_year: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    by_tech_year: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    retirements: dict[str, float] = defaultdict(float)
    operating_total: dict[str, float] = defaultdict(float)

    for r in latest:
        ba = r.get("balancing_authority_code")
        if ba not in BALANCING_AUTHORITIES:
            continue
        mw = _f(r.get("nameplate-capacity-mw"))
        if mw is None:
            continue
        operating_total[ba] += mw

        online = r.get("operating-year-month")
        if online and len(online) >= 4:
            yr = int(online[:4])
            # Clamp to the snapshot year. A meaningful minority of rows carry an
            # `operating-year-month` in the future — hydro relicensing dates and
            # data-entry errors run out to 2049. Those are not realised
            # additions and counting them would invent capacity that does not
            # exist.
            if cutoff_year <= yr <= snapshot_year:
                by_ba_year[ba][yr] += mw
                by_tech_year[_tech_bucket(r.get("technology"))][yr] += mw

        retire = r.get("planned-retirement-year-month")
        if retire and len(retire) >= 4:
            ry = int(retire[:4])
            # Bound the retirement horizon. Unbounded, this field spans 1950 to
            # 2049 — the early dates are data errors on units that are still
            # operating, and the late ones are decades beyond any decision
            # window. Summing all of them against a four-year addition history
            # compared two incomparable periods and overstated attrition by
            # roughly a quarter.
            if snapshot_year <= ry <= retire_horizon_year:
                retirements[ba] += mw

    years = sorted({y for d in by_ba_year.values() for y in d})

    ba_rows = []
    for ba, meta in BALANCING_AUTHORITIES.items():
        per_year = {str(y): round(by_ba_year[ba].get(y, 0.0), 1) for y in years}
        added = sum(by_ba_year[ba].values())
        ba_rows.append({
            "ba": ba,
            "name": meta["name"],
            "region": meta["region"],
            "dc_flagged": meta["dc_note"] is not None,
            "added_mw": round(added, 1),
            "by_year": per_year,
            "planned_retirement_mw": round(retirements.get(ba, 0.0), 1),
            "net_mw": round(added - retirements.get(ba, 0.0), 1),
            "operating_mw": round(operating_total.get(ba, 0.0), 1),
            "added_pct_of_fleet": (
                round(added / operating_total[ba] * 100, 1)
                if operating_total.get(ba) else None
            ),
        })

    tech_rows = [
        {
            "technology": tech,
            "by_year": {str(y): round(d.get(y, 0.0), 1) for y in years},
            "total_mw": round(sum(d.values()), 1),
        }
        for tech, d in sorted(by_tech_year.items(), key=lambda kv: -sum(kv[1].values()))
    ]

    return {
        "snapshot": snapshot,
        "years": [str(y) for y in years],
        "partial_final_year": int(snapshot[5:7]) < 12,
        "addition_window": f"{cutoff_year}–{snapshot_year}",
        "retirement_window": f"{snapshot_year}–{retire_horizon_year}",
        # Sorted by NET, not gross. Gross additions are the number that gets
        # quoted; net is the number that changes the supply picture, and a BA
        # retiring as fast as it builds should not lead the table.
        "by_ba": sorted(ba_rows, key=lambda r: r["net_mw"], reverse=True),
        "by_technology": tech_rows,
        "source": f"EIA operable-generator inventory (Form EIA-860M), snapshot {snapshot}",
        "caveat": (
            f"Additions are realised units that entered service {cutoff_year}–{snapshot_year}. "
            f"Retirements are operating units carrying a retirement date in {snapshot_year}–"
            f"{retire_horizon_year} — a forward window, so the two columns cover different "
            "periods and the difference is a build-rate-versus-attrition comparison, not a "
            "balance. EIA's v2 API exposes operable units only: planned and under-construction "
            "capacity is published in the EIA-860M spreadsheet, not the API, and is absent here. "
            "Units retired before the snapshot are also absent."
        ),
    }


# ─────────────────────────────────────────────────────────────────
# 3. Curated reference — capital commitment against observable revenue
#
# These are disclosed or published figures, not computed. Every entry carries
# its source and as-of date and the UI renders them as such. They change on
# earnings cadence and are reviewed then; nothing here is inferred or
# extrapolated.
# ─────────────────────────────────────────────────────────────────

CAPEX_GUIDANCE = [
    {"entity": "Amazon",    "basis": "calendar", "low_usd_bn": 200.0, "high_usd_bn": 200.0,
     "prior_usd_bn": 125.0, "source": "Company guidance", "as_of": "2026-07"},
    {"entity": "Microsoft", "basis": "calendar", "low_usd_bn": 190.0, "high_usd_bn": 190.0,
     "prior_usd_bn": None,  "source": "Company guidance (June fiscal year)", "as_of": "2026-07"},
    {"entity": "Alphabet",  "basis": "calendar", "low_usd_bn": 175.0, "high_usd_bn": 185.0,
     "prior_usd_bn": 91.0,  "source": "Company guidance", "as_of": "2026-07"},
    {"entity": "Meta",      "basis": "calendar", "low_usd_bn": 139.0, "high_usd_bn": 145.0,
     "prior_usd_bn": None,  "source": "Company guidance", "as_of": "2026-07"},
]

# Oracle is deliberately excluded from the subtotal — it reports on a May
# fiscal year, so adding it to calendar-year guidance would sum different
# periods. Carried separately for reference.
CAPEX_NON_ADDITIVE = [
    {"entity": "Oracle", "basis": "fiscal (May year-end)", "fy26_usd_bn": 55.7,
     "fy27_guided_usd_bn": 95.0, "note": "Partly customer-reimbursed",
     "source": "Company disclosure", "as_of": "2026-07"},
]

REVENUE_SCOPES = [
    {
        "scope": "Frontier laboratory run-rates",
        "value_usd_bn": 55.0,
        "detail": "OpenAI ~$25bn, Anthropic ~$30bn",
        "source": "Third-party estimates — private companies, not audited disclosure",
        "as_of": "2026-07",
        "double_counts": False,
        "preferred": True,
        "note": "Narrowest and cleanest test of end demand.",
    },
    {
        "scope": "Enterprise generative-AI spending",
        "value_usd_bn": 37.0,
        "detail": "Survey of ~500 US enterprise decision-makers",
        "source": "Menlo Ventures",
        "as_of": "2025 full year",
        "double_counts": False,
        "preferred": False,
        "note": "A prior-year figure and a narrow software slice. Not directly comparable to 2026 capex.",
    },
    {
        "scope": "Enterprise AI spending, all categories",
        "value_usd_bn": 407.0,
        "detail": "Includes infrastructure and services",
        "source": "Gartner forecast",
        "as_of": "2026 forecast",
        "double_counts": True,
        "preferred": False,
        "note": "Double-counts against capex — includes purchases of the infrastructure being capitalised.",
    },
]

US_NOMINAL_GDP_USD_BN = 32_400.0


def capital_reference() -> dict:
    """Capex guidance against observable revenue, at explicitly stated scopes.

    Returns a coverage ratio per scope rather than a single headline number.
    The ratio moves by roughly 7x across published definitions of "AI revenue",
    so a point estimate would be a presentation choice dressed as a finding.
    """
    low = sum(c["low_usd_bn"] for c in CAPEX_GUIDANCE)
    high = sum(c["high_usd_bn"] for c in CAPEX_GUIDANCE)
    prior = sum(c["prior_usd_bn"] for c in CAPEX_GUIDANCE if c["prior_usd_bn"])

    scopes = [
        {
            **s,
            "coverage_low_pct": round(s["value_usd_bn"] / high * 100, 1),
            "coverage_high_pct": round(s["value_usd_bn"] / low * 100, 1),
        }
        for s in REVENUE_SCOPES
    ]

    return {
        "capex": {
            "entities": CAPEX_GUIDANCE,
            "non_additive": CAPEX_NON_ADDITIVE,
            "subtotal_low_usd_bn": round(low, 1),
            "subtotal_high_usd_bn": round(high, 1),
            "pct_of_us_gdp_low": round(low / US_NOMINAL_GDP_USD_BN * 100, 2),
            "pct_of_us_gdp_high": round(high / US_NOMINAL_GDP_USD_BN * 100, 2),
            "prior_year_partial_usd_bn": round(prior, 1),
        },
        "revenue_scopes": scopes,
        "us_nominal_gdp_usd_bn": US_NOMINAL_GDP_USD_BN,
        "caveat": (
            "Capex is an annual flow; laboratory revenue is a run-rate, which flatters the "
            "capital side. Gross capex commitments include foreign spending and imported "
            "content and are therefore not comparable to AI investment as measured in the "
            "US national accounts (~0.8–1.4% of GDP)."
        ),
        "curated": True,
    }
