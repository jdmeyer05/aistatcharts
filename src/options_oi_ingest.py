"""Daily OI snapshot ingest.

Polygon Options Starter exposes current OI on the snapshot endpoint but
nothing historical — ``as_of`` silently returns current, and flat files
don't include the OI column. So we capture it ourselves, one snapshot per
trading day, and accumulate a time series in Supabase.

Called by:
  - API endpoint POST /api/admin/oi-snapshot (scheduled via Cloud Scheduler)
  - Manual runs for backfill/debug

Scope:
  - Universe: rank ~500 candidate tickers by total OI, keep top 200
  - Strike window: spot ± 2σ where σ = ATM_IV × sqrt(180/365)
    (annualized IV scaled to the 180-day DTE cap — covers where OI lives)
  - DTE cap: ≤180 days
  - OI filter: ≥10 (strip illiquid noise)
"""
from __future__ import annotations

import math
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

import requests

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────
# Seed universe — ~500 liquid optionable names. Curated from typical OCC
# top-200 by options volume plus major ETFs/ADRs and S&P 500 mega-caps.
# The daily ranking step narrows this to top 200 by aggregate OI before
# storage, so extras here are cheap (extra snapshot calls, nothing else).
# ────────────────────────────────────────────────────────────────────────
SEED_UNIVERSE: list[str] = [
    # Index/broad ETFs
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "VEA", "VWO", "VUG", "VTV",
    "IVV", "IJH", "IJR", "EFA", "EEM", "ACWI", "SCHB", "ITOT", "RSP", "MDY",
    # Sector SPDRs
    "XLE", "XLF", "XLK", "XLI", "XLU", "XLP", "XLY", "XLV", "XLB", "XLC", "XLRE",
    # Fixed income ETFs
    "TLT", "IEF", "SHY", "HYG", "LQD", "TIP", "AGG", "BND", "EMB", "MUB",
    "JNK", "SHV", "BSV", "BIL", "TMF", "TBT", "VNQ", "GOVT",
    # Commodity ETFs
    "GLD", "SLV", "GDX", "GDXJ", "USO", "UNG", "DBA", "PDBC", "CPER", "PALL",
    "IAU", "SLVP", "URA", "LIT", "WEAT", "CORN", "SOYB",
    # Currency / crypto ETFs
    "UUP", "FXE", "FXY", "FXB", "BITO", "GBTC", "ETHE", "IBIT", "FBTC",
    # Leveraged / inverse
    "TQQQ", "SQQQ", "SOXL", "SOXS", "TNA", "TZA", "FAS", "FAZ", "LABU", "LABD",
    "UPRO", "SPXU", "UVXY", "SVXY", "UVIX", "VIXY", "VXX",
    # Volatility / thematic
    "ARKK", "ARKG", "ARKQ", "ARKW", "ARKF", "SMH", "SOXX", "XBI", "IBB",
    "IYR", "REM", "KRE", "KBE", "IAI", "XOP", "OIH", "XME", "REMX",
    # Country / region ETFs
    "FXI", "ASHR", "MCHI", "EWZ", "EWJ", "EWY", "EWG", "EWU", "EPI", "INDA",
    # Mega-caps
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA", "AVGO",
    "BRK.B", "LLY", "JPM", "V", "UNH", "XOM", "MA", "COST", "HD", "PG",
    "WMT", "NFLX", "JNJ", "ORCL", "ABBV", "BAC", "CVX", "KO", "MRK", "ADBE",
    # Tech
    "AMD", "INTC", "CRM", "CSCO", "QCOM", "TXN", "IBM", "ACN", "NOW", "INTU",
    "PLTR", "PANW", "CRWD", "SNOW", "NET", "DDOG", "ZS", "MDB", "TEAM", "OKTA",
    "SMCI", "MU", "MRVL", "ON", "WDC", "STX", "NXPI", "ADI", "KLAC", "LRCX",
    "AMAT", "ARM", "COIN", "HOOD", "UBER", "LYFT", "ABNB", "DASH", "SQ", "PYPL",
    "SHOP", "SPOT", "ROKU", "PINS", "SNAP", "TWLO", "DOCU", "ZM", "U", "BB",
    # AI / semis
    "MSTR", "RIOT", "MARA", "CLSK", "IREN", "BTDR", "WULF", "HUT", "BITF",
    "SOUN", "BBAI", "AI", "PATH", "SYM", "VRT", "DELL", "HPE", "HPQ",
    # Autos / EV
    "F", "GM", "RIVN", "LCID", "NIO", "XPEV", "LI", "TM", "STLA", "FSLR",
    "ENPH", "SEDG", "RUN", "PLUG", "BE", "NEE", "D", "DUK", "SO", "EXC",
    # Financials
    "GS", "MS", "WFC", "C", "USB", "TFC", "PNC", "COF", "AXP", "BLK",
    "SCHW", "TROW", "IBKR", "NDAQ", "ICE", "CME", "SPGI", "MCO", "MSCI",
    "PGR", "TRV", "ALL", "MET", "PRU", "AIG", "AFL", "CB", "CINF", "HIG",
    # Healthcare / biotech
    "ABT", "TMO", "DHR", "BMY", "AMGN", "GILD", "VRTX", "REGN", "BIIB", "MRNA",
    "PFE", "CVS", "CI", "ELV", "HUM", "MCK", "COR", "CAH", "BAX", "BDX",
    "ISRG", "SYK", "MDT", "BSX", "EW", "ILMN", "IDXX", "DXCM", "VEEV", "IQV",
    "NVAX", "BNTX", "CRSP", "NTLA", "BEAM", "EDIT", "SAVA",
    # Consumer
    "MCD", "SBUX", "NKE", "LULU", "DIS", "ROST", "TJX", "LOW", "TGT", "DG",
    "DLTR", "BJ", "KR", "WBA", "CMG", "YUM", "QSR", "DRI", "DPZ", "DOMO",
    "F", "EL", "CL", "PG", "KMB", "CHD", "CLX", "MDLZ", "GIS", "K",
    # Industrials / energy
    "CAT", "DE", "HON", "MMM", "GE", "RTX", "LMT", "NOC", "BA", "UPS", "FDX",
    "UNP", "CSX", "NSC", "DAL", "UAL", "AAL", "LUV", "SAVE", "JBLU",
    "OXY", "COP", "SLB", "HAL", "EOG", "PXD", "MPC", "VLO", "PSX", "MRO",
    "FANG", "DVN", "CHK", "AR", "EQT", "CTRA",
    # REITs / materials
    "AMT", "CCI", "EQIX", "DLR", "PLD", "SPG", "PSA", "O", "WPC", "STAG",
    "LIN", "SHW", "APD", "ECL", "NEM", "FCX", "GOLD", "AA", "CLF", "X",
    # Communication
    "T", "VZ", "TMUS", "CHTR", "CMCSA", "SIRI", "WBD", "PARA", "NFLX", "TTD",
    # Aerospace / defense
    "HWM", "GD", "TXT", "LHX", "AXON", "RKLB", "ASTS", "MAXR",
    # China / EM
    "BABA", "JD", "PDD", "BIDU", "NTES", "TAL", "EDU", "DIDI", "ZTO",
    # Misc high-volume
    "GME", "AMC", "BB", "TSM", "ASML", "SONY", "NVO", "UL", "BTI", "SHEL",
    "TTE", "BP", "BHP", "RIO", "VALE", "PBR", "SU", "CNQ", "CVE", "PAAS",
    "AG", "WPM", "BTG", "KGC", "NGD", "HMY",
]
# De-dupe while preserving order
SEED_UNIVERSE = list(dict.fromkeys(SEED_UNIVERSE))


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────
@dataclass
class TickerSnapshot:
    """One day's captured OI for a single ticker."""
    ticker: str
    spot: float
    window_pct: float
    atm_iv: float | None
    rows: list[dict] = field(default_factory=list)  # per-contract OI rows
    total_oi: int = 0
    total_volume: int = 0


def _polygon_get(session: requests.Session, url: str, params: dict, timeout: int = 20) -> dict:
    r = session.get(url, params=params, timeout=timeout)
    if r.status_code != 200:
        return {}
    return r.json()


def _fetch_snapshot_page(session: requests.Session, url: str, key: str, first_params: dict) -> list[dict]:
    """Paginate Polygon's snapshot endpoint. Auth passed as query param on first call;
    next_url already has auth context baked in except apiKey — re-append."""
    out: list[dict] = []
    next_url: str | None = url
    page_params = first_params
    for _ in range(10):  # hard cap on pages
        if not next_url:
            break
        j = _polygon_get(session, next_url, page_params)
        out.extend(j.get("results", []) or [])
        nxt = j.get("next_url")
        if not nxt or nxt == next_url:
            break
        next_url = nxt
        page_params = {"apiKey": key}
    return out


def _compute_atm_iv(rows: list[dict], spot: float) -> float | None:
    """Median IV across calls with DTE 20-90 days and |strike-spot| <= 2% spot."""
    candidates = []
    today = date.today()
    for r in rows:
        det = r.get("details") or {}
        if det.get("contract_type") != "call":
            continue
        k = det.get("strike_price")
        exp = det.get("expiration_date")
        iv = r.get("implied_volatility")
        if not k or not exp or not iv or iv <= 0:
            continue
        try:
            dte = (date.fromisoformat(exp) - today).days
        except Exception:
            continue
        if 20 <= dte <= 90 and abs(float(k) - spot) <= 0.02 * spot:
            candidates.append(float(iv))
    if not candidates:
        return None
    candidates.sort()
    return candidates[len(candidates) // 2]


def _strike_window_pct(atm_iv: float | None, days_horizon: int = 180) -> float:
    """±2σ expected-move width for the max-DTE horizon, clamped to [0.10, 1.00].

    σ = IV × sqrt(T); window = 2σ. Covers ~95% of expected terminal price
    under lognormal assumptions — where meaningful OI concentrates.
    """
    if not atm_iv or atm_iv <= 0:
        return 0.20  # fallback — wider than fixed 15% but not crazy
    sigma = atm_iv * math.sqrt(days_horizon / 365.0)
    return max(0.10, min(1.00, 2.0 * sigma))


def _fetch_spot(session: requests.Session, ticker: str, api_key: str) -> float:
    """Spot price via the stock snapshot endpoint. Returns 0 on failure."""
    url = f"https://api.polygon.io/v3/snapshot/stocks/{ticker}"
    j = _polygon_get(session, url, {"apiKey": api_key}, timeout=10)
    res = j.get("results") or j.get("ticker") or {}
    # Shape differs across responses — try known price paths
    for path in (
        ("session", "price"),
        ("last_trade", "price"),
        ("day", "close"),
        ("prev_day", "close"),
        ("prevDay", "c"),
        ("min", "c"),
        ("price",),
    ):
        cur = res
        ok = True
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok and isinstance(cur, (int, float)) and cur > 0:
            return float(cur)
    # Fallback — prev close via v2 aggs
    url2 = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/prev"
    j2 = _polygon_get(session, url2, {"apiKey": api_key, "adjusted": "true"}, timeout=10)
    results = j2.get("results") or []
    if results:
        c = results[0].get("c")
        if isinstance(c, (int, float)) and c > 0:
            return float(c)
    return 0.0


def fetch_ticker_oi(
    ticker: str,
    api_key: str,
    days_horizon: int = 180,
    min_oi: int = 10,
    session: requests.Session | None = None,
) -> TickerSnapshot | None:
    """Fetch one day's OI snapshot for a ticker with 2σ strike window.

    Returns None if ticker has no options data or spot can't be derived.
    """
    sess = session or requests.Session()

    # Step 1: spot price (dedicated call — the options snapshot endpoint's
    # underlying_asset.price isn't reliable on Starter tier)
    spot = _fetch_spot(sess, ticker, api_key)
    if not spot:
        return None

    # Step 2: ATM probe for IV → window sizing. Skip 0DTE/expiring-soon
    # contracts — they lack IV. 20-90 DTE hits monthlies that carry IV.
    probe_url = f"https://api.polygon.io/v3/snapshot/options/{ticker}"
    today = date.today().isoformat()
    horizon = (date.today() + timedelta(days=days_horizon)).isoformat()
    probe_exp_lo = (date.today() + timedelta(days=20)).isoformat()
    probe_exp_hi = (date.today() + timedelta(days=90)).isoformat()
    atm_lo = spot * 0.95
    atm_hi = spot * 1.05
    probe_params = {
        "apiKey": api_key,
        "strike_price.gte": str(round(atm_lo, 2)),
        "strike_price.lte": str(round(atm_hi, 2)),
        "expiration_date.gte": probe_exp_lo,
        "expiration_date.lte": probe_exp_hi,
        "contract_type": "call",
        "limit": 100,
    }
    probe = _polygon_get(sess, probe_url, probe_params)
    probe_rows = probe.get("results", []) or []
    if not probe_rows:
        return None

    atm_iv = _compute_atm_iv(probe_rows, spot)
    window = _strike_window_pct(atm_iv, days_horizon)
    lo, hi = spot * (1 - window), spot * (1 + window)

    # Step 2: full snapshot with strike window and expiration cap.
    full_params = {
        "apiKey": api_key,
        "strike_price.gte": str(round(lo, 2)),
        "strike_price.lte": str(round(hi, 2)),
        "expiration_date.gte": today,
        "expiration_date.lte": horizon,
        "limit": 250,
    }
    all_rows = _fetch_snapshot_page(sess, probe_url, api_key, full_params)

    snap = TickerSnapshot(ticker=ticker, spot=spot, window_pct=window, atm_iv=atm_iv)
    for r in all_rows:
        det = r.get("details") or {}
        day = r.get("day") or {}
        strike = det.get("strike_price")
        exp = det.get("expiration_date")
        ct = det.get("contract_type")
        oi = r.get("open_interest")
        if strike is None or exp is None or ct not in ("call", "put") or oi is None:
            continue
        oi = int(oi)
        if oi < min_oi:
            continue
        vol = int(day.get("volume") or 0)
        iv = r.get("implied_volatility")
        snap.rows.append({
            "strike": float(strike),
            "expiration": exp,
            "contract_type": ct,
            "open_interest": oi,
            "volume": vol,
            "implied_vol": float(iv) if iv else None,
        })
        snap.total_oi += oi
        snap.total_volume += vol
    return snap


def capture_universe(
    tickers: Iterable[str],
    api_key: str,
    top_n: int = 200,
    max_workers: int = 12,
) -> tuple[list[TickerSnapshot], list[dict]]:
    """Fetch OI for every ticker in parallel, rank by total OI, keep top N.

    Returns (kept_snapshots, ranking_rows) where ranking_rows has one dict
    per ticker with {ticker, rank, total_oi, total_volume} for the ranked top-N.
    """
    session = requests.Session()
    results: list[TickerSnapshot] = []

    def _one(tk: str) -> TickerSnapshot | None:
        try:
            return fetch_ticker_oi(tk, api_key, session=session)
        except Exception as e:
            logger.warning("OI fetch failed for %s: %s", tk, e)
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_one, tk): tk for tk in tickers}
        for fut in as_completed(futures):
            snap = fut.result()
            if snap and snap.rows:
                results.append(snap)

    # Rank by total OI descending, slice top N
    results.sort(key=lambda s: s.total_oi, reverse=True)
    kept = results[:top_n]
    ranking = [
        {
            "ticker": s.ticker,
            "rank": i + 1,
            "total_oi": s.total_oi,
            "total_volume": s.total_volume,
        }
        for i, s in enumerate(kept)
    ]
    return kept, ranking
