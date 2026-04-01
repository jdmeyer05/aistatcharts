"""Scanner endpoints — Iron Condor and Calendar Spread scanning."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from api.deps import get_current_user

router = APIRouter()


class ICScanRequest(BaseModel):
    tickers: list[str] = ["SPY", "QQQ", "IWM", "DIA", "AAPL", "TSLA", "NVDA", "AMD",
                           "AMZN", "META", "MSFT", "GOOGL", "NFLX", "GLD", "SMH",
                           "XLF", "TLT", "EEM", "JPM", "BA"]
    dte_min: int = 7
    dte_max: int = 90
    short_delta: float = 0.25
    wing_width: int = 10
    profit_target_pct: int = 50
    stop_multiplier: float = 1.5
    account_size: int = 25000
    max_risk_pct: float = 5.0
    kelly_fraction: float = 0.5
    win_rate_bump: int = 12


@router.post("/iron-condor")
def scan_iron_condors(req: ICScanRequest, user: str = Depends(get_current_user)):
    """Scan for iron condor setups. Takes 1-3 minutes depending on ticker count."""
    import numpy as np
    import pandas as pd
    from src.data_engine import fetch_options_chain, polygon_batch_snapshot, fetch_massive_data
    from src.options_models import black_scholes
    from src.metrics_store import save_snapshot, percentile_rank

    tickers = [t.strip().upper() for t in req.tickers if t.strip()]

    # Step 1: Batch snapshot
    snapshots = polygon_batch_snapshot(tickers)

    # Step 2: Fetch price histories + earnings in parallel
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import yfinance as yf
    from datetime import datetime as dt

    price_cache = {}
    earnings_cache = {}

    def _fetch_px(tk):
        try:
            return tk, fetch_massive_data(tk, 252)
        except Exception:
            return tk, None

    def _fetch_earn(tk):
        try:
            info = yf.Ticker(tk).info or {}
            ts = info.get("earningsTimestampStart")
            if ts and ts > 0:
                ed = dt.utcfromtimestamp(ts).date()
                days = (ed - dt.now().date()).days
                if 0 < days <= 90:
                    return tk, {"date": ed.isoformat(), "days": days}
        except Exception:
            pass
        return tk, None

    with ThreadPoolExecutor(max_workers=10) as ex:
        px_futs = {ex.submit(_fetch_px, tk): tk for tk in tickers}
        earn_futs = {ex.submit(_fetch_earn, tk): tk for tk in tickers}
        for fut in as_completed(list(px_futs.keys()) + list(earn_futs.keys())):
            if fut in px_futs:
                tk, px = fut.result()
                price_cache[tk] = px
            elif fut in earn_futs:
                tk, earn = fut.result()
                earnings_cache[tk] = earn

    # Pricing helpers
    def _mid(row):
        b = row.get("bid", 0) or 0
        a = row.get("ask", 0) or 0
        lp = row.get("last_price", 0) or 0
        return (b + a) / 2 if (b > 0 and a > 0) else (lp if lp > 0 else 0)

    def _bid(row):
        v = row.get("bid", 0) or 0
        return v if v > 0 else _mid(row)

    def _ask(row):
        v = row.get("ask", 0) or 0
        return v if v > 0 else _mid(row)

    # Step 3: Fetch all chains in parallel (no session_state in FastAPI = thread-safe)
    chain_cache = {}

    def _fetch_chain(tk):
        try:
            return tk, fetch_options_chain(tk)
        except Exception:
            return tk, None

    with ThreadPoolExecutor(max_workers=8) as ex:
        chain_futs = {ex.submit(_fetch_chain, tk): tk for tk in tickers}
        for fut in as_completed(chain_futs):
            tk, chain = fut.result()
            chain_cache[tk] = chain

    # Step 4: Process results (CPU-only, fast)
    results = []
    for tk in tickers:
        try:
            spot = snapshots.get(tk, {}).get("price")
            spot = round(spot, 2) if spot else 0
            chain = chain_cache.get(tk)
            if chain is None or chain.empty or not spot:
                continue

            chain_c = chain.copy()
            chain_c["dte"] = (pd.to_datetime(chain_c["expiration_date"]) - pd.Timestamp.now()).dt.days
            chain_c = chain_c[(chain_c["dte"] >= req.dte_min) & (chain_c["dte"] <= req.dte_max)]
            if chain_c.empty:
                continue

            target_dte = (req.dte_min + req.dte_max) // 2
            chain_c["dte_dist"] = abs(chain_c["dte"] - target_dte)
            best_exp = chain_c.loc[chain_c["dte_dist"].idxmin(), "expiration_date"]
            exp_chain = chain_c[chain_c["expiration_date"] == best_exp].copy()
            actual_dte = exp_chain["dte"].iloc[0]

            calls = exp_chain[exp_chain["contract_type"] == "call"].sort_values("strike_price")
            puts = exp_chain[exp_chain["contract_type"] == "put"].sort_values("strike_price")
            if calls.empty or puts.empty:
                continue

            # Find short strikes
            puts_otm = puts[puts["strike_price"] < spot].copy()
            puts_otm["abs_delta"] = puts_otm["delta"].abs()
            sp_cands = puts_otm[puts_otm["abs_delta"] <= req.short_delta + 0.05]
            if sp_cands.empty:
                sp_cands = puts_otm.tail(5)
            if sp_cands.empty:
                continue
            short_put = sp_cands.iloc[(sp_cands["abs_delta"] - req.short_delta).abs().argmin()]

            calls_otm = calls[calls["strike_price"] > spot].copy()
            calls_otm["abs_delta"] = calls_otm["delta"].abs()
            sc_cands = calls_otm[calls_otm["abs_delta"] <= req.short_delta + 0.05]
            if sc_cands.empty:
                sc_cands = calls_otm.head(5)
            if sc_cands.empty:
                continue
            short_call = sc_cands.iloc[(sc_cands["abs_delta"] - req.short_delta).abs().argmin()]

            sp_strike = short_put["strike_price"]
            sc_strike = short_call["strike_price"]

            # Find long legs
            lp_cands = puts[puts["strike_price"] <= sp_strike - req.wing_width + 0.01]
            lc_cands = calls[calls["strike_price"] >= sc_strike + req.wing_width - 0.01]
            if lp_cands.empty or lc_cands.empty:
                continue
            long_put = lp_cands.iloc[(lp_cands["strike_price"] - (sp_strike - req.wing_width)).abs().argmin()]
            long_call = lc_cands.iloc[(lc_cands["strike_price"] - (sc_strike + req.wing_width)).abs().argmin()]

            credit = (_mid(short_put) + _mid(short_call)) - (_mid(long_put) + _mid(long_call))
            if credit <= 0.01:
                continue

            spread_natural = (_bid(short_put) + _bid(short_call)) - (_ask(long_put) + _ask(long_call))
            max_width = max(abs(sp_strike - long_put["strike_price"]),
                           abs(long_call["strike_price"] - sc_strike))
            max_risk = max_width - credit
            if max_risk <= 0:
                continue

            sp_delta = abs(float(short_put.get("delta", 0) or 0)) or req.short_delta
            sc_delta = abs(float(short_call.get("delta", 0) or 0)) or req.short_delta
            pop = max(0, min(1, 1 - sp_delta - sc_delta))

            avg_iv = ((float(short_put.get("implied_volatility", 0) or 0) +
                       float(short_call.get("implied_volatility", 0) or 0)) / 2)

            # Liquidity
            min_oi = min(float(r.get("open_interest", 0) or 0)
                        for r in [short_put, short_call, long_put, long_call])
            max_ba = max((float(r.get("ask", 0) or 0) - float(r.get("bid", 0) or 0))
                        if float(r.get("ask", 0) or 0) > 0 and float(r.get("bid", 0) or 0) > 0 else 999
                        for r in [short_put, short_call, long_put, long_call])

            if min_oi >= 500 and max_ba <= 0.10:
                liq = "A"
            elif min_oi >= 100 and max_ba <= 0.30:
                liq = "B"
            elif min_oi >= 50 and max_ba <= 0.60:
                liq = "C"
            elif min_oi >= 10:
                liq = "D"
            else:
                liq = "F"

            # Fill estimate
            _fill_pct = {"A": 0.40, "B": 0.30, "C": 0.20, "D": 0.10, "F": 0.05}.get(liq, 0.15)
            mid_credit = credit
            if spread_natural < mid_credit:
                fill_est = spread_natural + (mid_credit - spread_natural) * _fill_pct
            else:
                fill_est = mid_credit

            # IVR
            current_iv = avg_iv
            ivr = None
            hv20 = None
            vrp = None
            px = price_cache.get(tk)
            if px is not None and len(px) > 30:
                hv_s = px["Close"].pct_change().rolling(20).std().dropna() * np.sqrt(252)
                if len(hv_s) > 0:
                    hv20 = round(float(hv_s.iloc[-1] * 100), 1)
                if hv20 and current_iv > 0:
                    vrp = round(current_iv * 100 - hv20, 1)

            if current_iv > 0:
                try:
                    ivr = percentile_rank(tk, "atm_iv", current_value=current_iv)
                except Exception:
                    pass
            if ivr is None and current_iv > 0 and px is not None and len(px) > 30:
                hv_vals = px["Close"].pct_change().rolling(20).std().dropna().values * np.sqrt(252)
                if len(hv_vals) >= 20:
                    ivr = round(float(np.sum(hv_vals <= current_iv) / len(hv_vals) * 100), 1)

            # Save to metrics store
            try:
                save_snapshot(tk, {"ticker": tk, "atm_iv": current_iv,
                                   "hv20": hv20 / 100 if hv20 else None,
                                   "vrp": vrp / 100 if vrp else None})
            except Exception:
                pass

            ivr_band = "N/A"
            if ivr is not None:
                ivr_band = "Low" if ivr < 30 else "Normal" if ivr < 50 else "Optimal" if ivr <= 75 else "Extreme"

            # Earnings
            earn = earnings_cache.get(tk)
            earnings_before = earn and earn["days"] <= actual_dte if earn else False

            # Score
            score = (credit / max_risk) * pop
            IVR_W = {"Optimal": 1.5, "Normal": 1.0, "Extreme": 0.7, "Low": 0.6, "N/A": 0.8}
            LIQ_W = {"A": 1.2, "B": 1.0, "C": 0.85, "D": 0.6, "F": 0.3}
            adj_score = score * IVR_W.get(ivr_band, 0.8) * LIQ_W.get(liq, 0.3)
            if earnings_before:
                adj_score *= 0.4
            if vrp and vrp > 0:
                adj_score *= (1.0 + vrp / 100)

            upper_be = sc_strike + credit
            lower_be = sp_strike - credit

            # Payoff curve (P&L at expiration across price range)
            lp_k = float(long_put["strike_price"])
            lc_k = float(long_call["strike_price"])
            payoff_prices = np.linspace(lp_k - 5, lc_k + 5, 100).tolist()
            payoff_pnl = []
            for px in payoff_prices:
                pnl = (credit - max(float(sp_strike) - px, 0) + max(lp_k - px, 0)
                       - max(px - float(sc_strike), 0) + max(px - lc_k, 0)) * 100
                payoff_pnl.append(round(pnl, 0))

            # Theta decay path (spread value over time via BS)
            rfr = 0.045
            sp_iv = float(short_put.get("implied_volatility", 0) or 0) or 0.20
            sc_iv = float(short_call.get("implied_volatility", 0) or 0) or 0.20
            lp_iv = float(long_put.get("implied_volatility", 0) or 0) or sp_iv
            lc_iv = float(long_call.get("implied_volatility", 0) or 0) or sc_iv
            decay_days = []
            decay_vals = []
            for day in range(0, int(actual_dte)):
                T_rem = (actual_dte - day) / 365
                if T_rem <= 1 / 365:
                    break
                sv = (black_scholes(spot, float(sp_strike), T_rem, rfr, sp_iv, "put")
                      + black_scholes(spot, float(sc_strike), T_rem, rfr, sc_iv, "call")
                      - black_scholes(spot, lp_k, T_rem, rfr, lp_iv, "put")
                      - black_scholes(spot, lc_k, T_rem, rfr, lc_iv, "call"))
                decay_days.append(day)
                decay_vals.append(round(sv * 100, 0))

            # Net Greeks (iron condor: short the shorts, long the longs)
            def _g(row, field):
                v = row.get(field, 0)
                try: return float(v) if v else 0.0
                except: return 0.0

            net_delta = (-_g(short_put, "delta") + _g(long_put, "delta")
                         - _g(short_call, "delta") + _g(long_call, "delta"))
            net_gamma = (-_g(short_put, "gamma") + _g(long_put, "gamma")
                         - _g(short_call, "gamma") + _g(long_call, "gamma"))
            net_theta = (-_g(short_put, "theta") + _g(long_put, "theta")
                         - _g(short_call, "theta") + _g(long_call, "theta"))
            net_vega = (-_g(short_put, "vega") + _g(long_put, "vega")
                        - _g(short_call, "vega") + _g(long_call, "vega"))
            theta_vega_ratio = abs(net_theta / net_vega) if net_vega != 0 else 0

            # Per-leg pricing
            legs = [
                {"label": f"{float(sp_strike):.0f}P (short)", "bid": round(_bid(short_put), 2),
                 "ask": round(_ask(short_put), 2), "mid": round(_mid(short_put), 2),
                 "delta": round(_g(short_put, "delta"), 3), "gamma": round(_g(short_put, "gamma"), 4),
                 "theta": round(_g(short_put, "theta"), 3), "vega": round(_g(short_put, "vega"), 3),
                 "oi": int(float(short_put.get("open_interest", 0) or 0)),
                 "vol": int(float(short_put.get("volume", 0) or 0)),
                 "live": bool(short_put.get("quote_live", True))},
                {"label": f"{lp_k:.0f}P (long)", "bid": round(_bid(long_put), 2),
                 "ask": round(_ask(long_put), 2), "mid": round(_mid(long_put), 2),
                 "delta": round(_g(long_put, "delta"), 3), "gamma": round(_g(long_put, "gamma"), 4),
                 "theta": round(_g(long_put, "theta"), 3), "vega": round(_g(long_put, "vega"), 3),
                 "oi": int(float(long_put.get("open_interest", 0) or 0)),
                 "vol": int(float(long_put.get("volume", 0) or 0)),
                 "live": bool(long_put.get("quote_live", True))},
                {"label": f"{float(sc_strike):.0f}C (short)", "bid": round(_bid(short_call), 2),
                 "ask": round(_ask(short_call), 2), "mid": round(_mid(short_call), 2),
                 "delta": round(_g(short_call, "delta"), 3), "gamma": round(_g(short_call, "gamma"), 4),
                 "theta": round(_g(short_call, "theta"), 3), "vega": round(_g(short_call, "vega"), 3),
                 "oi": int(float(short_call.get("open_interest", 0) or 0)),
                 "vol": int(float(short_call.get("volume", 0) or 0)),
                 "live": bool(short_call.get("quote_live", True))},
                {"label": f"{lc_k:.0f}C (long)", "bid": round(_bid(long_call), 2),
                 "ask": round(_ask(long_call), 2), "mid": round(_mid(long_call), 2),
                 "delta": round(_g(long_call, "delta"), 3), "gamma": round(_g(long_call, "gamma"), 4),
                 "theta": round(_g(long_call, "theta"), 3), "vega": round(_g(long_call, "vega"), 3),
                 "oi": int(float(long_call.get("open_interest", 0) or 0)),
                 "vol": int(float(long_call.get("volume", 0) or 0)),
                 "live": bool(long_call.get("quote_live", True))},
            ]

            # Kelly position sizing
            managed_wr = min(0.95, pop + req.win_rate_bump / 100)
            q = 1 - managed_wr
            profit_at_target = credit * (req.profit_target_pct / 100) * 100
            stop_loss_amt = min(credit * req.stop_multiplier, max_risk) * 100
            if stop_loss_amt > 0 and profit_at_target > 0:
                b = profit_at_target / stop_loss_amt
                full_kelly = (managed_wr * b - q) / b if b > 0 else 0
                adj_kelly = max(0, full_kelly) * req.kelly_fraction
                capped_pct = min(adj_kelly, req.max_risk_pct / 100) if adj_kelly > 0 else req.max_risk_pct / 100
                contracts = int(req.account_size * capped_pct / max(stop_loss_amt, 1))
            else:
                full_kelly = adj_kelly = capped_pct = 0
                contracts = 0

            # 30-delta triggers
            from scipy.stats import norm as _norm
            def _find_trigger(strike, iv, dte_d, opt_type, target_d=0.30):
                T = dte_d / 365
                if T <= 0 or iv <= 0: return float(strike)
                lo, hi = strike * 0.70, strike * 1.30
                for _ in range(40):
                    m = (lo + hi) / 2
                    d1 = (np.log(m / strike) + (rfr + iv**2 / 2) * T) / (iv * np.sqrt(T))
                    da = abs(_norm.cdf(d1) - 1) if opt_type == "put" else _norm.cdf(d1)
                    if da < target_d:
                        if opt_type == "put": hi = m
                        else: lo = m
                    else:
                        if opt_type == "put": lo = m
                        else: hi = m
                return round((lo + hi) / 2, 2)

            put_30d = _find_trigger(float(sp_strike), sp_iv, actual_dte, "put")
            call_30d = _find_trigger(float(sc_strike), sc_iv, actual_dte, "call")

            results.append({
                "ticker": tk,
                "expiration": best_exp,
                "dte": int(actual_dte),
                "spot": round(spot, 2),
                "short_put": float(sp_strike),
                "long_put": float(long_put["strike_price"]),
                "short_call": float(sc_strike),
                "long_call": float(long_call["strike_price"]),
                "credit": round(credit * 100, 0),
                "fill_estimate": round(fill_est * 100, 0),
                "natural": round(spread_natural * 100, 0),
                "mid": round(mid_credit * 100, 0),
                "max_risk": round(max_risk * 100, 0),
                "pop": round(pop * 100, 1),
                "avg_iv": round(avg_iv * 100, 1),
                "ivr": round(ivr, 1) if ivr is not None else None,
                "ivr_band": ivr_band,
                "vrp": vrp,
                "hv20": hv20,
                "liq_grade": liq,
                "min_oi": int(min_oi),
                "max_ba": round(max_ba, 2) if max_ba < 900 else None,
                "upper_be": round(upper_be, 2),
                "lower_be": round(lower_be, 2),
                "earnings_before": bool(earnings_before),
                "earnings_days": earn["days"] if earn else None,
                "adj_score": round(adj_score, 4),
                "n_synthetic": sum(1 for r in [short_put, short_call, long_put, long_call]
                                   if not r.get("quote_live", True)),
                # Greeks
                "net_delta": round(net_delta, 4),
                "net_gamma": round(net_gamma, 4),
                "net_theta": round(net_theta, 4),
                "net_vega": round(net_vega, 4),
                "theta_vega_ratio": round(theta_vega_ratio, 3),
                "sp_delta": round(sp_delta, 3),
                "sc_delta": round(sc_delta, 3),
                # Per-leg
                "legs": legs,
                # Kelly
                "managed_wr": round(managed_wr * 100, 1),
                "kelly_full": round(full_kelly * 100, 1),
                "kelly_adj": round(adj_kelly * 100, 1),
                "contracts": contracts,
                "total_risk": round(stop_loss_amt * contracts, 0),
                "total_credit": round(fill_est * 100 * contracts, 0),
                # Adjustment triggers
                "put_30d_trigger": put_30d,
                "call_30d_trigger": call_30d,
                # Management
                "profit_target_pct": req.profit_target_pct,
                "stop_multiplier": req.stop_multiplier,
                "target_credit": round(credit * (req.profit_target_pct / 100) * 100, 0),
                "stop_loss_amt": round(stop_loss_amt, 0),
                # Chart data
                "payoff_prices": payoff_prices,
                "payoff_pnl": payoff_pnl,
                "decay_days": decay_days,
                "decay_vals": decay_vals,
            })
        except Exception as e:
            continue

    # Sort by score
    results.sort(key=lambda r: r["adj_score"], reverse=True)

    # Convert numpy types to native Python (FastAPI can't serialize numpy.float64, numpy.bool_, etc.)
    def _to_native(v):
        if isinstance(v, (np.integer,)): return int(v)
        if isinstance(v, (np.floating,)): return float(v)
        if isinstance(v, (np.bool_,)): return bool(v)
        return v

    clean_results = [{k: _to_native(v) for k, v in r.items()} for r in results]

    return {"count": len(clean_results), "results": clean_results}
