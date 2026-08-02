"""Cross-Asset Volatility Analysis — shared module for pages 46 and 48.

Provides universe definitions, parallel data loading, metrics computation,
smile interpolation, and implied correlation calculation.
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Universe Definition ──────────────────────────────────────────────────────

SCAN_UNIVERSE = {
    "Sectors": {
        "XLE": "Energy", "XLF": "Financials", "XLK": "Technology",
        "XLV": "Healthcare", "XLI": "Industrials", "XLC": "Communication",
        "XLY": "Consumer Disc", "XLP": "Consumer Staples", "XLU": "Utilities",
        "XLB": "Materials", "XLRE": "Real Estate",
    },
    "Macro": {
        "SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000",
        "TLT": "Long Bonds", "GLD": "Gold", "USO": "Crude Oil",
        "EFA": "Intl Developed", "EEM": "Emerging Mkts", "HYG": "High Yield",
    },
}

ALL_TICKERS = {}
for _group in SCAN_UNIVERSE.values():
    ALL_TICKERS.update(_group)


def get_rfr():
    """Get risk-free rate from FRED (cached at call site)."""
    try:
        from src.market_data import fetch_fred_series
        df = fetch_fred_series("DGS3MO", periods=5)
        if not df.empty:
            return df["value"].iloc[-1] / 100
    except Exception:
        pass
    return 0.045


# ── ATM IV and Delta Helpers ─────────────────────────────────────────────────

def atm_iv(chain, spot, opt_type="call"):
    """ATM implied volatility from a chain, or None when the chain cannot say.

    Returns None rather than a placeholder. This used to hand back 0.25 for an
    empty chain or a non-positive quote, which is a plausible ATM vol for an
    equity ETF and therefore invisible downstream — it flowed into Front_IV,
    IV_HV, VRP and the universe averages as though it had been measured.

    The case that forced the change: parity is atm_put_iv / atm_call_iv, and
    with both legs defaulting the ratio came out at exactly 1.00. A chain with
    no data in it scored as perfectly consistent, so the quality gate built on
    parity reported "clean" in precisely the situation it exists to catch.

    Callers must handle None. A missing ATM vol is a fact worth propagating.
    """
    if chain is None or len(chain) == 0:
        return None
    sub = chain[chain["contract_type"] == opt_type].reset_index(drop=True)
    if sub.empty:
        return None
    atm_row = sub.loc[(sub["strike_price"] - spot).abs().idxmin()]
    iv = atm_row.get("implied_volatility", 0) or 0
    return float(iv) if iv > 0 else None


def _delta_ladder_broken(sub, opt_type):
    """Fraction of adjacent strikes whose deltas contradict no-arbitrage.

    Delta is computed FROM implied vol, so a corrupt IV yields a corrupt delta �
    and an inflated IV pushes a far-OTM strike's delta up onto the 25-delta
    target, so the selector prefers the single worst quote on the chain. Live
    XLB: a 43 strike at 85% of spot carrying 121% IV on one lot of volume was
    chosen as the 25-delta put over a 48 strike at 95% with 606 open interest.

    |delta| must rise with strike for puts and fall for calls, so a pair that
    inverts is an arbitrage violation rather than a threshold anyone picked.
    SPY's ladder is monotone to four decimals; XLB's is not.

    This DETECTS the condition and does not repair it. Reconstructing the
    surface was tried and abandoned: picking the largest self-consistent subset
    leaves ties that the ordering cannot break, and weighting those ties by open
    interest optimises a whole-chain sum that will happily trade away the strikes
    near the target � on XLY it discarded a 471-lot strike and kept a 2-lot one.
    A wrong number that has been through a repair step is harder to catch than a
    wrong number that has been withheld, so callers withhold.
    """
    if "delta" not in sub.columns or len(sub) < 3:
        return 0.0
    s = sub.sort_values("strike_price")
    d = s["delta"].abs().to_numpy(dtype=float)
    d = d[~np.isnan(d)]
    if len(d) < 3:
        return 0.0
    diff = np.diff(d)
    bad = (diff < -1e-6) if opt_type == "put" else (diff > 1e-6)
    return float(bad.sum()) / float(len(diff))


def find_delta_strike(chain, spot, target_delta, opt_type):
    """Find the strike closest to a target delta. Prefers OI > 0."""
    sub = chain[chain["contract_type"] == opt_type].copy()
    sub = sub[sub["implied_volatility"] > 0]
    if sub.empty:
        return None, None
    if "open_interest" in sub.columns:
        sub_liquid = sub[sub["open_interest"].fillna(0) > 0]
        if len(sub_liquid) >= 3:
            sub = sub_liquid
    sub = sub.copy()
    sub["delta_abs"] = sub["delta"].abs()
    sub["delta_dist"] = (sub["delta_abs"] - abs(target_delta)).abs()
    sub = sub.dropna(subset=["delta_dist"])
    if sub.empty:
        return None, None

    # Reject a strike whose delta contradicts its IMMEDIATE neighbours.
    #
    # Delta is computed from implied vol, so a corrupt IV yields a corrupt delta
    # sitting right on the target — the plain nearest-delta match then prefers
    # the single worst quote on the chain. Live XLB: a 43 strike at 85% of spot
    # carrying 121% IV on one lot of volume beat a 48 strike at 95% with 606
    # open interest.
    #
    # The test is local on purpose. A whole-ladder monotonicity score does not
    # discriminate — measured across these 20 names it flags 19, including SPY,
    # because deep wings are thin everywhere and that noise is harmless. What
    # matters is only whether the strike being SELECTED is consistent where it
    # sits. Neighbours are taken from the full sorted ladder, so removing a bad
    # strike cannot make its neighbours look bad in turn.
    ladder = sub.sort_values("strike_price")
    dv = ladder["delta_abs"].to_numpy(dtype=float)
    lo = np.r_[-np.inf, dv[:-1]]
    hi = np.r_[dv[1:], np.inf]
    if opt_type == "put":                       # |delta| must rise with strike
        good = (dv >= lo - 1e-6) & (dv <= hi + 1e-6)
    else:                                       # and fall for calls
        good = (dv <= lo + 1e-6) & (dv >= hi - 1e-6)
    consistent = ladder[good | np.isnan(dv)]
    if len(consistent) >= 3:
        sub = consistent

    best = sub.loc[sub["delta_dist"].idxmin()]
    return float(best["strike_price"]), float(best.get("implied_volatility", 0))


def interpolate_smile(chain, spot, moneyness_points=None):
    """Interpolate IV at standardized moneyness points from a chain.

    Returns dict: {moneyness: iv_value} or None if insufficient data.
    moneyness_points defaults to [0.90, 0.95, 0.98, 1.00, 1.02, 1.05, 1.10].
    """
    if moneyness_points is None:
        moneyness_points = [0.90, 0.95, 0.98, 1.00, 1.02, 1.05, 1.10]

    # Use puts below spot, calls at/above spot (OTM for both sides)
    calls = chain[(chain["contract_type"] == "call") & (chain["strike_price"] >= spot)]
    puts = chain[(chain["contract_type"] == "put") & (chain["strike_price"] <= spot)]
    combined = pd.concat([calls, puts])
    combined = combined[combined["implied_volatility"] > 0]

    # Prefer contracts with OI
    if "open_interest" in combined.columns:
        liquid = combined[combined["open_interest"].fillna(0) > 0]
        if len(liquid) >= 5:
            combined = liquid

    if len(combined) < 3:
        return None

    combined = combined.copy().reset_index(drop=True)
    combined["moneyness"] = combined["strike_price"] / spot

    result = {}
    for m in moneyness_points:
        target_strike = spot * m
        nearby = combined.iloc[(combined["strike_price"] - target_strike).abs().argsort()[:2]]
        if len(nearby) == 0:
            result[m] = None
            continue
        # If closest strike is within 3% of target moneyness, use its IV
        closest = nearby.iloc[0]
        if abs(closest["moneyness"] - m) < 0.03:
            result[m] = float(closest["implied_volatility"])
        else:
            result[m] = None

    return result


def compute_implied_correlation(spy_iv, sector_ivs, sector_weights=None):
    """Implied correlation from index vs sector IVs.

    Dispersion identity — index variance is each component's own variance plus
    every cross term, with the cross terms scaled by the common correlation:

        sigma_idx^2 = SUM_i w_i^2 sigma_i^2
                      + rho * SUM_i SUM_{j!=i} w_i w_j sigma_i sigma_j

    so rho is the leftover index variance over the total cross term.

    That cross term is a sum of PRODUCTS of vols. An earlier form used
    avg(sigma^2) * (1 - 1/N), which equals it only when every sector IV is
    identical: by Jensen avg(sigma^2) >= avg(sigma)^2, so that denominator ran
    large and rho came out low. The gap widens with sector dispersion — exactly
    the regime where the number gets consulted. Today's chain reported 0.30
    against 0.32 actual.

    Equal weights are a real approximation, not a formality. The eleven SPDR
    sectors partition the index but not evenly — XLK is about a third of SPX and
    XLRE a fiftieth — so equal weighting misweights the cross terms, and the
    error does not vanish with sample size. Live index weights need a
    constituent feed this project does not carry. Read the result as roughly
    where dispersion sits, not as a printable COR-index value.

    Args:
        spy_iv: ATM IV of index (SPY)
        sector_ivs: list of sector ATM IVs
        sector_weights: optional weights (e.g., market cap proportional). Equal-weight if None.
    """
    if not sector_ivs or spy_iv <= 0:
        return None
    valid = [iv for iv in sector_ivs if iv > 0]
    n = len(valid)
    if n < 2:
        return None
    sig = np.array(valid, dtype=float)
    if sector_weights is not None and len(sector_weights) == n:
        w = np.array(sector_weights, dtype=float)
        if w.sum() <= 0:
            return None
        w = w / w.sum()
    else:
        w = np.full(n, 1.0 / n)

    own_var = float(np.sum(w ** 2 * sig ** 2))
    ws = w * sig
    # SUM_{i!=j} (w_i sig_i)(w_j sig_j) = (SUM ws)^2 - SUM ws^2
    cross = float(ws.sum() ** 2 - np.sum(ws ** 2))
    if cross <= 0:
        return None
    rho = (spy_iv ** 2 - own_var) / cross
    return max(0.0, min(1.0, rho))


# ── Parallel Data Loading ────────────────────────────────────────────────────

def load_universe_data(tickers_dict, rfr=0.045):
    """Load price + options data for multiple tickers in parallel.

    Args:
        tickers_dict: {ticker: label} mapping
        rfr: risk-free rate

    Returns:
        dict: {ticker: {spot, chains, expirations, hv20, px_df, label}}
    """
    from src.data_engine import fetch_massive_data, get_expiration_dates, fetch_options_chain
    from src.options_models import fill_missing_options_data
    from concurrent.futures import ThreadPoolExecutor, as_completed

    today_str = pd.Timestamp.now().strftime("%Y-%m-%d")
    tickers = list(tickers_dict.keys())

    def _load_one(tk):
        try:
            px = fetch_massive_data(tk, 252)
            if px is None or px.empty:
                return None
            spot = float(px["Close"].iloc[-1])
            if pd.isna(spot) or spot <= 0:
                return None

            all_exps = get_expiration_dates(tk)
            valid_exps = [e for e in (all_exps or []) if e >= today_str]
            monthly = [e for e in valid_exps
                       if 15 <= pd.to_datetime(e).day <= 21 and pd.to_datetime(e).weekday() == 4][:3]
            if len(monthly) < 2:
                monthly = valid_exps[:3]

            chains = {}
            for exp in monthly:
                try:
                    cdf = fetch_options_chain(tk, exp)
                    if cdf is not None and not cdf.empty:
                        cdf = fill_missing_options_data(cdf, spot, risk_free_rate=rfr)
                        chains[exp] = cdf
                except Exception:
                    pass

            if chains:
                rets = px["Close"].pct_change().dropna()
                hv20 = float((rets.rolling(20).std() * np.sqrt(252)).dropna().iloc[-1]) if len(rets) > 20 else None
                return {
                    "tk": tk, "spot": spot, "chains": chains,
                    "expirations": sorted(chains.keys()),
                    "hv20": hv20, "px_df": px, "label": tickers_dict.get(tk, tk),
                }
        except Exception as e:
            logger.warning(f"Failed to load {tk}: {e}")
        return None

    result = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_load_one, tk): tk for tk in tickers}
        for future in as_completed(futures):
            data = future.result()
            if data:
                tk = data.pop("tk")
                result[tk] = data

    return result


# ── Metrics Computation ──────────────────────────────────────────────────────

def compute_cross_asset_metrics(ticker_data, rfr=0.045):
    """Compute per-ticker vol metrics from loaded chain data.

    Args:
        ticker_data: dict from load_universe_data()
        rfr: risk-free rate

    Returns:
        pd.DataFrame with columns: Ticker, Label, Group, Spot, Front_IV, Back_IV,
        IV_HV, Put_Skew, Risk_Rev, TS_Slope, VRP, Impl_Move, HV20, PC_Ratio, IV_Pctile
    """
    rows = []
    for tk, td in ticker_data.items():
        spot = td["spot"]
        chains = td["chains"]
        exps = td["expirations"]
        hv20 = td.get("hv20")
        label = td.get("label", tk)

        if not exps or not chains:
            continue

        # Determine group
        group = "Other"
        for gname, gtickers in SCAN_UNIVERSE.items():
            if tk in gtickers:
                group = gname
                break

        front_chain = chains[exps[0]]
        front_iv = atm_iv(front_chain, spot, "call")
        if not front_iv:
            # Every metric below is a ratio or a slope anchored on the front ATM
            # vol. Without it the row would be a set of numbers computed against
            # a placeholder, which is the failure this whole pass has been about.
            logger.debug(f"{tk}: no ATM vol on {exps[0]}, skipping")
            continue
        # The SECOND expiration, not the last one.
        #
        # exps is up to three third-Friday monthlies, but which three depends on
        # what each product lists. Measured live: SPY and QQQ reach only two, so
        # their curve spanned 28 days, most names spanned 56, XLI and XLRE 91,
        # and XLB/XLC/XLU 119 — the last three skip the October and November
        # monthlies entirely. Dividing by the day count normalises to "per month"
        # but a vol curve is not linear in tenor, so a slope taken over 119 days
        # is a different quantity from one taken over 28 and the two do not
        # belong in the same n_inverted count.
        #
        # Every ticker in the universe lists the front two monthlies, so the
        # second expiry is the one tenor pair they all share. The third chain
        # stays loaded for the term-structure matrix that displays it directly.
        back_idx = 1 if len(exps) >= 2 else 0
        back_iv = atm_iv(chains[exps[back_idx]], spot, "call")
        if not back_iv:
            back_iv = front_iv       # flat curve, not an invented one

        # Put skew. Measured against the ATM PUT, not the ATM call.
        #
        # Put-call parity says the two ATM IVs must agree, so dividing a 25-delta
        # put by an ATM call looks harmless. In this universe they do not agree:
        # the put/call ratio at the same strike and expiry runs from 0.78 (QQQ)
        # to 2.30 (HYG). Both legs of that ratio being puts makes whatever breaks
        # parity — carry, a stale mid, a wide spread — cancel instead of landing
        # in the numerator alone. HYG read 3.10x against a call and 1.35x against
        # a put; the second is a mild put skew, the first was a fear signal that
        # was not there.
        _, p25_iv = find_delta_strike(front_chain, spot, 0.25, "put")
        _, c25_iv = find_delta_strike(front_chain, spot, 0.25, "call")
        # No silent fall back to the ATM CALL when the put is missing: that is
        # the mixed-type ratio this comment block exists to describe, and doing
        # it as a fallback would reintroduce it invisibly on exactly the thin
        # chains where it does the most damage. None means unmeasurable, and
        # 1.0 would have meant "no skew" — a claim, not an absence.
        atm_put_iv = atm_iv(front_chain, spot, "put")
        put_skew = (p25_iv / atm_put_iv) if (p25_iv and atm_put_iv) else None

        # How far the chain's own ATM quotes disagree with each other. Parity
        # forces this to 1.0, so the distance from 1.0 is a direct read on how
        # much to trust anything else derived from this chain — it is measured
        # from the data rather than assumed, and callers gate on it.
        parity = (atm_put_iv / front_iv) if atm_put_iv and front_iv and front_iv > 0 else None

        # Second, independent quality signal on the same chain. Parity checks one
        # pair of quotes at the money; this checks the whole put ladder for the
        # arbitrage violations that make a 25-delta selection meaningless. They
        # catch different failures — XLB passes neither, XLY fails only this one.
        _puts = front_chain[(front_chain["contract_type"] == "put")
                            & (front_chain["implied_volatility"] > 0)]
        ladder_broken = _delta_ladder_broken(_puts, "put")

        # A risk reversal IS a call minus a put, so being cross-type is its
        # definition rather than a defect — there is no ATM reference in it to
        # get wrong, and it survives a chain whose ATM quotes disagree intact.
        risk_rev = ((c25_iv - p25_iv) * 100) if c25_iv and p25_iv else None

        # Butterfly measures each wing against its OWN type's ATM.
        #
        # The textbook form `p25 + c25 - 2*ATM` anchors BOTH wings on one ATM
        # quote, which is right only when parity holds. Anchoring on the ATM
        # CALL puts the whole parity gap into the put wing, and the error lands
        # in the total as exactly (atm_put_iv - front_iv). Measured live
        # 2026-08-02 that gap FLIPPED THE SIGN for QQQ (-4.67 -> +1.19), SPY
        # (-0.63 -> +0.26) and XLK (+0.52 -> -1.01), and overstated TLT by 4.8x.
        # XLF, whose two ATM quotes agree to 0.005, read 1.18 against 1.25 —
        # the two forms coincide only where parity holds, which is the tell.
        #
        # Same sign convention and same scale as the textbook form: this is the
        # sum of the two wing lifts, which is what `- 2*ATM` buys you when the
        # single ATM anchor is legitimate. Nothing downstream needs rescaling.
        butterfly = ((((p25_iv - atm_put_iv) + (c25_iv - front_iv)) * 100)
                     if p25_iv and c25_iv and atm_put_iv and front_iv > 0 else None)

        # Term structure slope
        front_dte = max((pd.to_datetime(exps[0]) - pd.Timestamp.now()).days, 1)
        # Same expiry the back IV was read from, or the slope divides a move
        # measured over one span by the length of a different one.
        back_dte = max((pd.to_datetime(exps[back_idx]) - pd.Timestamp.now()).days, 1)
        ts_slope = (back_iv - front_iv) / max(back_dte - front_dte, 1) * 30  # per month
        # Published so the tenor the slope was measured over is checkable rather
        # than assumed — the assumption that it was the same for every ticker is
        # what made n_inverted an incoherent count.
        ts_span_days = back_dte - front_dte

        # VRP & IV/HV
        iv_hv = (front_iv / hv20) if hv20 and hv20 > 0 else None
        vrp = (front_iv ** 2 - hv20 ** 2) * 100 if hv20 and hv20 > 0 else None

        # Implied move
        impl_move = 0
        try:
            atm_calls = front_chain[(front_chain["contract_type"] == "call")].reset_index(drop=True)
            atm_puts = front_chain[(front_chain["contract_type"] == "put")].reset_index(drop=True)
            if not atm_calls.empty and not atm_puts.empty:
                c_row = atm_calls.loc[(atm_calls["strike_price"] - spot).abs().idxmin()]
                p_row = atm_puts.loc[(atm_puts["strike_price"] - spot).abs().idxmin()]
                c_mid = ((c_row.get("bid", 0) or 0) + (c_row.get("ask", 0) or 0)) / 2
                p_mid = ((p_row.get("bid", 0) or 0) + (p_row.get("ask", 0) or 0)) / 2
                # Fallback to last_price if mid is zero (wide/stale quotes)
                if c_mid <= 0:
                    c_mid = c_row.get("last_price", 0) or 0
                if p_mid <= 0:
                    p_mid = p_row.get("last_price", 0) or 0
                if c_mid > 0 and p_mid > 0:
                    impl_move = (c_mid + p_mid) * 0.798 / spot * 100
        except Exception:
            pass

        # P/C ratio
        try:
            put_vol = front_chain[front_chain["contract_type"] == "put"]["volume"].sum()
            call_vol = front_chain[front_chain["contract_type"] == "call"]["volume"].sum()
            pc_ratio = put_vol / call_vol if call_vol > 0 else 1.0
        except Exception:
            pc_ratio = 1.0

        # IV percentile — use IV/HV ratio method for universe scans (fast)
        # Historical IV percentile from options_history is too slow for 20-ticker batch
        # (each ticker requires ~200 Polygon API calls). Use it on single-ticker pages only.
        iv_pctile = None
        try:
            px_df = td.get("px_df")
            if px_df is not None and len(px_df) > 60 and hv20 and hv20 > 0:
                hv_series = (px_df["Close"].pct_change().rolling(20).std() * np.sqrt(252)).dropna()
                hv_vals = hv_series.values[hv_series.values > 0]
                if len(hv_vals) > 10:
                    iv_hv_history = front_iv / hv_vals
                    current_iv_hv = front_iv / hv20
                    iv_pctile = float((iv_hv_history < current_iv_hv).mean() * 100)
        except Exception:
            pass

        # VRP in vol terms (more intuitive: IV - HV, not variance)
        vrp_vol = (front_iv - hv20) if hv20 and hv20 > 0 else None

        # Front month DTE for context
        front_dte_val = front_dte

        rows.append({
            "Ticker": tk, "Label": label, "Group": group, "Spot": spot,
            "Front_IV": front_iv, "Back_IV": back_iv, "IV_HV": iv_hv,
            "Put_Skew": put_skew, "Risk_Rev": risk_rev, "Butterfly": butterfly,
            "Parity": parity, "Ladder_Broken": ladder_broken, "TS_Span_Days": ts_span_days,
            "TS_Slope": ts_slope, "VRP": vrp, "VRP_Vol": vrp_vol,
            "Impl_Move": impl_move, "HV20": hv20,
            "PC_Ratio": pc_ratio, "IV_Pctile": iv_pctile, "Front_DTE": front_dte_val,
        })

    return pd.DataFrame(rows)


# ── Divergence Detection ─────────────────────────────────────────────────────

# Known correlated pairs — when their vol profiles diverge, it's a signal
CORRELATED_PAIRS = [
    ("XLE", "USO", "Energy sector vs crude oil"),
    ("XLK", "QQQ", "Tech sector vs Nasdaq"),
    ("XLF", "TLT", "Financials vs long bonds (inverse)"),
    ("GLD", "TLT", "Gold vs bonds (safe havens)"),
    ("SPY", "IWM", "Large cap vs small cap"),
    ("XLE", "GLD", "Energy vs gold (inflation hedge)"),
    ("EEM", "EFA", "Emerging vs developed markets"),
    ("XLY", "XLP", "Consumer discretionary vs staples (risk-on/off)"),
    ("SPY", "HYG", "Equities vs high yield (credit risk)"),
]


def detect_divergences(mdf, top_n=5):
    """Find correlated pairs with divergent vol profiles.

    Returns list of dicts: {pair, ticker_a, ticker_b, description, metric, a_val, b_val, spread, signal}
    """
    results = []
    tickers_in_data = set(mdf["Ticker"].values)

    for tk_a, tk_b, desc in CORRELATED_PAIRS:
        if tk_a not in tickers_in_data or tk_b not in tickers_in_data:
            continue
        a = mdf[mdf["Ticker"] == tk_a].iloc[0]
        b = mdf[mdf["Ticker"] == tk_b].iloc[0]

        # Check IV/HV divergence
        if pd.notna(a.get("IV_HV")) and pd.notna(b.get("IV_HV")):
            spread = abs(a["IV_HV"] - b["IV_HV"])
            if spread > 0.3:
                richer = tk_a if a["IV_HV"] > b["IV_HV"] else tk_b
                cheaper = tk_b if richer == tk_a else tk_a
                results.append({
                    "pair": f"{tk_a}/{tk_b}", "ticker_a": tk_a, "ticker_b": tk_b,
                    "description": desc, "metric": "IV/HV",
                    "a_val": a["IV_HV"], "b_val": b["IV_HV"], "spread": spread,
                    "signal": f"{richer} vol is rich ({a['IV_HV'] if richer == tk_a else b['IV_HV']:.2f}x) while {cheaper} is cheap ({b['IV_HV'] if richer == tk_a else a['IV_HV']:.2f}x)",
                })

        # Check skew divergence.
        #
        # Guarded twice. Put_Skew is None when a chain could not be measured, and
        # `abs(None - 1.2)` is a TypeError that would only ever fire on the
        # degraded path — the exact case the None was introduced to represent.
        # Parity is checked for the same reason the ES credit read checks it: a
        # comparison between two skews is only as good as the worse chain, and a
        # stale chain would otherwise be published as "fear is concentrated
        # there". The IV/HV block above already guards with notna; this did not.
        _sk_ok = (pd.notna(a.get("Put_Skew")) and pd.notna(b.get("Put_Skew"))
                  and all(pd.isna(r.get("Parity")) or (0.75 <= r["Parity"] <= 1.35)
                          for r in (a, b)))
        skew_spread = abs(a["Put_Skew"] - b["Put_Skew"]) if _sk_ok else 0.0
        if _sk_ok and skew_spread > 0.08:
            steeper = tk_a if a["Put_Skew"] > b["Put_Skew"] else tk_b
            results.append({
                "pair": f"{tk_a}/{tk_b}", "ticker_a": tk_a, "ticker_b": tk_b,
                "description": desc, "metric": "Skew",
                "a_val": a["Put_Skew"], "b_val": b["Put_Skew"], "spread": skew_spread,
                "signal": f"{steeper} has much steeper skew ({a['Put_Skew'] if steeper == tk_a else b['Put_Skew']:.2f}x) — fear is concentrated there",
            })

        # Check term structure divergence (one inverted, other not)
        if (pd.notna(a.get("TS_Slope")) and pd.notna(b.get("TS_Slope"))
                and a["TS_Slope"] * b["TS_Slope"] < 0):  # opposite signs
            inverted = tk_a if a["TS_Slope"] < 0 else tk_b
            results.append({
                "pair": f"{tk_a}/{tk_b}", "ticker_a": tk_a, "ticker_b": tk_b,
                "description": desc, "metric": "Term Structure",
                "a_val": a["TS_Slope"], "b_val": b["TS_Slope"], "spread": abs(a["TS_Slope"] - b["TS_Slope"]),
                "signal": f"{inverted} is inverted (backwardation) while its pair is in contango — event risk is asset-specific, not broad",
            })

    # Sort by spread magnitude, return top N
    results.sort(key=lambda x: x["spread"], reverse=True)
    return results[:top_n]


def compute_metric_changes(current_mdf, previous_mdf):
    """Compute changes between two metric snapshots.

    Returns DataFrame with _chg suffix columns merged onto current_mdf.
    """
    if previous_mdf is None or previous_mdf.empty:
        return current_mdf

    change_cols = ["Front_IV", "Put_Skew", "IV_HV", "TS_Slope", "VRP_Vol"]
    prev = previous_mdf[["Ticker"] + [c for c in change_cols if c in previous_mdf.columns]].copy()
    prev.columns = ["Ticker"] + [f"{c}_prev" for c in change_cols if c in previous_mdf.columns]

    merged = current_mdf.merge(prev, on="Ticker", how="left")
    for c in change_cols:
        prev_col = f"{c}_prev"
        chg_col = f"{c}_chg"
        if prev_col in merged.columns and c in merged.columns:
            merged[chg_col] = merged[c] - merged[prev_col]

    # Drop prev columns
    merged = merged.drop(columns=[c for c in merged.columns if c.endswith("_prev")], errors="ignore")
    return merged


def compute_correlation_matrix(ticker_data, min_days=60):
    """Build pairwise return correlation matrix from price histories.

    Returns (corr_df, tickers_used) or (None, []).
    """
    returns = {}
    for tk, td in ticker_data.items():
        px = td.get("px_df")
        if px is not None and len(px) > min_days:
            rets = px["Close"].pct_change().dropna()
            if len(rets) > min_days:
                returns[tk] = rets.values[-min_days:]

    if len(returns) < 3:
        return None, []

    # Align lengths
    min_len = min(len(v) for v in returns.values())
    aligned = {tk: v[-min_len:] for tk, v in returns.items()}
    tickers = sorted(aligned.keys())
    matrix = np.array([aligned[tk] for tk in tickers])
    corr = np.corrcoef(matrix)
    corr_df = pd.DataFrame(corr, index=tickers, columns=tickers)
    return corr_df, tickers


def fetch_earnings_dates(tickers):
    """Fetch next earnings date for multiple tickers via yfinance.

    Returns dict: {ticker: {"date": date_str, "days": int}} for tickers with earnings within 60 days.
    """
    import yfinance as yf
    from datetime import datetime as dt

    result = {}
    today = dt.now().date()

    for tk in tickers:
        try:
            info = yf.Ticker(tk).info or {}
            ts = info.get("earningsTimestampStart")
            if ts and ts > 0:
                ed = dt.utcfromtimestamp(ts).date()
                days = (ed - today).days
                if 0 < days <= 60:
                    result[tk] = {"date": ed.isoformat(), "days": days}
        except Exception:
            continue

    return result


def compute_benchmark_context(ticker_data, mdf):
    """Compare current metrics to 30-day rolling HV averages for context.

    Returns dict: {ticker: {metric: {current, avg_30d, pct_change}}}
    """
    benchmarks = {}
    for tk, td in ticker_data.items():
        px = td.get("px_df")
        if px is None or len(px) < 60:
            continue
        rets = px["Close"].pct_change().dropna()
        hv_series = (rets.rolling(20).std() * np.sqrt(252)).dropna()
        if len(hv_series) < 30:
            continue

        hv_30d_avg = float(hv_series.tail(30).mean())
        hv_current = float(hv_series.iloc[-1]) if len(hv_series) > 0 else None

        row = mdf[mdf["Ticker"] == tk]
        if row.empty:
            continue
        r = row.iloc[0]
        front_iv = r.get("Front_IV", 0)

        benchmarks[tk] = {
            "hv_30d_avg": hv_30d_avg,
            "hv_current": hv_current,
            "iv_vs_hv30d": ((front_iv / hv_30d_avg - 1) * 100) if hv_30d_avg > 0 and front_iv > 0 else None,
        }

    return benchmarks
