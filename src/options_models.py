"""Options pricing models for filling gaps in market data.

Implements:
- Black-Scholes (BS) for baseline pricing
- Merton Jump-Diffusion (MJD) for tail-risk-adjusted pricing
- BS-MJD blend that weights MJD more heavily for OTM options

Used when market data (IV, price, Greeks) is missing or zero.
"""

import numpy as np
from scipy.stats import norm
from math import factorial


def black_scholes(S, K, T, r, sigma, opt_type="call"):
    """Standard Black-Scholes option price."""
    if T <= 0 or sigma <= 0:
        # Expired or zero vol — intrinsic value only
        if opt_type == "call":
            return max(0, S - K)
        return max(0, K - S)

    d1 = (np.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    if opt_type == "call":
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_greeks(S, K, T, r, sigma, opt_type="call"):
    """Black-Scholes Greeks."""
    if T <= 0 or sigma <= 0:
        return {"delta": 1.0 if opt_type == "call" else -1.0,
                "gamma": 0, "theta": 0, "vega": 0}

    d1 = (np.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    delta = norm.cdf(d1) if opt_type == "call" else norm.cdf(d1) - 1
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))

    common_theta = -(S * sigma * norm.pdf(d1)) / (2 * np.sqrt(T))
    if opt_type == "call":
        theta = (common_theta - r * K * np.exp(-r * T) * norm.cdf(d2)) / 365
    else:
        theta = (common_theta + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365

    vega = S * np.sqrt(T) * norm.pdf(d1) / 100  # per 1% IV change

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def merton_jump_diffusion(S, K, T, r, sigma, lam=0.1, mu_j=-0.1, sigma_j=0.15,
                          opt_type="call", n_terms=10):
    """Merton Jump-Diffusion option price.

    Extends BS with Poisson-distributed jumps in the underlying price.

    Parameters:
        S: spot price
        K: strike price
        T: time to expiry (years)
        r: risk-free rate
        sigma: diffusion volatility
        lam: jump intensity (expected jumps per year, default 0.1)
        mu_j: mean jump size (log-normal, default -0.1 = 10% down-jump)
        sigma_j: jump size volatility (default 0.15)
        opt_type: "call" or "put"
        n_terms: number of Poisson expansion terms
    """
    if T <= 0 or sigma <= 0:
        if opt_type == "call":
            return max(0, S - K)
        return max(0, K - S)

    # Compensated jump drift
    k_bar = np.exp(mu_j + sigma_j**2 / 2) - 1
    lam_prime = lam * (1 + k_bar)

    price = 0.0
    for n in range(n_terms):
        # Modified parameters for n jumps
        sigma_n = np.sqrt(sigma**2 + n * sigma_j**2 / T)
        r_n = r - lam * k_bar + n * (mu_j + sigma_j**2 / 2) / T

        # BS price with modified params
        bs_price = black_scholes(S, K, T, r_n, sigma_n, opt_type)

        # Poisson weight
        poisson_weight = np.exp(-lam_prime * T) * (lam_prime * T)**n / factorial(n)

        price += poisson_weight * bs_price

    return price


def mjd_greeks(S, K, T, r, sigma, lam=0.1, mu_j=-0.1, sigma_j=0.15, opt_type="call"):
    """Merton Jump-Diffusion Greeks via finite difference."""
    if T <= 0 or sigma <= 0:
        return {"delta": 1.0 if opt_type == "call" else -1.0,
                "gamma": 0, "theta": 0, "vega": 0}

    dS = S * 0.001
    dsig = 0.001
    dT = 1 / 365

    price = merton_jump_diffusion(S, K, T, r, sigma, lam, mu_j, sigma_j, opt_type)
    price_up = merton_jump_diffusion(S + dS, K, T, r, sigma, lam, mu_j, sigma_j, opt_type)
    price_down = merton_jump_diffusion(S - dS, K, T, r, sigma, lam, mu_j, sigma_j, opt_type)

    delta = (price_up - price_down) / (2 * dS)
    gamma = (price_up - 2 * price + price_down) / (dS**2)

    price_later = merton_jump_diffusion(S, K, max(T - dT, 0.0001), r, sigma, lam, mu_j, sigma_j, opt_type)
    theta = (price_later - price)  # already per day since dT = 1/365

    price_vol_up = merton_jump_diffusion(S, K, T, r, sigma + dsig, lam, mu_j, sigma_j, opt_type)
    vega = (price_vol_up - price) / (dsig * 100)  # per 1% IV

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def blended_price(S, K, T, r, sigma, opt_type="call",
                  lam=0.1, mu_j=-0.1, sigma_j=0.15):
    """BS-MJD blended price.

    Weights MJD more heavily for OTM options where jump risk matters most.
    ATM options use mostly BS (which is well-calibrated there).
    """
    moneyness = abs(np.log(S / K))  # 0 = ATM, higher = more OTM

    # Weight: 0 at ATM → 1 for deep OTM
    mjd_weight = min(1.0, moneyness * 5)  # full MJD weight at ~20% OTM
    bs_weight = 1.0 - mjd_weight

    bs_px = black_scholes(S, K, T, r, sigma, opt_type)
    mjd_px = merton_jump_diffusion(S, K, T, r, sigma, lam, mu_j, sigma_j, opt_type)

    return bs_weight * bs_px + mjd_weight * mjd_px


def blended_greeks(S, K, T, r, sigma, opt_type="call",
                   lam=0.1, mu_j=-0.1, sigma_j=0.15):
    """BS-MJD blended Greeks with same weighting as blended_price."""
    moneyness = abs(np.log(S / K))
    mjd_weight = min(1.0, moneyness * 5)
    bs_weight = 1.0 - mjd_weight

    bs_g = bs_greeks(S, K, T, r, sigma, opt_type)
    mjd_g = mjd_greeks(S, K, T, r, sigma, lam, mu_j, sigma_j, opt_type)

    return {
        "delta": bs_weight * bs_g["delta"] + mjd_weight * mjd_g["delta"],
        "gamma": bs_weight * bs_g["gamma"] + mjd_weight * mjd_g["gamma"],
        "theta": bs_weight * bs_g["theta"] + mjd_weight * mjd_g["theta"],
        "vega": bs_weight * bs_g["vega"] + mjd_weight * mjd_g["vega"],
    }


def fill_missing_options_data(df, spot, risk_free_rate=0.045,
                              lam=0.1, mu_j=-0.1, sigma_j=0.15):
    """Fill missing prices and Greeks in an options DataFrame using BS-MJD blend.

    Fills:
    - mid_price (bid/ask) when both are 0
    - delta, gamma, theta, vega when 0 or missing
    - implied_volatility is NOT filled (we need market IV as input, not model IV)

    Parameters:
        df: DataFrame with strike_price, contract_type, expiration_date, bid, ask,
            implied_volatility, delta, gamma, theta, vega
        spot: current underlying price
        risk_free_rate: annualized risk-free rate
    """
    import pandas as pd

    df = df.copy()

    for idx, row in df.iterrows():
        K = row['strike_price']
        opt_type = row['contract_type']
        iv = row.get('implied_volatility', 0)

        # Calculate time to expiry
        try:
            exp_date = pd.to_datetime(row['expiration_date'])
            T = max((exp_date - pd.Timestamp.now()).days / 365, 0.001)
        except Exception:
            T = 0.01

        if iv <= 0 or iv is None:
            continue  # Can't price without IV

        # Fill price if missing
        bid = row.get('bid', 0) or 0
        ask = row.get('ask', 0) or 0
        if bid == 0 and ask == 0:
            model_price = blended_price(spot, K, T, risk_free_rate, iv, opt_type,
                                        lam, mu_j, sigma_j)
            df.at[idx, 'bid'] = model_price * 0.99
            df.at[idx, 'ask'] = model_price * 1.01
            if 'last_price' in df.columns and (df.at[idx, 'last_price'] == 0 or pd.isna(df.at[idx, 'last_price'])):
                df.at[idx, 'last_price'] = model_price
            if 'model_filled' not in df.columns:
                df['model_filled'] = False
            df.at[idx, 'model_filled'] = True

        # Fill Greeks if missing or zero
        delta = row.get('delta', 0) or 0
        gamma = row.get('gamma', 0) or 0

        if abs(delta) < 1e-10 and abs(gamma) < 1e-10:
            greeks = blended_greeks(spot, K, T, risk_free_rate, iv, opt_type,
                                    lam, mu_j, sigma_j)
            df.at[idx, 'delta'] = greeks['delta']
            df.at[idx, 'gamma'] = greeks['gamma']
            df.at[idx, 'theta'] = greeks['theta']
            df.at[idx, 'vega'] = greeks['vega']

    return df
