"""Capital and financing for the AI build-out, from filed SEC statements.

The rest of this page measures the physical chain — metered demand, generators
actually running. This module does the financial link the same way: what these
companies have ACTUALLY PAID and how they paid for it, taken from 10-K and 10-Q
filings, against the guidance in `ai_infra.CAPEX_GUIDANCE` which is what they
SAY they will spend.

That gap is the whole point of the tab. Announced capex is a press release;
`PaymentsToAcquirePropertyPlantAndEquipment` is cash that left the building.

WHAT IS AND IS NOT TAGGED — measured 2026-08-03 against the live EDGAR API,
not assumed:

    concept                                  AMZN  MSFT  GOOGL  META  ORCL
    PaymentsToAcquirePropertyPlantAndEquip.   yes   yes   yes    yes   yes
    NetCashProvidedByUsedInOperatingActiv.    yes   yes   yes    yes   yes
    OperatingLeaseLiabilityNoncurrent         yes   yes   yes    yes   yes
    FinanceLeaseLiabilityNoncurrent           yes    NO   yes    yes   yes
    LongTermDebtNoncurrent                    yes   yes   yes    yes    NO
    UnrecordedUnconditionalPurchaseOblig.     yes    NO   yes    yes   yes

The last row is the one worth stating plainly: unrecorded purchase obligations
were expected to live only in narrative footnotes and require extraction. Four
of the five tag them. MICROSOFT DOES NOT, and it is reported as untagged rather
than as zero — a company with no tag is not a company with no obligations, and
substituting a default for an unknown is the defect pattern this page exists to
avoid.

NO CROSS-COMPANY TOTALS. Microsoft closes its year in June and Oracle in May, so
a sum across these five would add different twelve-month periods together. The
curated capex block already made this call for Oracle; this module applies it to
everything, and every row carries the period it covers.
"""

from __future__ import annotations

import gzip
import json
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# SEC requires a descriptive User-Agent with contact information. Requests
# without one are refused, and a generic library default gets the IP blocked.
_UA = "aistatcharts (contact: jdmeyer05@gmail.com)"
_BASE = "https://data.sec.gov/api/xbrl/companyconcept"

# The four calendar-year reporters carried in CAPEX_GUIDANCE, plus Oracle, which
# the guidance block carries separately for the same fiscal-year reason.
ISSUERS = [
    {"entity": "Amazon",    "ticker": "AMZN",  "cik": 1018724, "fy_end": "December"},
    {"entity": "Microsoft", "ticker": "MSFT",  "cik": 789019,  "fy_end": "June"},
    {"entity": "Alphabet",  "ticker": "GOOGL", "cik": 1652044, "fy_end": "December"},
    {"entity": "Meta",      "ticker": "META",  "cik": 1326801, "fy_end": "December"},
    {"entity": "Oracle",    "ticker": "ORCL",  "cik": 1341439, "fy_end": "May"},
]

# Flows (income/cash-flow items) carry start+end; balances carry end only. The
# distinction decides how a datapoint is selected, so it is declared per concept
# rather than sniffed from the payload.
#
# EACH METRIC IS A LIST, IN PRIORITY ORDER, BECAUSE FILERS MIGRATE TAGS AND
# EDGAR KEEPS SERVING THE OLD ONE. Amazon reported capex under
# `PaymentsToAcquirePropertyPlantAndEquipment` until 2017 and has used
# `PaymentsToAcquireProductiveAssets` since. Both concepts still return 152
# datapoints, so a single-tag lookup does not fail — it silently answers with
# the last figure of the abandoned tag. That resolved Amazon to a year ending
# 2017-03-31 and put $7.4bn on the page against a real $173.0bn. A dead tag
# returning stale data is far more dangerous than one returning nothing.
_FLOWS = {
    "capex": [
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
}
_BALANCES = {
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "finance_lease": ["FinanceLeaseLiabilityNoncurrent"],
    "operating_lease": ["OperatingLeaseLiabilityNoncurrent"],
    "purchase_obligations": [
        "UnrecordedUnconditionalPurchaseObligationBalanceSheetAmount",
        "PurchaseObligation",
    ],
}

# An annual figure older than this is not "the latest year", it is a stale tag.
# Reported as unavailable rather than served — see the Amazon note above.
_MAX_PERIOD_AGE_DAYS = 550


def _get(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept-Encoding": "gzip, deflate"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw)
    except Exception as e:
        # A 404 here is the normal answer for "this company does not tag this
        # concept", which is information, not a failure. Logged at debug so an
        # untagged concept does not read as an outage in the logs.
        logger.debug(f"edgar {url.rsplit('/', 1)[-1]}: {e}")
        return None


def _points(cik: int, concept: str) -> list[dict]:
    j = _get(f"{_BASE}/CIK{cik:010d}/us-gaap/{concept}.json")
    if not j:
        return []
    pts = (j.get("units") or {}).get("USD") or []
    # Periodic reports only. An 8-K or S-1 can carry the same tag for a
    # different scope, and mixing them produces a series that steps for
    # reasons that have nothing to do with the business.
    return [p for p in pts if str(p.get("form", "")).startswith("10-")]


def _annual_flow(points: list[dict]) -> list[dict]:
    """Full-year figures, newest first.

    A cash-flow tag carries year-to-date values in every 10-Q as well as the
    annual figure in the 10-K, so filtering on duration is what separates "the
    year" from "the nine months to September". Anything between 350 and 380
    days is a fiscal year; a 52/53-week filer lands inside that band.
    """
    out: dict[str, dict] = {}
    for p in points:
        start, end = p.get("start"), p.get("end")
        if not start or not end:
            continue
        try:
            from datetime import date
            d0 = date.fromisoformat(start)
            d1 = date.fromisoformat(end)
        except Exception:
            continue
        days = (d1 - d0).days
        if not (350 <= days <= 380):
            continue
        # Restatements and amended filings repeat a period. The latest FILED
        # version of a period supersedes the earlier one.
        prev = out.get(end)
        if prev is None or str(p.get("filed", "")) >= str(prev.get("filed", "")):
            out[end] = p
    return sorted(out.values(), key=lambda p: p["end"], reverse=True)


def _latest_balance(points: list[dict]) -> dict | None:
    """Most recent balance-sheet figure, superseded-filing aware."""
    best: dict | None = None
    for p in points:
        if not p.get("end"):
            continue
        if best is None or (p["end"], str(p.get("filed", ""))) > (best["end"], str(best.get("filed", ""))):
            best = p
    return best


def _bn(v) -> float | None:
    return round(float(v) / 1e9, 2) if v is not None else None


def _fresh(end: str | None) -> bool:
    """Is this period recent enough to be the latest one, or an abandoned tag?"""
    if not end:
        return False
    try:
        from datetime import date
        return (date.today() - date.fromisoformat(end)).days <= _MAX_PERIOD_AGE_DAYS
    except Exception:
        return False


def _issuer_capital(issuer: dict) -> dict:
    cik = issuer["cik"]

    def flow(concepts: list[str]) -> tuple[list[dict], str | None]:
        """First concept in priority order that yields a RECENT annual series.

        Falls through a tag whose newest figure is stale, which is how a
        migrated concept is detected — it still returns data, just old data.

        10-K PERIODS ONLY. A 350-380 day window also appears in 10-Qs as a
        rolling twelve months, and Amazon's newest such window ran to June 2026
        while Alphabet's and Meta's fiscal years ran to December 2025 — three
        different twelve-month periods that the subtotal below would have
        added together. Restricting to the annual report gives every issuer its
        last completed fiscal year, which is the only basis on which any of
        these figures are comparable.
        """
        stale: tuple[list[dict], str | None] = ([], None)
        for c in concepts:
            years = [p for p in _annual_flow(_points(cik, c)) if p.get("form") == "10-K"]
            if years and _fresh(years[0].get("end")):
                return years, c
            if years and not stale[0]:
                stale = (years, c)
        return stale

    def balance(concepts: list[str]) -> tuple[dict | None, str | None]:
        for c in concepts:
            p = _latest_balance(_points(cik, c))
            # A LIABILITY OF EXACTLY ZERO IS A SCOPING ARTEFACT, NOT A FACT.
            # Oracle's `LongTermDebt` fallback resolved to 0.0 — a company with
            # tens of billions outstanding — because the tag carries a narrower
            # context than the one intended. Zero is treated as untagged, which
            # renders as a blank the reader can interrogate rather than a
            # number that quietly says "this company has no debt".
            if p and float(p.get("val") or 0) != 0:
                return p, c
        return None, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        f_flow = {k: pool.submit(flow, c) for k, c in _FLOWS.items()}
        f_bal = {k: pool.submit(balance, c) for k, c in _BALANCES.items()}
        flows = {k: f.result() for k, f in f_flow.items()}
        bals = {k: f.result() for k, f in f_bal.items()}

    concepts_used = {k: v[1] for k, v in list(flows.items()) + list(bals.items())}
    flows = {k: v[0] for k, v in flows.items()}
    bals = {k: v[0] for k, v in bals.items()}

    capex_years = flows["capex"]
    ocf_years = flows["operating_cash_flow"]
    capex = capex_years[0] if capex_years else None
    capex_prior = capex_years[1] if len(capex_years) > 1 else None
    # The operating cash flow for the SAME period, not merely the latest one —
    # a ratio built from two different twelve-month windows is not a ratio.
    ocf = next((p for p in ocf_years if capex and p["end"] == capex["end"]), None)

    capex_bn = _bn(capex["val"]) if capex else None
    prior_bn = _bn(capex_prior["val"]) if capex_prior else None
    ocf_bn = _bn(ocf["val"]) if ocf else None

    row: dict = {
        "entity": issuer["entity"],
        "ticker": issuer["ticker"],
        "cik": cik,
        "fiscal_year_end": issuer["fy_end"],
        # Every figure below covers THIS period. Named on the row because the
        # five issuers do not share one.
        "period_end": capex["end"] if capex else None,
        "period_start": capex["start"] if capex else None,
        "filed": capex.get("filed") if capex else None,
        "form": capex.get("form") if capex else None,
        # Which XBRL tag each figure came from. Filers migrate concepts and the
        # old tag keeps answering, so the tag in use is part of the provenance,
        # not an implementation detail.
        "concepts": concepts_used,
        "period_is_stale": not _fresh(capex["end"] if capex else None),
        "capex_usd_bn": capex_bn,
        "capex_prior_usd_bn": prior_bn,
        "capex_growth_pct": (round((capex_bn - prior_bn) / prior_bn * 100, 1)
                             if capex_bn and prior_bn else None),
        "operating_cash_flow_usd_bn": ocf_bn,
        # THE NUMBER THIS TAB EXISTS FOR. Below 100% the build is paid for out
        # of what the business generates. Above it, the difference has to come
        # from the balance sheet — cash, debt or leases — and the financing
        # rows below say which.
        "capex_to_ocf_pct": (round(capex_bn / ocf_bn * 100, 1)
                             if capex_bn and ocf_bn else None),
        "free_cash_flow_usd_bn": (round(ocf_bn - capex_bn, 2)
                                  if capex_bn is not None and ocf_bn is not None else None),
    }

    for key, concept in _BALANCES.items():
        p = bals.get(key)
        row[f"{key}_usd_bn"] = _bn(p["val"]) if p else None
        row[f"{key}_asof"] = p.get("end") if p else None
        # Explicitly untagged, NOT zero. Microsoft tags neither finance leases
        # nor purchase obligations, and rendering those as 0.0 would read as
        # "this company has none" — which is a stronger and more wrong claim
        # than "this company does not tag it".
        row[f"{key}_tagged"] = p is not None

    return row


def capital_financing() -> dict:
    """Filed capital spending and how it is financed, per issuer.

    Realised only: every figure traces to a 10-K or 10-Q line item with the
    period and filing date attached. Nothing here is guidance, and nothing is
    summed across issuers.
    """
    with ThreadPoolExecutor(max_workers=len(ISSUERS)) as pool:
        rows = list(pool.map(_issuer_capital, ISSUERS))

    rows = [r for r in rows if r.get("capex_usd_bn") is not None]
    rows.sort(key=lambda r: r["capex_usd_bn"], reverse=True)

    # Calendar-year reporters only, and only where the fiscal years actually
    # coincide. Fiscal-year-end month is necessary but not sufficient: a filer
    # that has reported its next 10-K while its peers have not would otherwise
    # contribute a later year to the same sum.
    additive = [r for r in rows if r["fiscal_year_end"] == "December"]
    if additive:
        common_end = max(r["period_end"] for r in additive)
        additive = [r for r in additive if r["period_end"] == common_end]
    # Every balance concept, not just two of them. Oracle tags neither
    # `LongTermDebtNoncurrent` nor `LongTermDebt` at a usable scope, so checking
    # only leases and obligations left it out of the list the page names while
    # still rendering it a blank cell — the caption and the table disagreed.
    untagged = sorted({r["entity"] for r in rows
                       if any(not r.get(f"{k}_tagged") for k in _BALANCES)})

    return {
        "available": bool(rows),
        "issuers": rows,
        "calendar_year_subtotal": {
            "entities": [r["entity"] for r in additive],
            "capex_usd_bn": round(sum(r["capex_usd_bn"] for r in additive), 1) if additive else None,
            "operating_cash_flow_usd_bn": (
                round(sum(r["operating_cash_flow_usd_bn"] for r in additive
                          if r["operating_cash_flow_usd_bn"]), 1) if additive else None),
            "note": ("December fiscal-year filers only. Microsoft (June) and Oracle (May) "
                     "close different twelve-month periods and are excluded from the sum, "
                     "not from the table."),
        },
        "untagged_entities": untagged,
        "source": "SEC EDGAR XBRL company concepts (10-K / 10-Q as filed)",
        "caveat": (
            "Capex here is total property and equipment purchases, not a data-centre line — "
            "no filer breaks out AI infrastructure separately, so this overstates the "
            "build by whatever else the company is buying. Figures are as filed and "
            "restatements supersede: the latest filed version of each period is used. "
            "A blank is a concept the filer does not tag, which is not the same as zero."
        ),
    }
