"""Scanner endpoints — Iron Condor and Calendar Spread scanning."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from api._json_safe import df_records
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


# ── Historical Win Rate Simulation ──────────────────────────────────

def _compute_historical_winrate(px, spot: float, sp_strike: float, sc_strike: float,
                                credit: float, max_risk: float, dte: int,
                                profit_target_pct: int = 50, stop_mult: float = 1.5):
    """Simulate managed iron condor trades over 252 days of price history.

    For each historical entry point, simulates a DTE-day condor with three exit paths:
      1. Profit target hit (spread value decays via theta approximation)
      2. Stop loss hit (unrealized loss exceeds stop_mult × credit)
      3. Held to expiration (price in range or breached)

    Returns dict with managed WR, exp-only WR, early/stopped/breached counts.
    """
    import numpy as np
    if px is None or len(px) < dte + 30:
        return None
    closes = px["Close"].values
    n = len(closes)
    if n < dte + 10:
        return None

    put_dist_pct = (spot - sp_strike) / spot if spot > 0 else 0
    call_dist_pct = (sc_strike - spot) / spot if spot > 0 else 0
    credit_pct = credit / spot if spot > 0 else 0
    target_pct = profit_target_pct / 100

    wins_managed = 0
    exp_in_range = 0
    losses_stop = 0
    losses_breach = 0
    early_profit = 0
    max_moves = []

    for i in range(n - dte):
        entry_price = closes[i]
        if entry_price <= 0:
            continue
        hist_sp = entry_price * (1 - put_dist_pct)
        hist_sc = entry_price * (1 + call_dist_pct)
        hist_credit = entry_price * credit_pct
        stop_level = hist_credit * stop_mult

        window = closes[i + 1: i + dte + 1]
        if len(window) < 2:
            continue

        max_move = max(abs(window.max() - entry_price),
                       abs(entry_price - window.min())) / entry_price * 100
        max_moves.append(max_move)

        final_px = window[-1]
        if hist_sp <= final_px <= hist_sc:
            exp_in_range += 1

        outcome = None
        for d, px_d in enumerate(window):
            put_loss = max(hist_sp - px_d, 0)
            call_loss = max(px_d - hist_sc, 0)
            intrinsic_loss = put_loss + call_loss
            days_held = d + 1
            theta_fraction = days_held / dte
            spread_value_approx = hist_credit * (1 - theta_fraction * 0.7)

            if intrinsic_loss > 0:
                unrealized_pnl = hist_credit - intrinsic_loss
            else:
                unrealized_pnl = hist_credit - spread_value_approx

            if unrealized_pnl >= hist_credit * target_pct:
                outcome = "early_profit"
                break
            if unrealized_pnl <= -stop_level:
                outcome = "stop_loss"
                break

        if outcome is None:
            if hist_sp <= final_px <= hist_sc:
                outcome = "exp_win"
            else:
                outcome = "exp_loss"

        if outcome == "early_profit":
            wins_managed += 1
            early_profit += 1
        elif outcome == "exp_win":
            wins_managed += 1
        elif outcome == "stop_loss":
            losses_stop += 1
        elif outcome == "exp_loss":
            losses_breach += 1

    total = wins_managed + losses_stop + losses_breach
    if total < 10:
        return None

    return {
        "win_rate": round(wins_managed / total * 100, 1),
        "exp_win_rate": round(exp_in_range / max(total, 1) * 100, 1),
        "n_trials": total,
        "early_profit": early_profit,
        "stopped_out": losses_stop,
        "breached_at_exp": losses_breach,
        "avg_max_move_pct": round(float(np.mean(max_moves)), 1) if max_moves else 0,
        "median_max_move_pct": round(float(np.median(max_moves)), 1) if max_moves else 0,
    }


# ── Alternative Expirations ─────────────────────────────────────────

def _find_alt_expirations(chain, spot: float, dte_min: int, dte_max: int,
                          target_delta: float, width: float, best_exp: str,
                          profit_target: int = 50):
    """Find condor summaries for up to 3 alternative expirations."""
    import pandas as pd
    if chain is None or chain.empty or not spot or spot <= 0:
        return []

    chain = chain.copy()
    if "dte" not in chain.columns:
        chain["dte"] = (pd.to_datetime(chain["expiration_date"]) - pd.Timestamp.now()).dt.days
    chain = chain[(chain["dte"] >= dte_min) & (chain["dte"] <= dte_max)]
    if chain.empty:
        return []

    all_exps = chain[chain["expiration_date"] != best_exp]["expiration_date"].unique()
    if len(all_exps) == 0:
        return []

    exp_dte = []
    for exp in all_exps:
        dte_val = int(chain[chain["expiration_date"] == exp]["dte"].iloc[0])
        exp_dte.append((exp, dte_val))
    exp_dte.sort(key=lambda x: x[1])

    if len(exp_dte) <= 3:
        selected = exp_dte
    else:
        mid_idx = len(exp_dte) // 2
        selected = [exp_dte[0], exp_dte[mid_idx], exp_dte[-1]]

    def _mid(row):
        b = row.get("bid", 0) or 0
        a = row.get("ask", 0) or 0
        lp = row.get("last_price", 0) or 0
        mid = (b + a) / 2 if (b > 0 and a > 0) else lp
        return mid if mid > 0 else 0

    alts = []
    for exp, dte_val in selected:
        exp_chain = chain[chain["expiration_date"] == exp]
        calls = exp_chain[exp_chain["contract_type"] == "call"].sort_values("strike_price")
        puts = exp_chain[exp_chain["contract_type"] == "put"].sort_values("strike_price")
        if calls.empty or puts.empty:
            continue

        puts_otm = puts[puts["strike_price"] < spot].copy()
        if puts_otm.empty:
            continue
        puts_otm["abs_delta"] = puts_otm["delta"].abs()
        sp_cands = puts_otm[puts_otm["abs_delta"] <= target_delta + 0.05]
        if sp_cands.empty:
            sp_cands = puts_otm.tail(3)
        if sp_cands.empty:
            continue
        sp_row = sp_cands.iloc[(sp_cands["abs_delta"] - target_delta).abs().argmin()]

        calls_otm = calls[calls["strike_price"] > spot].copy()
        if calls_otm.empty:
            continue
        calls_otm["abs_delta"] = calls_otm["delta"].abs()
        sc_cands = calls_otm[calls_otm["abs_delta"] <= target_delta + 0.05]
        if sc_cands.empty:
            sc_cands = calls_otm.head(3)
        if sc_cands.empty:
            continue
        sc_row = sc_cands.iloc[(sc_cands["abs_delta"] - target_delta).abs().argmin()]

        sp_k = float(sp_row["strike_price"])
        sc_k = float(sc_row["strike_price"])

        lp_target = sp_k - width
        lc_target = sc_k + width
        lp_rows = puts[puts["strike_price"] <= lp_target + 0.01]
        lc_rows = calls[calls["strike_price"] >= lc_target - 0.01]
        if lp_rows.empty or lc_rows.empty:
            continue
        lp_row = lp_rows.iloc[(lp_rows["strike_price"] - lp_target).abs().argmin()]
        lc_row = lc_rows.iloc[(lc_rows["strike_price"] - lc_target).abs().argmin()]
        if (sp_k - float(lp_row["strike_price"])) > width * 3:
            continue
        if (float(lc_row["strike_price"]) - sc_k) > width * 3:
            continue

        credit = (_mid(sp_row) + _mid(sc_row)) - (_mid(lp_row) + _mid(lc_row))
        if credit <= 0.01:
            continue
        max_w = max(abs(sp_k - float(lp_row["strike_price"])),
                    abs(float(lc_row["strike_price"]) - sc_k))
        max_risk = max_w - credit
        if max_risk <= 0:
            continue

        sp_d = abs(float(sp_row.get("delta", 0) or 0)) or target_delta
        sc_d = abs(float(sc_row.get("delta", 0) or 0)) or target_delta
        pop = max(0, min(1, 1 - sp_d - sc_d))

        alts.append({
            "exp": exp,
            "dte": dte_val,
            "strikes": f"{sp_k:.0f}P/{sc_k:.0f}C",
            "credit": round(credit * 100, 0),
            "credit_per_day": round(credit * 100 / max(dte_val, 1), 1),
            "max_risk": round(max_risk * 100, 0),
            "pop": round(pop * 100, 1),
        })

    return alts


# ── Forward Event Stress Test ───────────────────────────────────────

def _compute_stress_test(spot: float, sp_strike: float, sc_strike: float,
                         lp_strike: float, lc_strike: float, credit: float,
                         stop_loss_amt: float, dte: int, expiration: str,
                         avg_iv: float, earnings_cache_entry):
    """Compute forward event stress test for FOMC meetings + earnings within DTE window."""
    import pandas as pd
    from src.economic_calendar import FOMC_DATES, FOMC_SEP_DATES

    exp_date = pd.to_datetime(expiration)
    now = pd.Timestamp.now()
    scenarios = []

    # P&L helper: accounts for long leg protection (defined-risk)
    def _ic_pnl(shocked_price):
        # Put spread: short put - long put
        put_loss = max(sp_strike - shocked_price, 0) - max(lp_strike - shocked_price, 0)
        # Call spread: short call - long call
        call_loss = max(shocked_price - sc_strike, 0) - max(shocked_price - lc_strike, 0)
        return round((credit - put_loss - call_loss) * 100, 0)

    # FOMC events within DTE window
    for fd in FOMC_DATES:
        fdt = pd.to_datetime(fd)
        if now < fdt <= exp_date:
            is_sep = fd in FOMC_SEP_DATES
            base_move_pct = 1.5 if is_sep else 1.0
            event_name = f"FOMC{' + SEP' if is_sep else ''}"
            days_away = (fdt - now).days

            for sigma, label in [(1, "1σ"), (2, "2σ"), (3, "3σ")]:
                move_pct = base_move_pct * sigma / 100
                for direction in ["down", "up"]:
                    shocked = spot * (1 - move_pct) if direction == "down" else spot * (1 + move_pct)
                    pnl = _ic_pnl(shocked)
                    survives = pnl > -stop_loss_amt

                    scenarios.append({
                        "event": event_name,
                        "date": fd,
                        "days_away": days_away,
                        "scenario": f"{label} {direction}",
                        "move_pct": round(move_pct * 100, 2),
                        "pnl": pnl,
                        "survives": survives,
                    })

    # Earnings event
    if earnings_cache_entry and earnings_cache_entry.get("days"):
        earn_days = earnings_cache_entry["days"]
        earn_date = earnings_cache_entry["date"]
        if earn_days <= dte and earn_days > 0:
            # Expected move from IV
            em_pct = avg_iv / 100 * (1 / 252) ** 0.5 if avg_iv > 0 else 0.02
            for sigma, label in [(1, "1σ"), (2, "2σ"), (3, "3σ")]:
                move_pct = em_pct * sigma
                for direction in ["down", "up"]:
                    shocked = spot * (1 - move_pct) if direction == "down" else spot * (1 + move_pct)
                    pnl = _ic_pnl(shocked)
                    survives = pnl > -stop_loss_amt

                    scenarios.append({
                        "event": f"Earnings",
                        "date": earn_date,
                        "days_away": earn_days,
                        "scenario": f"{label} {direction}",
                        "move_pct": round(move_pct * 100, 2),
                        "pnl": pnl,
                        "survives": survives,
                    })

    return scenarios


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

            # ── Historical Win Rate Backtest ──
            _px = price_cache.get(tk)
            hist_wr_data = _compute_historical_winrate(
                _px, spot, float(sp_strike), float(sc_strike),
                credit, max_risk, int(actual_dte),
                req.profit_target_pct, req.stop_multiplier)

            # Kelly position sizing — use historical WR if available
            if hist_wr_data and hist_wr_data["win_rate"] > 0:
                managed_wr = min(0.95, hist_wr_data["win_rate"] / 100)
            else:
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

            # ── EV per contract ──
            ev_per_contract = round((credit * pop - max_risk * (1 - pop)) * 100, 0)

            # ── Days to target (via BS forward pricing, sampled every 3 days) ──
            days_to_target = int(actual_dte)
            lp_k = float(long_put["strike_price"])
            lc_k = float(long_call["strike_price"])
            target_debit = credit * (1 - req.profit_target_pct / 100)
            for day in range(1, int(actual_dte), 3):
                T_rem = (actual_dte - day) / 365
                if T_rem <= 1 / 365:
                    break
                sv = (black_scholes(spot, float(sp_strike), T_rem, rfr, sp_iv, "put")
                      + black_scholes(spot, float(sc_strike), T_rem, rfr, sc_iv, "call")
                      - black_scholes(spot, lp_k, T_rem, rfr, lp_iv, "put")
                      - black_scholes(spot, lc_k, T_rem, rfr, lc_iv, "call"))
                if sv <= target_debit:
                    days_to_target = max(1, day - 1)
                    break

            # ── Breakeven % from spot ──
            upper_be_pct = round((upper_be - spot) / spot * 100, 1) if spot > 0 else 0
            lower_be_pct = round((spot - lower_be) / spot * 100, 1) if spot > 0 else 0

            # ── Wing width context ──
            wing_pct = round(req.wing_width / spot * 100, 1) if spot > 0 else 0

            # ── Alternative expirations ──
            alt_chain = chain_cache.get(tk)
            alt_exps = _find_alt_expirations(
                alt_chain, spot, req.dte_min, req.dte_max,
                req.short_delta, req.wing_width, best_exp, req.profit_target_pct)

            # ── Forward event stress test ──
            earn_entry = earnings_cache.get(tk)
            stress_test = _compute_stress_test(
                spot, float(sp_strike), float(sc_strike), lp_k, lc_k,
                credit, stop_loss_amt, int(actual_dte), best_exp,
                avg_iv * 100, earn_entry)

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
                "upper_be_pct": upper_be_pct,
                "lower_be_pct": lower_be_pct,
                "earnings_before": bool(earnings_before),
                "earnings_days": earn["days"] if earn else None,
                "adj_score": round(adj_score, 4),
                "n_synthetic": sum(1 for r in [short_put, short_call, long_put, long_call]
                                   if not r.get("quote_live", True)),
                "ev_per_contract": ev_per_contract,
                "wing_pct": wing_pct,
                "days_to_target": days_to_target,
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
                # Historical backtest
                "hist_winrate": hist_wr_data,
                # Alternative expirations
                "alt_expirations": alt_exps,
                # Forward event stress test
                "stress_test": stress_test,
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
        if isinstance(v, dict): return {k2: _to_native(v2) for k2, v2 in v.items()}
        if isinstance(v, list): return [_to_native(i) for i in v]
        return v

    clean_results = [{k: _to_native(v) for k, v in r.items()} for r in results]

    return {"count": len(clean_results), "results": clean_results}


# ═══════════════════════════════════════════════════════════════
# VERTICAL SPREAD SCANNER
# ═══════════════════════════════════════════════════════════════

class VSScanRequest(BaseModel):
    tickers: list[str] = ["SPY", "QQQ", "IWM", "DIA", "AAPL", "TSLA", "NVDA", "AMD",
                           "AMZN", "META", "MSFT", "GOOGL", "NFLX", "GLD", "SMH",
                           "XLF", "TLT", "EEM", "JPM", "BA"]
    spread_types: list[str] = ["bull_put", "bear_call", "bull_call", "bear_put"]
    dte_min: int = 7
    dte_max: int = 90
    short_delta: float = 0.30
    width: int = 5
    profit_target_pct: int = 50
    stop_multiplier: float = 1.5
    account_size: int = 25000
    max_risk_pct: float = 5.0
    kelly_fraction: float = 0.5
    win_rate_bump: int = 12


@router.post("/vertical-spread")
def scan_vertical_spreads(req: VSScanRequest, user: str = Depends(get_current_user)):
    """Scan for vertical spread setups: bull put, bear call, bull call, bear put."""
    import numpy as np
    import pandas as pd
    from src.data_engine import fetch_options_chain, polygon_batch_snapshot, fetch_massive_data
    from src.options_models import black_scholes
    from src.metrics_store import save_snapshot, percentile_rank
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import yfinance as yf
    from datetime import datetime as dt

    tickers = [t.strip().upper() for t in req.tickers if t.strip()]

    # Step 1: Batch snapshot + parallel data fetch
    snapshots = polygon_batch_snapshot(tickers)
    price_cache, earnings_cache, chain_cache = {}, {}, {}

    def _fetch_px(tk):
        try: return tk, fetch_massive_data(tk, 252)
        except: return tk, None

    def _fetch_earn(tk):
        try:
            info = yf.Ticker(tk).info or {}
            ts = info.get("earningsTimestampStart")
            if ts and ts > 0:
                ed = dt.utcfromtimestamp(ts).date()
                days = (ed - dt.now().date()).days
                if 0 < days <= 90: return tk, {"date": ed.isoformat(), "days": days}
        except: pass
        return tk, None

    def _fetch_chain(tk):
        try: return tk, fetch_options_chain(tk)
        except: return tk, None

    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {}
        for tk in tickers:
            futs[ex.submit(_fetch_px, tk)] = ("px", tk)
            futs[ex.submit(_fetch_earn, tk)] = ("earn", tk)
            futs[ex.submit(_fetch_chain, tk)] = ("chain", tk)
        for fut in as_completed(futs):
            kind, tk = futs[fut]
            try:
                _, val = fut.result()
                if kind == "px": price_cache[tk] = val
                elif kind == "earn": earnings_cache[tk] = val
                elif kind == "chain": chain_cache[tk] = val
            except: pass

    def _mid(row):
        b = row.get("bid", 0) or 0
        a = row.get("ask", 0) or 0
        lp = row.get("last_price", 0) or 0
        return (b + a) / 2 if (b > 0 and a > 0) else (lp if lp > 0 else 0)

    def _g(row, field):
        v = row.get(field, 0)
        try: return float(v) if v else 0.0
        except: return 0.0

    rfr = 0.045
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
            actual_dte = int(exp_chain["dte"].iloc[0])

            calls = exp_chain[exp_chain["contract_type"] == "call"].sort_values("strike_price")
            puts = exp_chain[exp_chain["contract_type"] == "put"].sort_values("strike_price")
            if calls.empty or puts.empty:
                continue

            # IVR / HV20 / VRP
            avg_iv_raw = ((float(calls.iloc[len(calls)//2].get("implied_volatility", 0) or 0) +
                           float(puts.iloc[len(puts)//2].get("implied_volatility", 0) or 0)) / 2)
            ivr, hv20, vrp = None, None, None
            px = price_cache.get(tk)
            if px is not None and len(px) > 30:
                hv_s = px["Close"].pct_change().rolling(20).std().dropna() * np.sqrt(252)
                if len(hv_s) > 0: hv20 = round(float(hv_s.iloc[-1] * 100), 1)
                if hv20 and avg_iv_raw > 0: vrp = round(avg_iv_raw * 100 - hv20, 1)
            if avg_iv_raw > 0:
                try: ivr = percentile_rank(tk, "atm_iv", current_value=avg_iv_raw)
                except: pass
            if ivr is None and avg_iv_raw > 0 and px is not None and len(px) > 30:
                hv_vals = px["Close"].pct_change().rolling(20).std().dropna().values * np.sqrt(252)
                if len(hv_vals) >= 20: ivr = round(float(np.sum(hv_vals <= avg_iv_raw) / len(hv_vals) * 100), 1)

            ivr_band = "N/A"
            if ivr is not None:
                ivr_band = "Low" if ivr < 30 else "Normal" if ivr < 50 else "Optimal" if ivr <= 75 else "Extreme"

            earn = earnings_cache.get(tk)
            earnings_before = bool(earn and earn["days"] <= actual_dte)

            # ── IV Skew for scoring context ──
            # 25Δ put skew = how expensive puts are vs ATM (>1.0 = puts rich)
            atm_call = calls.iloc[(calls["strike_price"] - spot).abs().argmin()] if len(calls) > 0 else None
            atm_iv = float(atm_call.get("implied_volatility", 0) or 0) if atm_call is not None else avg_iv_raw
            put_25d = puts[puts["strike_price"] < spot * 0.95]
            put_skew = 1.0
            if len(put_25d) > 0 and atm_iv > 0:
                put_25d_iv = float(put_25d.iloc[-1].get("implied_volatility", 0) or 0)
                if put_25d_iv > 0:
                    put_skew = round(put_25d_iv / atm_iv, 2)

            # ── Expected move from ATM straddle ──
            atm_put = puts.iloc[(puts["strike_price"] - spot).abs().argmin()] if len(puts) > 0 else None
            exp_move_pct = 0
            if atm_call is not None and atm_put is not None:
                call_mid = (_mid(atm_call) if atm_call is not None else 0)
                put_mid_val = (_mid(atm_put) if atm_put is not None else 0)
                straddle = call_mid + put_mid_val
                if spot > 0:
                    exp_move_pct = round(straddle / spot * 100, 1)

            # Try each spread type
            for spread_type in req.spread_types:
                try:
                    is_credit = spread_type in ("bull_put", "bear_call")
                    is_bullish = spread_type in ("bull_put", "bull_call")
                    opt_type = "put" if "put" in spread_type else "call"
                    legs = puts if opt_type == "put" else calls
                    otm = legs[legs["strike_price"] < spot] if opt_type == "put" else legs[legs["strike_price"] > spot]
                    if otm.empty: continue

                    otm_copy = otm.copy()
                    otm_copy["abs_delta"] = otm_copy["delta"].abs()
                    short_cands = otm_copy[otm_copy["abs_delta"] <= req.short_delta + 0.05]
                    if short_cands.empty: short_cands = otm_copy.tail(5) if opt_type == "put" else otm_copy.head(5)
                    if short_cands.empty: continue
                    short_leg = short_cands.iloc[(short_cands["abs_delta"] - req.short_delta).abs().argmin()]
                    short_k = float(short_leg["strike_price"])

                    # Long leg — credit: further OTM protection; debit: closer to money
                    if is_credit:
                        if opt_type == "put":
                            long_cands = legs[legs["strike_price"] <= short_k - req.width + 0.01]
                        else:
                            long_cands = legs[legs["strike_price"] >= short_k + req.width - 0.01]
                    else:
                        if opt_type == "call":
                            long_cands = legs[(legs["strike_price"] >= short_k - req.width - 0.01) & (legs["strike_price"] < short_k)]
                        else:
                            long_cands = legs[(legs["strike_price"] <= short_k + req.width + 0.01) & (legs["strike_price"] > short_k)]

                    if long_cands.empty: continue
                    if is_credit:
                        target = (short_k - req.width) if opt_type == "put" else (short_k + req.width)
                    else:
                        target = (short_k - req.width) if opt_type == "call" else (short_k + req.width)
                    long_leg = long_cands.iloc[(long_cands["strike_price"] - target).abs().argmin()]

                    long_k = float(long_leg["strike_price"])
                    if abs(short_k - long_k) < 0.5: continue

                    # Pricing
                    short_mid = _mid(short_leg)
                    long_mid = _mid(long_leg)

                    if is_credit:
                        credit = short_mid - long_mid
                        if credit <= 0.01: continue
                        width = abs(short_k - long_k)
                        max_risk = width - credit
                        if max_risk <= 0: continue
                        max_profit_val = credit
                        net_premium = credit
                    else:
                        debit = long_mid - short_mid
                        if debit <= 0.01: continue
                        width = abs(short_k - long_k)
                        max_risk = debit
                        max_profit_val = width - debit
                        if max_profit_val <= 0: continue
                        net_premium = -debit

                    # ── POP (corrected) ──
                    short_delta_abs = abs(float(short_leg.get("delta", 0) or 0)) or req.short_delta
                    long_delta_abs = abs(float(long_leg.get("delta", 0) or 0)) or 0
                    if is_credit:
                        # Credit: profit if OTM at exp. POP ≈ 1 - |short delta|
                        pop = 1 - short_delta_abs
                    else:
                        # Debit: profit if price crosses breakeven toward short strike
                        # POP ≈ |long delta| (probability long leg expires ITM = any profit)
                        # More precisely: probability of being past breakeven, between long & short deltas
                        pop = long_delta_abs
                    pop = max(0.05, min(0.95, pop))

                    avg_iv = ((float(short_leg.get("implied_volatility", 0) or 0) +
                               float(long_leg.get("implied_volatility", 0) or 0)) / 2)

                    # Liquidity
                    min_oi = min(float(short_leg.get("open_interest", 0) or 0), float(long_leg.get("open_interest", 0) or 0))
                    max_ba = max(
                        (float(short_leg.get("ask", 0) or 0) - float(short_leg.get("bid", 0) or 0)) if float(short_leg.get("ask", 0) or 0) > 0 else 999,
                        (float(long_leg.get("ask", 0) or 0) - float(long_leg.get("bid", 0) or 0)) if float(long_leg.get("ask", 0) or 0) > 0 else 999,
                    )
                    if min_oi >= 500 and max_ba <= 0.10: liq = "A"
                    elif min_oi >= 100 and max_ba <= 0.30: liq = "B"
                    elif min_oi >= 50 and max_ba <= 0.60: liq = "C"
                    elif min_oi >= 10: liq = "D"
                    else: liq = "F"

                    # Fill estimate — conservative: assume crossing half the wider leg's spread
                    _fill_pct = {"A": 0.35, "B": 0.25, "C": 0.15, "D": 0.08, "F": 0.03}.get(liq, 0.10)
                    short_bid = float(short_leg.get("bid", 0) or 0) or short_mid
                    long_ask = float(long_leg.get("ask", 0) or 0) or long_mid
                    if is_credit:
                        natural = short_bid - long_ask
                        fill_est = natural + (credit - natural) * _fill_pct if natural < credit else credit
                    else:
                        natural = long_ask - short_bid
                        fill_est = natural - (natural - debit) * _fill_pct if natural > debit else debit

                    # ── Breakeven ──
                    if is_credit:
                        breakeven = short_k - credit if opt_type == "put" else short_k + credit
                    else:
                        breakeven = long_k + debit if opt_type == "call" else long_k - debit
                    be_pct = round(abs(breakeven - spot) / spot * 100, 1) if spot > 0 else 0

                    # ── Expected move check ──
                    short_dist_pct = round(abs(short_k - spot) / spot * 100, 1) if spot > 0 else 0
                    inside_exp_move = exp_move_pct > 0 and short_dist_pct < exp_move_pct

                    # ── Skew score ──
                    # Bull put (sell puts): benefits from steep put skew (selling rich puts)
                    # Bear call (sell calls): benefits from flat/inverted skew
                    # Bull call (buy calls): benefits from flat call skew (buying cheap)
                    # Bear put (buy puts): benefits from steep skew (high put premium = large expected move)
                    skew_mult = 1.0
                    if spread_type == "bull_put" and put_skew > 1.10:
                        skew_mult = 1.0 + (put_skew - 1.0) * 2  # steep skew = more edge selling puts
                    elif spread_type == "bear_call" and put_skew < 1.05:
                        skew_mult = 1.1  # flat skew = calls fairly priced, OK for bear call
                    elif spread_type == "bull_call" and put_skew > 1.15:
                        skew_mult = 1.15  # steep put skew = calls relatively cheap = good for buying call spreads
                    elif spread_type == "bear_put" and put_skew > 1.15:
                        skew_mult = 1.15  # steep skew = large expected move, helps directional put bet

                    # ── Composite Score ──
                    IVR_W = {"Optimal": 1.5, "Normal": 1.0, "Extreme": 0.7, "Low": 0.6, "N/A": 0.8}
                    LIQ_W = {"A": 1.2, "B": 1.0, "C": 0.85, "D": 0.6, "F": 0.3}
                    base = (max_profit_val / max(max_risk, 0.01)) * pop
                    adj_score = base * IVR_W.get(ivr_band, 0.8) * LIQ_W.get(liq, 0.3) * skew_mult
                    if earnings_before:
                        adj_score *= 0.2 if inside_exp_move else 0.5
                    if vrp and vrp > 0 and is_credit:
                        adj_score *= (1.0 + vrp / 100)

                    # ── Greeks ──
                    sign = -1 if is_credit else 1  # credit: short the short leg; debit: long the long leg
                    net_delta = sign * _g(short_leg, "delta") + (-sign) * _g(long_leg, "delta") if is_credit else _g(long_leg, "delta") - _g(short_leg, "delta")
                    net_theta = -_g(short_leg, "theta") + _g(long_leg, "theta") if is_credit else _g(long_leg, "theta") - _g(short_leg, "theta")
                    net_vega = -_g(short_leg, "vega") + _g(long_leg, "vega") if is_credit else _g(long_leg, "vega") - _g(short_leg, "vega")
                    net_gamma = -_g(short_leg, "gamma") + _g(long_leg, "gamma") if is_credit else _g(long_leg, "gamma") - _g(short_leg, "gamma")

                    # ── 30Δ adjustment trigger ──
                    from scipy.stats import norm as _norm
                    short_iv_val = float(short_leg.get("implied_volatility", 0) or 0) or 0.20
                    T_trigger = actual_dte / 365
                    trigger_30d = float(short_k)
                    if T_trigger > 0 and short_iv_val > 0:
                        lo_t, hi_t = short_k * 0.70, short_k * 1.30
                        for _ in range(40):
                            m_t = (lo_t + hi_t) / 2
                            d1_t = (np.log(m_t / short_k) + (rfr + short_iv_val**2 / 2) * T_trigger) / (short_iv_val * np.sqrt(T_trigger))
                            da_t = abs(_norm.cdf(d1_t) - 1) if opt_type == "put" else _norm.cdf(d1_t)
                            if da_t < 0.30:
                                if opt_type == "put": hi_t = m_t
                                else: lo_t = m_t
                            else:
                                if opt_type == "put": lo_t = m_t
                                else: hi_t = m_t
                        trigger_30d = round((lo_t + hi_t) / 2, 2)

                    # ── Days to target (BS forward pricing) ──
                    long_iv_val = float(long_leg.get("implied_volatility", 0) or 0) or short_iv_val
                    days_to_target = actual_dte
                    if is_credit:
                        target_debit_val = net_premium * (1 - req.profit_target_pct / 100)
                    else:
                        target_spread_val = debit + max_profit_val * (req.profit_target_pct / 100)
                        target_debit_val = target_spread_val
                    for day in range(1, actual_dte, 3):
                        T_rem = (actual_dte - day) / 365
                        if T_rem <= 1 / 365: break
                        sv = abs(black_scholes(spot, short_k, T_rem, rfr, short_iv_val, opt_type) -
                                 black_scholes(spot, long_k, T_rem, rfr, long_iv_val, opt_type))
                        if is_credit and sv <= target_debit_val:
                            days_to_target = max(1, day - 1)
                            break
                        elif not is_credit and sv >= target_debit_val:
                            days_to_target = max(1, day - 1)
                            break

                    # ── Payoff curve ──
                    lo_k, hi_k = min(short_k, long_k), max(short_k, long_k)
                    range_lo = min(lo_k - 5, spot * 0.93, breakeven - 5)
                    range_hi = max(hi_k + 5, spot * 1.07, breakeven + 5)
                    payoff_prices = np.linspace(range_lo, range_hi, 100).tolist()
                    payoff_pnl = []
                    for px_val in payoff_prices:
                        if is_credit:
                            short_intr = max(short_k - px_val, 0) if opt_type == "put" else max(px_val - short_k, 0)
                            long_intr = max(long_k - px_val, 0) if opt_type == "put" else max(px_val - long_k, 0)
                            pnl = (credit - short_intr + long_intr) * 100
                        else:
                            long_intr = max(px_val - long_k, 0) if opt_type == "call" else max(long_k - px_val, 0)
                            short_intr = max(px_val - short_k, 0) if opt_type == "call" else max(short_k - px_val, 0)
                            pnl = (long_intr - short_intr - debit) * 100
                        payoff_pnl.append(round(pnl, 0))

                    # ── Theta decay path ──
                    decay_days, decay_vals = [], []
                    for day in range(0, actual_dte):
                        T_rem = (actual_dte - day) / 365
                        if T_rem <= 1 / 365: break
                        sv = abs(black_scholes(spot, short_k, T_rem, rfr, short_iv_val, opt_type) -
                                 black_scholes(spot, long_k, T_rem, rfr, long_iv_val, opt_type))
                        decay_days.append(day)
                        decay_vals.append(round(sv * 100, 0))

                    # ── Forward event stress test ──
                    # For credit spreads: credit is positive, P&L = credit - losses
                    # For debit spreads: we pass -debit as "credit", so P&L = -debit + gains
                    stress_credit = net_premium  # positive for credit, negative for debit
                    stress_stop = round(max_risk * 100 * req.stop_multiplier, 0) if is_credit else round(max_risk * 100, 0)
                    stress = _compute_stress_test(
                        spot, short_k if is_bullish else 0, short_k if not is_bullish else spot * 10,
                        long_k if is_bullish else 0, long_k if not is_bullish else spot * 10,
                        stress_credit, stress_stop,
                        actual_dte, best_exp, avg_iv * 100, earn)

                    # ── Kelly + managed WR (use hist if available) ──
                    if is_bullish:
                        bt_sp = short_k if opt_type == "put" else 0
                        bt_sc = spot * 10 if opt_type == "put" else short_k
                    else:
                        bt_sp = 0 if opt_type == "call" else short_k
                        bt_sc = short_k if opt_type == "call" else spot * 10
                    hist_wr = _compute_historical_winrate(
                        px, spot, bt_sp, bt_sc,
                        abs(net_premium), max_risk, actual_dte,
                        req.profit_target_pct, req.stop_multiplier)

                    if hist_wr and hist_wr["win_rate"] > 0:
                        managed_wr = min(0.95, hist_wr["win_rate"] / 100)
                    else:
                        managed_wr = min(0.95, pop + req.win_rate_bump / 100)
                    premium_100 = round(abs(net_premium) * 100, 0)
                    risk_100 = round(max_risk * 100, 0)
                    target_profit = premium_100 * req.profit_target_pct / 100 if is_credit else round(max_profit_val * req.profit_target_pct / 100 * 100, 0)
                    stop_loss_amt = min(premium_100 * req.stop_multiplier, risk_100) if is_credit else risk_100

                    if stop_loss_amt > 0 and target_profit > 0:
                        b = target_profit / stop_loss_amt
                        full_kelly = (managed_wr * b - (1 - managed_wr)) / b if b > 0 else 0
                        adj_kelly = max(0, full_kelly) * req.kelly_fraction
                        capped_pct = min(adj_kelly, req.max_risk_pct / 100) if adj_kelly > 0 else req.max_risk_pct / 100
                        contracts = int(req.account_size * capped_pct / max(risk_100, 1))
                    else:
                        full_kelly = adj_kelly = 0
                        contracts = 0

                    # ── Alt expirations ──
                    alt_exps = _find_alt_expirations(
                        chain, spot, req.dte_min, req.dte_max,
                        req.short_delta, req.width, best_exp, req.profit_target_pct)

                    results.append({
                        "ticker": tk,
                        "spread_type": spread_type,
                        "spread_label": spread_type.replace("_", " ").title(),
                        "is_credit": is_credit,
                        "is_bullish": is_bullish,
                        "opt_type": opt_type,
                        "expiration": best_exp,
                        "dte": actual_dte,
                        "spot": round(spot, 2),
                        "short_strike": short_k,
                        "long_strike": long_k,
                        "width": round(abs(short_k - long_k), 2),
                        "premium": round(abs(net_premium) * 100, 0),
                        "fill_estimate": round(abs(fill_est) * 100, 0),
                        "max_risk": risk_100,
                        "max_profit": round(max_profit_val * 100, 0),
                        "pop": round(pop * 100, 1),
                        "rr_ratio": round(max_profit_val / max(max_risk, 0.01), 2),
                        "breakeven": round(breakeven, 2),
                        "be_pct": be_pct,
                        "avg_iv": round(avg_iv * 100, 1),
                        "ivr": round(ivr, 1) if ivr is not None else None,
                        "ivr_band": ivr_band,
                        "vrp": vrp,
                        "hv20": hv20,
                        "put_skew": put_skew,
                        "exp_move_pct": exp_move_pct,
                        "short_dist_pct": short_dist_pct,
                        "inside_exp_move": inside_exp_move,
                        "liq_grade": liq,
                        "min_oi": int(min_oi),
                        "max_ba": round(max_ba, 2) if max_ba < 900 else None,
                        "earnings_before": earnings_before,
                        "earnings_days": earn["days"] if earn else None,
                        "adj_score": round(adj_score, 4),
                        "n_synthetic": sum(1 for r in [short_leg, long_leg] if not r.get("quote_live", True)),
                        # Greeks
                        "net_delta": round(net_delta, 4),
                        "net_gamma": round(net_gamma, 4),
                        "net_theta": round(net_theta, 4),
                        "net_vega": round(net_vega, 4),
                        # Management
                        "trigger_30d": trigger_30d,
                        "days_to_target": days_to_target,
                        # Kelly
                        "managed_wr": round(managed_wr * 100, 1),
                        "kelly_full": round(full_kelly * 100, 1),
                        "kelly_adj": round(adj_kelly * 100, 1),
                        "contracts": contracts,
                        "total_credit": round(abs(fill_est) * 100 * contracts, 0),
                        "total_risk": round(risk_100 * contracts, 0),
                        "profit_target_pct": req.profit_target_pct,
                        "stop_multiplier": req.stop_multiplier,
                        "target_profit": round(target_profit, 0),
                        "stop_loss": round(stop_loss_amt, 0),
                        # Charts
                        "payoff_prices": payoff_prices,
                        "payoff_pnl": payoff_pnl,
                        "decay_days": decay_days,
                        "decay_vals": decay_vals,
                        # Analysis
                        "hist_winrate": hist_wr,
                        "stress_test": stress,
                        "alt_expirations": alt_exps,
                        # Legs
                        "legs": [
                            {"label": f"${short_k:.0f} {opt_type[0].upper()} (short)", "bid": round(float(short_leg.get("bid", 0) or 0), 2),
                             "ask": round(float(short_leg.get("ask", 0) or 0), 2), "mid": round(short_mid, 2),
                             "delta": round(_g(short_leg, "delta"), 3), "oi": int(float(short_leg.get("open_interest", 0) or 0)),
                             "live": bool(short_leg.get("quote_live", True))},
                            {"label": f"${long_k:.0f} {opt_type[0].upper()} (long)", "bid": round(float(long_leg.get("bid", 0) or 0), 2),
                             "ask": round(float(long_leg.get("ask", 0) or 0), 2), "mid": round(long_mid, 2),
                             "delta": round(_g(long_leg, "delta"), 3), "oi": int(float(long_leg.get("open_interest", 0) or 0)),
                             "live": bool(long_leg.get("quote_live", True))},
                        ],
                    })
                except Exception:
                    continue
        except Exception:
            continue

    results.sort(key=lambda r: r["adj_score"], reverse=True)

    def _to_native(v):
        if isinstance(v, (np.integer,)): return int(v)
        if isinstance(v, (np.floating,)): return float(v)
        if isinstance(v, (np.bool_,)): return bool(v)
        if isinstance(v, dict): return {k2: _to_native(v2) for k2, v2 in v.items()}
        if isinstance(v, list): return [_to_native(i) for i in v]
        return v

    clean = [{k: _to_native(v) for k, v in r.items()} for r in results]
    return {"count": len(clean), "results": clean}


# ═══════════════════════════════════════════════════════════════════
# Signal Scanner bundle — prices + fundamentals + EPS + insider
# ═══════════════════════════════════════════════════════════════════

class SignalScanRequest(BaseModel):
    tickers: list[str]
    lookback: str = "1y"  # 6mo | 1y | 2y


@router.post("/signal-bundle")
def signal_bundle(req: SignalScanRequest, user: str = Depends(get_current_user)):
    """One-shot fetch for the Signal Scanner page — prices/volume for all
    tickers plus fundamentals, EPS revisions, and insider activity.
    """
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor
    from src.market_data import fetch_eps_revisions, fetch_insider_summary

    tickers = [t.strip().upper() for t in req.tickers if t.strip()]
    if len(tickers) < 2:
        return {"error": "Need at least 2 tickers"}

    def _load_prices():
        try:
            data = yf.download(tickers, period=req.lookback, progress=False, threads=True, auto_adjust=False)
            out_prices: dict[str, list[dict]] = {}
            if hasattr(data.columns, "levels"):  # MultiIndex
                close_df = data["Close"]
                vol_df = data["Volume"]
                for tk in close_df.columns:
                    rows = []
                    for ts, val in close_df[tk].items():
                        if val == val:  # not NaN
                            vol = vol_df[tk].get(ts, None)
                            rows.append({
                                "Date": str(ts.date()),
                                "Close": float(val),
                                "Volume": float(vol) if vol == vol else 0,
                            })
                    out_prices[tk] = rows
            else:
                tk = tickers[0]
                rows = []
                for ts, val in data["Close"].items():
                    if val == val:
                        vol = data["Volume"].get(ts, 0)
                        rows.append({
                            "Date": str(ts.date()),
                            "Close": float(val),
                            "Volume": float(vol) if vol == vol else 0,
                        })
                out_prices[tk] = rows
            return out_prices
        except Exception:
            return {}

    def _load_fundamentals():
        def _one(tk: str) -> dict | None:
            try:
                info = yf.Ticker(tk).info or {}
                mktcap = info.get("marketCap")
                fcf = info.get("freeCashflow")
                debt = info.get("totalDebt")
                cash = info.get("totalCash")
                ebitda = info.get("ebitda")
                def _pct(k):
                    v = info.get(k)
                    return v * 100 if isinstance(v, (int, float)) else None
                return {
                    "ticker": tk,
                    "forward_pe": info.get("forwardPE"),
                    "trailing_pe": info.get("trailingPE"),
                    "price_to_book": info.get("priceToBook"),
                    "ev_ebitda": info.get("enterpriseToEbitda"),
                    "dividend_yield": _pct("dividendYield"),
                    "fcf_yield": (fcf / mktcap * 100) if fcf and mktcap and mktcap > 0 else None,
                    "roe": _pct("returnOnEquity"),
                    "profit_margin": _pct("profitMargins"),
                    "operating_margin": _pct("operatingMargins"),
                    "gross_margin": _pct("grossMargins"),
                    "revenue_growth": _pct("revenueGrowth"),
                    "earnings_growth": _pct("earningsGrowth"),
                    "beta": info.get("beta"),
                    "net_debt_ebitda": ((debt or 0) - (cash or 0)) / ebitda if debt and ebitda and ebitda > 0 else None,
                    "current_ratio": info.get("currentRatio"),
                    "market_cap": mktcap,
                }
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=8) as ex:
            return [r for r in ex.map(_one, tickers) if r]

    def _load_eps():
        try:
            return df_records(fetch_eps_revisions(tickers))
        except Exception:
            return []

    def _load_insider():
        try:
            return df_records(fetch_insider_summary(tickers))
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_prices = pool.submit(_load_prices)
        f_funds = pool.submit(_load_fundamentals)
        f_eps = pool.submit(_load_eps)
        f_ins = pool.submit(_load_insider)

    return {
        "prices": f_prices.result(),
        "fundamentals": f_funds.result(),
        "eps_revisions": f_eps.result(),
        "insider": f_ins.result(),
    }
