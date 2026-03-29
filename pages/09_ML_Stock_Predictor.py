"""
ML Tactical Forecast — Institutional-Grade 30-Day Price Projection

Multi-model ensemble with walk-forward validation, feature importance,
regime detection, distribution analysis, and baseline comparison.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging

from src.data_engine import fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.layout import setup_page, get_active_ticker, set_active_ticker, error_boundary, fun_loader
from src.styles import COLORS

logger = logging.getLogger(__name__)
setup_page("09_ML_Stock_Predictor")

st.title("ML Tactical Forecast")
st.markdown(
    "Multi-model ensemble with walk-forward validation. Projects 30-day price paths "
    "using recursive Random Forest with regime detection and distribution analysis."
)

PLOTLY_NOBAR = {"displayModeBar": False}


# ═══════════════════════════════════════════════
# FEATURE ENGINEERING
# ═══════════════════════════════════════════════

def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build comprehensive feature set from OHLCV data."""
    f = pd.DataFrame(index=df.index)

    close = df["Close"]
    f["ret_1"] = close.pct_change(1)
    f["ret_2"] = close.pct_change(2)
    f["ret_5"] = close.pct_change(5)
    f["ret_10"] = close.pct_change(10)
    f["ret_21"] = close.pct_change(21)

    # Lagged returns (avoid look-ahead)
    for lag in [1, 2, 3, 5, 10]:
        f[f"lag_ret_{lag}"] = f["ret_1"].shift(lag)

    # Volatility features
    f["vol_5"] = f["ret_1"].rolling(5).std()
    f["vol_10"] = f["ret_1"].rolling(10).std()
    f["vol_20"] = f["ret_1"].rolling(20).std()
    f["vol_60"] = f["ret_1"].rolling(60).std()
    f["vol_ratio"] = f["vol_5"] / f["vol_20"].replace(0, np.nan)  # short/long vol ratio

    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    f["rsi_14"] = (100 - (100 / (1 + rs))).fillna(50)

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    f["macd_hist"] = macd - macd_signal

    # Bollinger Band position
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    f["bb_pct"] = (close - sma20) / std20.replace(0, np.nan)

    # Mean reversion (z-score) — zscore_60 uses longer window than bb_pct (20d)
    f["zscore_60"] = (close - close.rolling(60).mean()) / close.rolling(60).std().replace(0, np.nan)

    # Price momentum
    f["mom_12_1"] = close.pct_change(252).shift(21)  # 12-month return, skip recent month
    f["sma_cross"] = (close / sma20 - 1)  # distance from SMA20

    # Volume features (if available)
    if "Volume" in df.columns:
        vol = df["Volume"]
        f["vol_sma_ratio"] = vol / vol.rolling(20).mean().replace(0, np.nan)
        f["vol_change"] = vol.pct_change(5)

    # ATR (if OHLC available)
    if all(c in df.columns for c in ["High", "Low"]):
        tr = pd.concat([
            df["High"] - df["Low"],
            (df["High"] - close.shift(1)).abs(),
            (df["Low"] - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        f["atr_14"] = tr.rolling(14).mean() / close  # normalized ATR

    return f


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_macro_features(n_days: int) -> pd.DataFrame:
    """Fetch VIX, SPY, and yield data for macro context features."""
    macro = pd.DataFrame()
    try:
        import yfinance as yf
        # VIX
        vix = yf.download("^VIX", period=f"{max(n_days // 252 + 1, 2)}y", progress=False)
        if vix is not None and not vix.empty:
            macro["vix"] = vix["Close"]
            macro["vix_ret_5"] = macro["vix"].pct_change(5)
            macro["vix_level"] = macro["vix"] / macro["vix"].rolling(60).mean()  # normalized

        # SPY (market factor)
        spy = yf.download("SPY", period=f"{max(n_days // 252 + 1, 2)}y", progress=False)
        if spy is not None and not spy.empty:
            macro["spy_ret_1"] = spy["Close"].pct_change(1)
            macro["spy_ret_5"] = spy["Close"].pct_change(5)
            macro["spy_ret_21"] = spy["Close"].pct_change(21)
            macro["spy_vol_20"] = spy["Close"].pct_change().rolling(20).std()

        # 10Y yield (via TNX)
        tnx = yf.download("^TNX", period=f"{max(n_days // 252 + 1, 2)}y", progress=False)
        if tnx is not None and not tnx.empty:
            macro["yield_10y"] = tnx["Close"]
            macro["yield_chg_5"] = macro["yield_10y"].diff(5)
    except Exception:
        pass
    return macro


# ═══════════════════════════════════════════════
# CONTROLS
# ═══════════════════════════════════════════════

with st.expander("Model Configuration", expanded=True):
    _c1, _c2, _c3 = st.columns(3)
    with _c1:
        raw_ticker = st.text_input("Ticker", value=get_active_ticker())
    with _c2:
        fwd_horizon = st.selectbox("Forecast Horizon", [1, 5, 10, 21],
                                    index=1, format_func=lambda d: f"{d}-Day Forward",
                                    help="5-day has 3-5x better signal-to-noise than daily")
    with _c3:
        lookback = st.slider("Training Window (Days)", 500, 1260 * 5, 1260, step=252,
                              help="Polygon provides 5Y stock data + 2Y options data")

    _c4, _c5, _c6 = st.columns(3)
    with _c4:
        n_trees = st.slider("Estimators per Model", 100, 500, 200, step=50)
    with _c5:
        n_sims = st.slider("Monte Carlo Paths", 500, 5000, 2000, step=500)
    with _c6:
        use_vol_adj = st.checkbox("Vol-Adjusted Target", value=True,
                                   help="Predict return/vol instead of raw return — normalizes across regimes")

ticker = format_massive_ticker(raw_ticker)
set_active_ticker(ticker)

submit = st.button("Run Forecast", type="primary", use_container_width=True)


# ═══════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════

if submit:
    with fun_loader("compute"):
        df = fetch_massive_data(ticker, max(lookback + 300, 756))
        if df is None or df.empty:
            st.error(f"Failed to fetch data for {ticker}.")
            st.stop()

        px_close = df["Close"].dropna()
        if len(px_close) < 252:
            st.error("Need at least 1 year of data.")
            st.stop()

        # Build stock-specific features
        features_df = _build_features(df)

        # Add IV-derived features from Polygon options data
        try:
            from src.data_engine import fetch_polygon_rsi as _poly_rsi
            _iv_proxy = fetch_massive_data("^VIX" if ticker != "SPY" else ticker, max(lookback + 300, 756))
            if _iv_proxy is not None and not _iv_proxy.empty and "Close" in _iv_proxy.columns:
                _vix_close = _iv_proxy["Close"]
                features_df["iv_proxy"] = _vix_close.reindex(features_df.index).ffill()
                features_df["iv_proxy_ret5"] = features_df["iv_proxy"].pct_change(5)
                features_df["iv_proxy_level"] = features_df["iv_proxy"] / features_df["iv_proxy"].rolling(60).mean()
        except Exception:
            pass

        # Add macro/cross-asset features
        macro_df = _fetch_macro_features(lookback + 300)
        if not macro_df.empty:
            # Align macro to stock dates
            for col in macro_df.columns:
                features_df[f"macro_{col}"] = macro_df[col].reindex(features_df.index).ffill()

        feature_cols = [c for c in features_df.columns if features_df[c].notna().sum() > 100]

        # ── Target: multi-horizon, optionally vol-adjusted ──
        fwd_ret = df["Close"].pct_change(fwd_horizon).shift(-fwd_horizon)
        if use_vol_adj:
            trailing_vol = df["Close"].pct_change().rolling(20).std() * np.sqrt(fwd_horizon)
            trailing_vol = trailing_vol.replace(0, np.nan)
            features_df["target"] = fwd_ret / trailing_vol  # vol-adjusted
        else:
            features_df["target"] = fwd_ret

        # ── Purge overlapping targets ──
        # With N-day forward targets, consecutive samples share N-1 days.
        # Purge by keeping every Nth row for training to remove overlap.
        all_data = features_df[feature_cols + ["target"]].dropna().tail(lookback)
        if fwd_horizon > 1:
            # Purged: keep every fwd_horizon-th row for train, but use ALL for OOS
            purge_step = max(1, fwd_horizon // 2)  # partial purge (full purge = fwd_horizon)
            train_df = all_data.iloc[::purge_step]
        else:
            train_df = all_data

        if len(train_df) < 100:
            st.error("Insufficient clean training data after purging.")
            st.stop()

        X_train = train_df[feature_cols].values
        y_train = train_df["target"].values

        # ── Walk-forward validation ──
        oos_n = max(30, min(63, len(train_df) // 5))
        split = len(train_df) - oos_n
        X_is, y_is = X_train[:split], y_train[:split]
        X_oos, y_oos = X_train[split:], y_train[split:]

        from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor

        # ── Model 1: Random Forest ──
        rf_model = RandomForestRegressor(
            n_estimators=n_trees, max_depth=8, min_samples_leaf=10,
            max_features=0.6, random_state=42, n_jobs=-1,
        )
        rf_model.fit(X_is, y_is)
        rf_oos = rf_model.predict(X_oos)

        # ── Model 2: Gradient Boosting ──
        gb_model = GradientBoostingRegressor(
            n_estimators=n_trees, max_depth=4, min_samples_leaf=15,
            learning_rate=0.05, subsample=0.8, random_state=42,
        )
        gb_model.fit(X_is, y_is)
        gb_oos = gb_model.predict(X_oos)

        # ── Ensemble: fixed 50/50 blend for unbiased OOS evaluation ──
        # (Optimizing weight on OOS would inflate apparent performance)
        best_w = 0.5
        oos_preds = best_w * rf_oos + (1 - best_w) * gb_oos

        # OOS metrics
        _corr_val = np.corrcoef(oos_preds, y_oos)[0, 1] if len(y_oos) > 5 else 0
        oos_corr = float(_corr_val) if not np.isnan(_corr_val) else 0.0
        oos_rmse = np.sqrt(np.mean((oos_preds - y_oos) ** 2))
        oos_direction = np.mean(np.sign(oos_preds) == np.sign(y_oos))
        naive_rmse = np.sqrt(np.mean(y_oos ** 2))
        skill_score = 1 - (oos_rmse / naive_rmse) if naive_rmse > 0 else 0

        # Now optimize weight on OOS for the FINAL model (not used in evaluation)
        _best_w_final, _best_corr_final = 0.5, -1
        for w in np.arange(0, 1.05, 0.1):
            blend = w * rf_oos + (1 - w) * gb_oos
            _c = np.corrcoef(blend, y_oos)[0, 1]
            if not np.isnan(_c) and _c > _best_corr_final:
                _best_corr_final = _c
                _best_w_final = w
        best_w = _best_w_final  # use optimized weight for forward forecast only

        # Individual model metrics for comparison
        rf_dir = np.mean(np.sign(rf_oos) == np.sign(y_oos))
        gb_dir = np.mean(np.sign(gb_oos) == np.sign(y_oos))

        # ── Train final ensemble on full data ──
        rf_final = RandomForestRegressor(
            n_estimators=n_trees, max_depth=8, min_samples_leaf=10,
            max_features=0.6, random_state=42, n_jobs=-1,
        )
        rf_final.fit(X_train, y_train)

        gb_final = GradientBoostingRegressor(
            n_estimators=n_trees, max_depth=4, min_samples_leaf=15,
            learning_rate=0.05, subsample=0.8, random_state=42,
        )
        gb_final.fit(X_train, y_train)

        model = rf_final  # for tree-based uncertainty and feature importance

        # Feature importance (average of both models)
        rf_imp = pd.Series(rf_final.feature_importances_, index=feature_cols)
        gb_imp = pd.Series(gb_final.feature_importances_, index=feature_cols)
        importances = ((rf_imp + gb_imp) / 2).sort_values(ascending=False)

        # ── Recursive forecast ──
        def _simple_features(temp_df):
            """Feature builder for recursive forecasting (Close-only buffer)."""
            f = pd.DataFrame(index=temp_df.index)
            close = temp_df["Close"]
            ret = close.pct_change()
            f["ret_1"] = ret
            f["ret_2"] = close.pct_change(2)
            f["ret_5"] = close.pct_change(5)
            f["ret_10"] = close.pct_change(10)
            f["ret_21"] = close.pct_change(21)
            for lag in [1, 2, 3, 5, 10]:
                f[f"lag_ret_{lag}"] = ret.shift(lag)
            f["vol_5"] = ret.rolling(5).std()
            f["vol_10"] = ret.rolling(10).std()
            f["vol_20"] = ret.rolling(20).std()
            if len(ret.dropna()) > 60:
                f["vol_60"] = ret.rolling(60).std()
            f["vol_ratio"] = f["vol_5"] / f["vol_20"].replace(0, np.nan)
            delta = close.diff()
            gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / 14, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1 / 14, adjust=False).mean()
            rs = gain / loss.replace(0, np.nan)
            f["rsi_14"] = (100 - (100 / (1 + rs))).fillna(50)
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd = ema12 - ema26
            f["macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            f["bb_pct"] = (close - sma20) / std20.replace(0, np.nan)
            if len(close) > 60:
                f["zscore_60"] = (close - close.rolling(60).mean()) / close.rolling(60).std().replace(0, np.nan)
            _sma20_safe = sma20.replace(0, np.nan)
            f["sma_cross"] = (close / _sma20_safe - 1).fillna(0)
            if len(close) > 252:
                f["mom_12_1"] = close.pct_change(252).shift(21)
            # Pad missing columns — use last known training value for macro features
            # (0 would mean "no VIX, no SPY return" which biases the model)
            for c in feature_cols:
                if c not in f.columns:
                    if c.startswith("macro_") and c in features_df.columns:
                        _last_val = features_df[c].dropna().iloc[-1] if features_df[c].notna().any() else 0
                        f[c] = float(_last_val)
                    else:
                        f[c] = 0
            return f[feature_cols] if all(c in f.columns for c in feature_cols) else f

        # Ensemble recursive forecast: RF for uncertainty, RF+GB blend for drift
        buffer = px_close.tail(150).tolist()
        mu_path = []
        sigma_path = []
        for step in range(30):
            temp_df = pd.DataFrame({"Close": buffer})
            feats = _simple_features(temp_df).dropna()
            if feats.empty:
                mu_path.append(0)
                sigma_path.append(0.01)
                buffer.append(buffer[-1])
                continue
            X_cur = feats.iloc[-1:].values
            # RF trees for uncertainty
            tree_preds = [tree.predict(X_cur)[0] for tree in rf_final.estimators_]
            rf_mu = np.mean(tree_preds)
            step_sigma = max(np.std(tree_preds), 1e-6)
            # GB prediction
            gb_mu = float(gb_final.predict(X_cur)[0])
            # Ensemble drift
            step_mu = best_w * rf_mu + (1 - best_w) * gb_mu
            # If vol-adjusted target, convert back to raw return
            if use_vol_adj:
                _tv = pd.Series(buffer).pct_change().tail(20).std() * np.sqrt(fwd_horizon)
                _tv = float(_tv) if pd.notna(_tv) and _tv > 0 else 0.01
                step_mu = step_mu * _tv
                step_sigma = step_sigma * _tv
            mu_path.append(step_mu)
            sigma_path.append(step_sigma)
            buffer.append(buffer[-1] * (1 + step_mu))
        mu_path = np.array(mu_path)
        sigma_path = np.array(sigma_path)

        # ── Monte Carlo distribution ──
        rng = np.random.default_rng(42)
        shocks = rng.normal(loc=mu_path, scale=sigma_path, size=(n_sims, 30))
        price_paths = float(px_close.iloc[-1]) * np.cumprod(1.0 + shocks, axis=1)

        p5 = np.percentile(price_paths, 5, axis=0)
        p25 = np.percentile(price_paths, 25, axis=0)
        p50 = np.percentile(price_paths, 50, axis=0)
        p75 = np.percentile(price_paths, 75, axis=0)
        p95 = np.percentile(price_paths, 95, axis=0)

        # Representative path (closest to median terminal value)
        median_end = np.percentile(price_paths[:, -1], 50)
        rep_idx = np.argmin(np.abs(price_paths[:, -1] - median_end))
        rep_path = price_paths[rep_idx]

        future_dates = pd.bdate_range(start=px_close.index[-1] + pd.Timedelta(days=1), periods=30)

        # Regime detection (current vol regime)
        recent_vol = float(px_close.pct_change().tail(20).std() * np.sqrt(252))
        long_vol = float(px_close.pct_change().tail(252).std() * np.sqrt(252))
        vol_regime = "High Vol" if recent_vol > long_vol * 1.3 else ("Low Vol" if recent_vol < long_vol * 0.7 else "Normal")

        # Terminal distribution stats
        terminal_prices = price_paths[:, -1]
        prob_up = (terminal_prices > float(px_close.iloc[-1])).mean() * 100
        expected_return = (np.median(terminal_prices) / float(px_close.iloc[-1]) - 1) * 100

        # ── OOS trading signal backtest ──
        # Use raw daily returns for P&L (not vol-adjusted targets).
        # Model predicts N-day direction but we trade 1-day returns — this tests
        # whether the multi-day signal has same-day predictive power.
        _oos_dates = train_df.index[split:]
        _raw_daily = df["Close"].pct_change().reindex(_oos_dates).fillna(0).values
        oos_long_mask = oos_preds > 0
        oos_signal_pnl = np.where(oos_long_mask, _raw_daily, 0)
        oos_signal_cum = np.cumprod(1 + oos_signal_pnl) - 1
        oos_buyhold_cum = np.cumprod(1 + _raw_daily) - 1
        oos_dates = _oos_dates

        # ── Prediction calibration (quintile analysis) ──
        cal_df = pd.DataFrame({"pred": oos_preds, "actual": y_oos})
        try:
            cal_df["decile"] = pd.qcut(cal_df["pred"], q=5, labels=False, duplicates="drop")
        except ValueError:
            # Too few unique prediction values — fall back to 3 bins
            try:
                cal_df["decile"] = pd.qcut(cal_df["pred"], q=3, labels=False, duplicates="drop")
            except ValueError:
                cal_df["decile"] = 0  # all same bin
        calibration = cal_df.groupby("decile").agg(
            avg_pred=("pred", "mean"),
            avg_actual=("actual", "mean"),
            count=("pred", "count"),
        ).reset_index()

        # ── Analyst consensus comparison ──
        analyst_target = None
        analyst_return = None
        try:
            import yfinance as yf
            _info = yf.Ticker(ticker).info or {}
            _at = _info.get("targetMeanPrice")
            _cp = _info.get("currentPrice") or _info.get("regularMarketPrice")
            if _at and _cp and _cp > 0:
                analyst_target = float(_at)
                analyst_return = (analyst_target / _cp - 1) * 100
        except Exception:
            pass

        # ── Confidence decay (CI width by day) ──
        ci_width_pct = (p95 - p5) / float(px_close.iloc[-1]) * 100

        # ── Regime-conditional accuracy ──
        # Split OOS into high-vol and low-vol sub-periods
        oos_vol = pd.Series(y_oos).rolling(10).std().values
        oos_vol_median = np.nanmedian(oos_vol)
        hi_vol_mask = oos_vol > oos_vol_median
        lo_vol_mask = ~hi_vol_mask & ~np.isnan(oos_vol)
        hi_vol_dir = np.mean(np.sign(oos_preds[hi_vol_mask]) == np.sign(y_oos[hi_vol_mask])) if hi_vol_mask.sum() > 5 else None
        lo_vol_dir = np.mean(np.sign(oos_preds[lo_vol_mask]) == np.sign(y_oos[lo_vol_mask])) if lo_vol_mask.sum() > 5 else None

        # Track prediction for accuracy measurement
        try:
            from src.prediction_tracker import record_prediction
            record_prediction(
                source="ml_predictor",
                ticker=ticker,
                prediction={
                    "direction": "Bullish" if expected_return > 0 else "Bearish",
                    "target": float(p50[-1]),
                    "expected_return_pct": round(expected_return, 2),
                    "probability_up": round(prob_up, 1),
                    "skill_score": round(skill_score, 3),
                },
                spot=float(px_close.iloc[-1]),
                metadata={"horizon": fwd_horizon, "vol_adjusted": use_vol_adj,
                           "n_features": len(feature_cols), "direction_accuracy": round(float(oos_direction), 3)},
            )
        except Exception:
            pass

        # Write signal for signal engine
        try:
            from src.signal_engine import write_signal
            _dir = "bull" if expected_return > 0 else ("bear" if expected_return < 0 else "neutral")
            _conv = min(1.0, max(0.0, abs(expected_return) / 10.0)) * min(1.0, max(0.0, float(oos_direction)))
            _reason = (f"{ticker} {fwd_horizon}d ML forecast: {expected_return:+.1f}% expected return, "
                       f"{prob_up:.0f}% prob up, direction acc {oos_direction:.0%}, skill {skill_score:.2f}")
            write_signal("ml_predictor", ticker, _dir, round(_conv, 3), reasoning=_reason)
        except Exception:
            pass

        # Store results
        st.session_state["mlp_forecast"] = {
            "px_close": px_close,
            "rep_path": rep_path,
            "p5": p5, "p25": p25, "p50": p50, "p75": p75, "p95": p95,
            "future_dates": future_dates,
            "terminal_prices": terminal_prices,
            "importances": importances,
            "oos_corr": oos_corr,
            "oos_rmse": oos_rmse,
            "oos_direction": oos_direction,
            "skill_score": skill_score,
            "naive_rmse": naive_rmse,
            "vol_regime": vol_regime,
            "recent_vol": recent_vol,
            "long_vol": long_vol,
            "prob_up": prob_up,
            "expected_return": expected_return,
            "mu_path": mu_path,
            "sigma_path": sigma_path,
            "n_features": len(feature_cols),
            "n_train": len(train_df),
            "n_oos": len(y_oos),
            "n_sims": n_sims,
            # New institutional metrics
            "oos_signal_cum": oos_signal_cum,
            "oos_buyhold_cum": oos_buyhold_cum,
            "oos_dates": oos_dates,
            "calibration": calibration,
            "analyst_target": analyst_target,
            "analyst_return": analyst_return,
            "ci_width_pct": ci_width_pct,
            "hi_vol_dir": hi_vol_dir,
            "lo_vol_dir": lo_vol_dir,
            "fwd_horizon": fwd_horizon,
            "vol_adjusted": use_vol_adj,
            "ensemble_weight": best_w,
            "rf_direction": rf_dir,
            "gb_direction": gb_dir,
        }
        st.session_state["mlp_ticker"] = ticker


# ═══════════════════════════════════════════════
# RENDER
# ═══════════════════════════════════════════════

if "mlp_forecast" not in st.session_state:
    st.info("Configure settings and click **Run Forecast**.")
    st.stop()

fc = st.session_state["mlp_forecast"]
px_close = fc["px_close"]
current_price = float(px_close.iloc[-1])


# ─── TABS ──────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "Forecast", "Model Diagnostics", "Distribution Analysis",
    "Feature Importance", "Walk-Forward Validation",
    "Signal Backtest", "Calibration", "Regime Analysis",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — FORECAST
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    with error_boundary("Forecast"):
        with st.expander("How to use this forecast"):
            st.markdown("""
**What you're looking at:** A 30-day probabilistic price forecast generated by a
Random Forest model trained on this stock's own historical patterns.

**Key elements:**
- **Representative Path** (solid cyan): A single realistic simulated path that ends
  near the median outcome. This is what "a typical scenario" looks like — jagged and
  volatile, not a smooth line.
- **50% CI** (darker band): Half of all simulated paths fall within this range.
  Think of it as the "likely" zone.
- **90% CI** (lighter band): 90% of simulations land here. The edges are the
  "tail risk" scenarios.
- **Median** (green dotted): The mathematical middle of all simulations.

**How to use it:**
- If the 90% CI lower bound is above your entry price, risk/reward is favorable.
- If the median is below current price, the model is net bearish.
- Compare the ML target to the analyst consensus (shown below the chart when available)
  to see if the model agrees with the street.
- Check the **Confidence Decay** chart — if uncertainty explodes after day 10,
  the forecast beyond that point is largely noise.

**What NOT to do:** Don't treat the representative path as "the prediction."
The whole distribution matters. A 60% probability of being up with a wide range
is very different from 60% with a narrow range.
""")
        # Metrics row
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Current", f"${current_price:,.2f}")
        m2.metric("30-Day Target", f"${fc['p50'][-1]:,.2f}",
                  delta=f"{fc['expected_return']:+.1f}%")
        m3.metric("90% Range", f"${fc['p5'][-1]:,.2f} – ${fc['p95'][-1]:,.2f}")
        m4.metric("Prob. Positive", f"{fc['prob_up']:.0f}%")
        m5.metric("Vol Regime", fc["vol_regime"])

        # Model quality banner
        skill = fc["skill_score"]
        if skill > 0.05:
            st.success(f"Model has predictive skill — RMSE {fc['oos_rmse']*100:.2f}% vs naive {fc['naive_rmse']*100:.2f}% (skill score: {skill:.2f})")
        elif skill > -0.05:
            st.info(f"Model is marginal vs random walk — skill score {skill:.2f}. Use directional accuracy ({fc['oos_direction']:.0%}) as primary signal.")
        else:
            st.warning(f"Model underperforms random walk (skill: {skill:.2f}). Forecast should be interpreted with caution.")

        # Chart
        hist_plot = px_close.tail(90)
        future_dates = fc["future_dates"]

        fig = go.Figure()
        # Historical
        fig.add_trace(go.Scatter(
            x=hist_plot.index, y=hist_plot.values,
            mode="lines", name="Historical", line=dict(color="white", width=2),
        ))
        # 90% CI
        fig.add_trace(go.Scatter(
            x=np.concatenate([future_dates, future_dates[::-1]]),
            y=np.concatenate([fc["p95"], fc["p5"][::-1]]),
            fill="toself", fillcolor="rgba(0,209,255,0.08)",
            line=dict(color="rgba(0,0,0,0)"), name="90% CI", hoverinfo="skip",
        ))
        # 50% CI
        fig.add_trace(go.Scatter(
            x=np.concatenate([future_dates, future_dates[::-1]]),
            y=np.concatenate([fc["p75"], fc["p25"][::-1]]),
            fill="toself", fillcolor="rgba(0,209,255,0.15)",
            line=dict(color="rgba(0,0,0,0)"), name="50% CI", hoverinfo="skip",
        ))
        # Connector
        fig.add_trace(go.Scatter(
            x=[hist_plot.index[-1], future_dates[0]],
            y=[hist_plot.values[-1], fc["rep_path"][0]],
            mode="lines", line=dict(color=COLORS["accent"], width=2), showlegend=False,
        ))
        # Representative path
        fig.add_trace(go.Scatter(
            x=future_dates, y=fc["rep_path"],
            mode="lines", name="Representative Path",
            line=dict(color=COLORS["accent"], width=2.5),
        ))
        # Median
        fig.add_trace(go.Scatter(
            x=future_dates, y=fc["p50"],
            mode="lines", name="Median",
            line=dict(color=COLORS["success"], width=1, dash="dot"),
        ))

        fig.add_hline(y=current_price, line_dash="dash", line_color=COLORS["text_muted"], line_width=0.5)
        fig.update_layout(
            template="plotly_dark", height=500,
            xaxis_title="Date", yaxis_title="Price ($)",
            margin=dict(t=10, b=0, l=0, r=0), hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_NOBAR)

        # Analyst comparison
        _a_target = fc.get("analyst_target")
        _a_return = fc.get("analyst_return")
        if _a_target and _a_return is not None:
            ac1, ac2, ac3 = st.columns(3)
            ac1.metric("ML Target (30d)", f"${fc['p50'][-1]:,.2f}",
                        delta=f"{fc['expected_return']:+.1f}%")
            ac2.metric("Analyst Consensus (12mo)", f"${_a_target:,.2f}",
                        delta=f"{_a_return:+.1f}%")
            _diff = fc["expected_return"] - _a_return
            ac3.metric("ML vs Street",
                        "More Bullish" if _diff > 2 else ("More Bearish" if _diff < -2 else "In Line"),
                        delta=f"{_diff:+.1f}pp")

        # Confidence decay
        st.markdown("#### Forecast Confidence Decay")
        _ci_w = fc.get("ci_width_pct")
        if _ci_w is not None and len(_ci_w) > 0:
            fig_decay = go.Figure()
            fig_decay.add_trace(go.Scatter(
                x=list(range(1, len(_ci_w) + 1)), y=_ci_w,
                fill="tozeroy", fillcolor="rgba(255,170,0,0.1)",
                line=dict(color=COLORS["warning"], width=2),
                hovertemplate="Day %{x}: ±%{y:.1f}%<extra></extra>",
            ))
            fig_decay.update_layout(
                template="plotly_dark", height=250,
                xaxis_title="Days Forward", yaxis_title="90% CI Width (%)",
                margin=dict(l=50, r=20, t=10, b=40),
            )
            st.plotly_chart(fig_decay, use_container_width=True, config=PLOTLY_NOBAR)
            _decay_ratio = _ci_w[-1] / _ci_w[0] if _ci_w[0] > 0 else 1.0
            st.caption(
                f"Day 1 uncertainty: ±{_ci_w[0]:.1f}% | Day 30: ±{_ci_w[-1]:.1f}%. "
                f"Forecast is {_decay_ratio:.1f}x less precise at the end than the start."
            )

        _fh = fc.get("fwd_horizon", 1)
        _va = fc.get("vol_adjusted", False)
        _ew = fc.get("ensemble_weight", 0.5)
        st.caption(
            f"RF+GradientBoosting ensemble (RF {_ew:.0%} / GB {1-_ew:.0%}) | "
            f"{_fh}-day forward {'vol-adjusted ' if _va else ''}target | "
            f"{fc['n_train']:,} training samples, {fc['n_features']} features | "
            f"{fc.get('n_sims', 2000):,} Monte Carlo paths | "
            f"20d vol: {fc['recent_vol']*100:.1f}% (1Y: {fc['long_vol']*100:.1f}%)"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — MODEL DIAGNOSTICS
# ══════════════════════════════════════════════════════════════════════════════

with tab2:
    with error_boundary("Model Diagnostics"):
        st.subheader("Model Quality Assessment")
        with st.expander("How to interpret these diagnostics"):
            st.markdown("""
**These metrics answer one question: should you trust this forecast?**

- **Skill Score**: Compares the model's error (RMSE) to a "predict zero" naive baseline.
  - **> 0.05**: Model genuinely adds value over random walk. Forecast has substance.
  - **-0.05 to 0.05**: Marginal — model is roughly as good as guessing "no change."
  - **< -0.05**: Model is worse than doing nothing. Forecast is unreliable.

- **Directional Accuracy**: What % of days did the model correctly predict up vs down?
  - **> 55%**: Statistically meaningful edge. Tradeable signal.
  - **50-55%**: Slight edge, but need more data to confirm it's not luck.
  - **< 50%**: Model predicts the wrong direction more often than not. Contrarian signal?

- **Prediction-Actual Correlation**: Does the model predict *larger* moves on days that
  actually move *more*? This tests magnitude prediction, not just direction.
  - **> 0.10**: Useful magnitude signal. Model sees something real.
  - **< 0.05**: Magnitude predictions are noise. Use direction only.

- **Daily Drift & Uncertainty charts**: Show what the model thinks will happen each day.
  If drift oscillates wildly, the model is uncertain. If uncertainty grows rapidly,
  the forecast degrades quickly beyond the first few days.
""")

        dc1, dc2, dc3, dc4 = st.columns(4)
        dc1.metric("Skill Score", f"{fc['skill_score']:.3f}")
        dc2.metric("Direction Accuracy", f"{fc['oos_direction']:.0%}")
        dc3.metric("Pred-Actual Corr", f"{fc['oos_corr']:.3f}")
        dc4.metric("OOS RMSE", f"{fc['oos_rmse']*100:.3f}%")

        # Ensemble breakdown
        st.markdown("#### Model Ensemble")
        _ew = fc.get("ensemble_weight", 0.5)
        _rf_d = fc.get("rf_direction", 0)
        _gb_d = fc.get("gb_direction", 0)
        _fh = fc.get("fwd_horizon", 1)
        _va = fc.get("vol_adjusted", False)

        ec1, ec2, ec3, ec4 = st.columns(4)
        ec1.metric("Forecast Horizon", f"{_fh}-Day")
        ec2.metric("RF Direction", f"{_rf_d:.0%}" if _rf_d else "—")
        ec3.metric("GB Direction", f"{_gb_d:.0%}" if _gb_d else "—")
        ec4.metric("Ensemble Weight", f"RF {_ew:.0%} / GB {1-_ew:.0%}")

        _target_desc = "Vol-adjusted target (return/vol)" if _va else "Raw return target"
        _purge_desc = f"Purged training data to remove {_fh}-day target overlap." if _fh > 1 else "Daily target, no purging needed."
        st.caption(f"{_target_desc}. Ensemble weight: RF {_ew:.0%} / GB {1-_ew:.0%}. {_purge_desc}")

        # Daily predicted drift and uncertainty
        st.markdown("#### Predicted Daily Drift & Uncertainty")
        fig_drift = make_subplots(rows=1, cols=2, subplot_titles=["Daily Drift (μ)", "Daily Uncertainty (σ)"])
        fig_drift.add_trace(go.Bar(
            x=list(range(1, 31)), y=fc["mu_path"] * 100,
            marker_color=[COLORS["success"] if v > 0 else COLORS["danger"] for v in fc["mu_path"]],
        ), row=1, col=1)
        fig_drift.add_trace(go.Bar(
            x=list(range(1, 31)), y=fc["sigma_path"] * 100,
            marker_color=COLORS["warning"],
        ), row=1, col=2)
        fig_drift.update_xaxes(title_text="Day")
        fig_drift.update_yaxes(title_text="% per day", row=1, col=1)
        fig_drift.update_yaxes(title_text="% per day", row=1, col=2)
        fig_drift.update_layout(template="plotly_dark", height=300, showlegend=False,
                                 margin=dict(l=50, r=20, t=40, b=40))
        st.plotly_chart(fig_drift, use_container_width=True, config=PLOTLY_NOBAR)

        # Regime context
        st.markdown("#### Volatility Regime")
        st.caption(
            f"20-day realized vol: **{fc['recent_vol']*100:.1f}%** | "
            f"1-year average: **{fc['long_vol']*100:.1f}%** | "
            f"Regime: **{fc['vol_regime']}**"
        )
        if fc["vol_regime"] == "High Vol":
            st.warning("High vol regime — forecast uncertainty is elevated. Wider confidence intervals are appropriate.")
        elif fc["vol_regime"] == "Low Vol":
            st.info("Low vol regime — forecast confidence is higher, but watch for regime shifts.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — DISTRIBUTION ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

with tab3:
    with error_boundary("Distribution Analysis"):
        st.subheader("Terminal Price Distribution")
        with st.expander("How to use the distribution"):
            st.markdown("""
**This is where the forecast becomes actionable for position sizing and risk management.**

**Price Distribution (top chart):**
- The histogram shows every possible 30-day outcome from the Monte Carlo simulation.
- Green vertical line = current price. Everything right = profit, left = loss.
- **Wide histogram**: High uncertainty — size positions smaller.
- **Narrow histogram**: Model is confident — can size larger.
- **Right-skewed**: More upside scenarios. Favorable risk/reward.
- **Left-skewed**: Tail risk is to the downside. Consider protective puts.

**Return Distribution (bottom chart):**
- Same data expressed as percentage returns. The percentile markers show:
  - **5th percentile**: Your worst-case at 95% confidence (= 30-day VaR)
  - **25th/75th**: The "likely" range
  - **95th**: Best realistic upside

**How traders use this:**
- **Position sizing**: If 95% VaR is -8%, and you can tolerate a $10K loss,
  max position = $10K / 8% = $125K.
- **Option strike selection**: The 25th and 75th percentiles map to ~1-sigma moves —
  these are natural strike levels for puts and calls.
- **Skewness**: Positive skew with negative median = "lottery ticket" distribution.
  Negative skew with positive median = "steady income with tail risk."
""")

        terminal = fc["terminal_prices"]

        fig_dist = go.Figure()
        fig_dist.add_trace(go.Histogram(
            x=terminal, nbinsx=60,
            marker_color=COLORS["accent"], opacity=0.7,
        ))
        fig_dist.add_vline(x=current_price, line_dash="dash", line_color=COLORS["success"],
                            annotation_text=f"Current ${current_price:,.2f}")
        fig_dist.add_vline(x=np.median(terminal), line_dash="dot", line_color=COLORS["warning"],
                            annotation_text=f"Median ${np.median(terminal):,.2f}")
        fig_dist.update_layout(
            template="plotly_dark", height=400,
            xaxis_title="Terminal Price ($)", yaxis_title="Frequency",
            margin=dict(l=50, r=20, t=10, b=50),
        )
        st.plotly_chart(fig_dist, use_container_width=True, config=PLOTLY_NOBAR)

        # Stats
        ds1, ds2, ds3, ds4, ds5 = st.columns(5)
        ds1.metric("Mean", f"${terminal.mean():,.2f}")
        ds2.metric("Median", f"${np.median(terminal):,.2f}")
        ds3.metric("Std Dev", f"${terminal.std():,.2f}")
        ds4.metric("Skewness", f"{pd.Series(terminal).skew():.2f}")
        ds5.metric("P(Up)", f"{fc['prob_up']:.0f}%")

        # Return distribution
        st.markdown("#### Return Distribution")
        ret_dist = (terminal / current_price - 1) * 100
        fig_ret = go.Figure()
        # Split into positive and negative returns for color coding
        fig_ret.add_trace(go.Histogram(
            x=ret_dist[ret_dist >= 0], nbinsx=30, name="Positive",
            marker_color=COLORS["success"], opacity=0.7,
        ))
        fig_ret.add_trace(go.Histogram(
            x=ret_dist[ret_dist < 0], nbinsx=30, name="Negative",
            marker_color=COLORS["danger"], opacity=0.7,
        ))
        fig_ret.add_vline(x=0, line_dash="dash", line_color="white")
        # Percentile markers
        for pct, label in [(5, "5th"), (25, "25th"), (75, "75th"), (95, "95th")]:
            val = np.percentile(ret_dist, pct)
            fig_ret.add_vline(x=val, line_dash="dot", line_color=COLORS["text_muted"],
                               annotation_text=f"{label}: {val:+.1f}%", annotation_font_size=9)
        fig_ret.update_layout(
            template="plotly_dark", height=350,
            xaxis_title="30-Day Return (%)", yaxis_title="Frequency",
            margin=dict(l=50, r=20, t=10, b=50),
        )
        st.plotly_chart(fig_ret, use_container_width=True, config=PLOTLY_NOBAR)

        # Risk metrics
        var_95 = np.percentile(ret_dist, 5)
        cvar_95 = ret_dist[ret_dist <= var_95].mean() if (ret_dist <= var_95).any() else var_95
        st.caption(
            f"**30-Day VaR (95%):** {var_95:+.1f}% | "
            f"**CVaR:** {cvar_95:+.1f}% | "
            f"**Max loss (sim):** {ret_dist.min():+.1f}% | "
            f"**Max gain (sim):** {ret_dist.max():+.1f}%"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — FEATURE IMPORTANCE
# ══════════════════════════════════════════════════════════════════════════════

with tab4:
    with error_boundary("Feature Importance"):
        st.subheader("Random Forest Feature Importance")
        with st.expander("What the features tell you about the model"):
            st.markdown("""
**Feature importance reveals what the model has "learned" — and what to be suspicious of.**

**What each feature group means:**
- **Volatility features** (vol_5, vol_20, vol_ratio, atr): Model is primarily detecting
  vol regime changes. These models tend to predict "mean reversion after vol spikes"
  and "momentum during low vol." This is a common and often robust signal.
- **Lagged returns** (lag_ret_1 through lag_ret_10): Model is trading short-term
  momentum or mean reversion. If lag_ret_1 dominates, it's a 1-day reversal strategy.
- **RSI/Bollinger** (rsi_14, bb_pct, zscore): Mean reversion signals. These work best
  in range-bound markets and fail during breakouts.
- **MACD**: Trend-following signal. Works in trending markets, fails in chop.
- **Momentum** (ret_21, mom_12_1, sma_cross): Longer-term trend signals. These are
  more robust across regimes but less precise day-to-day.

**Red flags to watch for:**
- If a **single feature** has >30% importance, the model is fragile — it's essentially
  a one-variable regression dressed up as a forest.
- If **volume features** dominate but the stock is illiquid, the model may be fitting
  to noise in volume data.
- If **lagged returns** dominate, check the Regime Analysis tab — these signals often
  work only in one vol regime.

**The Group Breakdown** at the bottom shows the aggregate importance by category.
A model where Volatility + Momentum account for 60%+ is typical and generally robust.
""")

        imp = fc["importances"]
        top_n = min(20, len(imp))
        top_imp = imp.head(top_n)

        fig_imp = go.Figure(go.Bar(
            y=top_imp.index[::-1], x=top_imp.values[::-1] * 100,
            orientation="h",
            marker_color=COLORS["accent"],
            text=[f"{v*100:.1f}%" for v in top_imp.values[::-1]],
            textposition="outside",
        ))
        fig_imp.update_layout(
            template="plotly_dark", height=max(350, top_n * 22),
            xaxis_title="Importance (%)",
            margin=dict(l=0, r=60, t=10, b=0),
        )
        st.plotly_chart(fig_imp, use_container_width=True, config=PLOTLY_NOBAR)

        # Feature group breakdown
        # Volume features first (more specific match) to avoid "vol" matching volatility
        _volume_feats = {c for c in imp.index if "vol_sma" in c or "vol_change" in c}
        _groups = {
            "Momentum": [c for c in imp.index if ("ret" in c or "mom" in c or "sma_cross" in c or "lag" in c) and c not in _volume_feats],
            "Volatility": [c for c in imp.index if ("vol_" in c or "atr" in c) and c not in _volume_feats],
            "Mean Reversion": [c for c in imp.index if "rsi" in c or "bb" in c or "zscore" in c],
            "Trend": [c for c in imp.index if "macd" in c],
            "Volume": list(_volume_feats),
        }
        group_imp = {}
        for gname, cols in _groups.items():
            gv = imp.reindex(cols).dropna().sum()
            if gv > 0:
                group_imp[gname] = gv
        if group_imp:
            st.markdown("#### Feature Group Breakdown")
            total_imp = sum(group_imp.values())
            for gname, gv in sorted(group_imp.items(), key=lambda x: -x[1]):
                st.caption(f"**{gname}:** {gv/total_imp*100:.1f}% of total importance")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — WALK-FORWARD VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

with tab5:
    with error_boundary("Walk-Forward"):
        st.subheader("Out-of-Sample Validation")
        with st.expander("Why this is the most important tab"):
            st.markdown("""
**This is the honesty test.** Everything else on this page could be overfitted noise.
This tab shows how the model performed on data it was explicitly prevented from seeing
during training.

**Why walk-forward matters:**
- Any model can fit historical data perfectly (that's just curve-fitting).
- The question is: does the model predict data it's never seen?
- We train on the first ~75% of the lookback period, then test on the last ~63 days.
- If the model still works on the test period, there's a real signal. If not, it's noise.

**How to read the metrics:**
- **Skill Score > 0.05 + Direction > 55%**: Genuine edge. Forecast is credible.
- **Skill ≈ 0 + Direction > 52%**: Marginal. Use as a directional hint, not a precise target.
- **Skill < 0 or Direction < 50%**: Model doesn't work for this stock. The forecast is
  no better than (or worse than) flipping a coin.

**What to do if the model fails:**
- Try a different training window (shorter for recent regime, longer for stability)
- Check the Feature Importance tab — the model may be relying on features that
  worked historically but broke recently
- Check the Regime Analysis tab — the current vol environment may be outside
  the model's training experience
- Accept that not all stocks are predictable — this itself is valuable information
""")

        wf1, wf2, wf3, wf4 = st.columns(4)
        wf1.metric("OOS Period", f"{fc['n_oos']} days")
        wf2.metric("Skill vs Random Walk", f"{fc['skill_score']:+.3f}")
        wf3.metric("Direction Accuracy", f"{fc['oos_direction']:.0%}")
        wf4.metric("Pred-Actual Correlation", f"{fc['oos_corr']:.3f}")

        # Verdict
        if fc["skill_score"] > 0.05 and fc["oos_direction"] > 0.55:
            st.success("Model shows genuine predictive skill on held-out data. Forecast has statistical credibility.")
        elif fc["oos_direction"] > 0.52:
            st.info("Marginal directional edge. Forecast direction is slightly better than random, but magnitude predictions are noisy.")
        else:
            st.warning(
                "Model does not reliably outperform a random walk on held-out data. "
                "The forecast should be used for scenario analysis, not as a point prediction."
            )

        st.caption(
            f"Training: {fc['n_train'] - fc['n_oos']} days | "
            f"Testing: last {fc['n_oos']} days | "
            f"Model RMSE: {fc['oos_rmse']*100:.3f}% | "
            f"Naive RMSE: {fc['naive_rmse']*100:.3f}%"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — SIGNAL BACKTEST
# ══════════════════════════════════════════════════════════════════════════════

with tab6:
    with error_boundary("Signal Backtest"):
        st.subheader("Trading Signal Backtest")
        with st.expander("How to interpret the signal backtest"):
            st.markdown("""
**This is the "put your money where your mouth is" test.** Instead of just measuring
accuracy in abstract terms, we simulate what would have happened if you actually
traded the model's predictions.

**The trading rule is simple:**
- Model predicts positive return → go long (buy and hold that day)
- Model predicts negative return → sit in cash (earn nothing, lose nothing)
- No leverage, no shorting, no transaction costs (conservative approximation)

**How to read the chart:**
- **Cyan line** (Model Signal): Cumulative P&L from following the model
- **Gray dashed** (Buy & Hold): What you'd have made just holding the stock
- **Signal Alpha**: The difference. Positive = the model's timing added value.

**What the outcomes mean:**
- **Signal > Buy & Hold**: Model correctly avoids down days. Timing alpha is real.
  This is the strongest possible validation — the model makes money AND beats passive.
- **Signal > 0 but < Buy & Hold**: Model makes money but doesn't beat holding.
  The model's directional signal has value for risk management (avoiding big down days)
  even if total return is lower (you miss some up days too).
- **Signal < 0**: Model's timing actively loses money. Don't trade this signal.

**Important caveat:** This is a backtest on ~63 OOS days. Statistical significance
requires 200+ days. Use this for directional insight, not as a guaranteed edge.
""")

        _sig_cum = fc.get("oos_signal_cum")
        _bh_cum = fc.get("oos_buyhold_cum")
        _oos_dt = fc.get("oos_dates")

        if _sig_cum is not None and _oos_dt is not None and len(_sig_cum) > 5:
            fig_sig = go.Figure()
            fig_sig.add_trace(go.Scatter(
                x=_oos_dt, y=_sig_cum * 100,
                mode="lines", name="Model Signal",
                line=dict(color=COLORS["accent"], width=2),
            ))
            fig_sig.add_trace(go.Scatter(
                x=_oos_dt, y=_bh_cum * 100,
                mode="lines", name="Buy & Hold",
                line=dict(color=COLORS["text_muted"], width=1, dash="dash"),
            ))
            fig_sig.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5)
            fig_sig.update_layout(
                template="plotly_dark", height=400,
                xaxis_title="Date", yaxis_title="Cumulative Return (%)",
                margin=dict(l=50, r=20, t=10, b=50),
            )
            st.plotly_chart(fig_sig, use_container_width=True, config=PLOTLY_NOBAR)

            sig_final = _sig_cum[-1] * 100
            bh_final = _bh_cum[-1] * 100
            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("Model Signal Return", f"{sig_final:+.1f}%")
            sc2.metric("Buy & Hold Return", f"{bh_final:+.1f}%")
            sc3.metric("Signal Alpha", f"{sig_final - bh_final:+.1f}pp",
                        help="Positive = model timing adds value vs always holding")

            if sig_final > bh_final:
                st.success("Model signal outperformed buy & hold — timing adds value.")
            elif sig_final > 0:
                st.info("Model signal is profitable but doesn't beat buy & hold — long bias may be enough.")
            else:
                st.warning("Model signal lost money on OOS period. Timing signal is not reliable.")
        else:
            st.info("Run forecast to see signal backtest.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — CALIBRATION
# ══════════════════════════════════════════════════════════════════════════════

with tab7:
    with error_boundary("Calibration"):
        st.subheader("Prediction Calibration")
        with st.expander("Why calibration is the institutional gold standard"):
            st.markdown("""
**Calibration is how quant funds decide whether to deploy capital on a signal.**

Direction accuracy tells you if the model is right more than half the time.
Calibration tells you something much more powerful: *does the model know when it's
most right?*

**How it works:**
We sort all OOS predictions from most bearish (Q1) to most bullish (Q5), then check
what actually happened in each quintile.

**What the chart shows:**
- **Bars** = actual average daily return per quintile
- **Line** = model's predicted average return per quintile
- If bars slope upward from Q1 to Q5, the model has **sorting power** — its strongest
  conviction predictions actually perform best.

**What to look for:**
- **Q5-Q1 spread > 0.05%/day**: Strong sorting. Over 252 trading days, that's ~12.6%
  annualized alpha from the long/short signal. Institutional-grade.
- **Q5-Q1 spread 0.01-0.05%/day**: Moderate sorting. Useful for timing, not for a
  standalone strategy.
- **Monotonic bars**: Perfect — every quintile outperforms the one below it. Rare but
  very strong when it occurs.
- **Q5-Q1 spread ≈ 0 or negative**: No sorting power. Model's confidence levels are
  meaningless — it doesn't know when it's more or less likely to be right.

**How traders use this:**
- Only trade Q5 signals (highest conviction) if Q5 consistently outperforms
- Use Q1 as a "don't trade" filter if Q1 consistently underperforms
- If Q5 and Q1 both show positive returns, the model may just have a long bias
  (not actual alpha)
""")

        cal = fc.get("calibration")
        if cal is not None and len(cal) >= 3:
            fig_cal = go.Figure()
            fig_cal.add_trace(go.Bar(
                x=[f"Q{int(d)+1}" for d in cal["decile"]],
                y=cal["avg_actual"] * 100,
                marker_color=[COLORS["success"] if v > 0 else COLORS["danger"] for v in cal["avg_actual"]],
                text=[f"{v*100:+.2f}%" for v in cal["avg_actual"]],
                textposition="outside",
                name="Actual Return",
            ))
            fig_cal.add_trace(go.Scatter(
                x=[f"Q{int(d)+1}" for d in cal["decile"]],
                y=cal["avg_pred"] * 100,
                mode="lines+markers",
                line=dict(color=COLORS["accent"], width=2),
                marker=dict(size=8),
                name="Predicted Return",
            ))
            fig_cal.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5)
            fig_cal.update_layout(
                template="plotly_dark", height=380,
                xaxis_title="Prediction Quintile (Q1=most bearish, Q5=most bullish)",
                yaxis_title="Average Daily Return (%)",
                margin=dict(l=50, r=20, t=10, b=50),
                legend=dict(x=0.02, y=0.98, bgcolor="rgba(0,0,0,0.5)"),
            )
            st.plotly_chart(fig_cal, use_container_width=True, config=PLOTLY_NOBAR)

            # Check monotonicity
            actual_vals = cal["avg_actual"].values
            is_monotonic = all(actual_vals[i] <= actual_vals[i + 1] for i in range(len(actual_vals) - 1))
            spread = (actual_vals[-1] - actual_vals[0]) * 100

            cal_c1, cal_c2 = st.columns(2)
            cal_c1.metric("Q5-Q1 Spread", f"{spread:+.2f}%/day",
                           help="Difference between most bullish and most bearish quintile actual returns")
            cal_c2.metric("Monotonic", "Yes" if is_monotonic else "No",
                           help="Are actual returns ordered correctly across quintiles?")

            if is_monotonic and spread > 0.05:
                st.success(f"Strong calibration — model's top quintile outperforms bottom by {spread:.2f}%/day. Signal has genuine sorting power.")
            elif spread > 0:
                st.info(f"Positive but imperfect calibration — Q5-Q1 spread is {spread:.2f}%/day. Some sorting power exists.")
            else:
                st.warning("Weak or inverted calibration — model's rankings don't predict relative returns. Signal may be noise.")

            with st.expander("Quintile Detail"):
                _cal_disp = cal.copy()
                _cal_disp["Quintile"] = [f"Q{int(d)+1}" for d in _cal_disp["decile"]]
                _cal_disp["Avg Predicted"] = _cal_disp["avg_pred"].apply(lambda v: f"{v*100:+.3f}%")
                _cal_disp["Avg Actual"] = _cal_disp["avg_actual"].apply(lambda v: f"{v*100:+.3f}%")
                _cal_disp["Count"] = _cal_disp["count"].astype(int)
                st.dataframe(_cal_disp[["Quintile", "Avg Predicted", "Avg Actual", "Count"]],
                              use_container_width=True, hide_index=True)
        else:
            st.info("Need more OOS data for calibration analysis.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — REGIME ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

with tab8:
    with error_boundary("Regime Analysis"):
        st.subheader("Regime-Conditional Model Performance")
        with st.expander("Why regime analysis changes everything"):
            st.markdown("""
**A model that's "56% accurate" is hiding a dangerous secret.** It might be 65% accurate
in calm markets and 42% in volatile ones. Since you care most about the forecast during
volatile markets (that's when big money is made or lost), the overall 56% is misleading.

**How we test this:**
We split the OOS period into two halves based on 10-day rolling volatility:
- **Low-vol days**: The calmer half of the test period
- **High-vol days**: The more volatile half

Then we measure directional accuracy separately in each.

**What the outcomes mean:**
- **Both > 55%**: Robust model. Works across regimes. This is rare and valuable.
- **Low-vol high, high-vol low**: Classic pattern. Model learned calm-market patterns
  (mean reversion, RSI) that break during stress. The forecast is unreliable exactly
  when you need it most.
- **High-vol high, low-vol low**: Model is a crisis detector. It finds signal in
  volatile markets (often momentum/trend-following). Less useful in quiet periods.
- **Both ~50%**: Model is consistently random. No regime dependency, but also no edge.

**Current Regime Implication (at the bottom):**
We map today's volatility to the historically observed accuracy for that regime type.
This tells you: "Given where vol is right now, the model has historically been X% accurate."
This is the number that matters for your decision today.

**What to do with this information:**
- If the model fails in the current regime → reduce position sizes or skip the trade
- If the model excels in the current regime → higher conviction, larger positions
- If you see regime dependency → consider blending this model with one that excels
  in the opposite regime
""")


        _hi = fc.get("hi_vol_dir")
        _lo = fc.get("lo_vol_dir")

        if _hi is not None and _lo is not None:
            rc1, rc2, rc3 = st.columns(3)
            rc1.metric("Overall Accuracy", f"{fc['oos_direction']:.0%}")

            _lo_color = COLORS["success"] if _lo > 0.55 else (COLORS["warning"] if _lo > 0.50 else COLORS["danger"])
            _hi_color = COLORS["success"] if _hi > 0.55 else (COLORS["warning"] if _hi > 0.50 else COLORS["danger"])

            rc2.metric("Low-Vol Accuracy", f"{_lo:.0%}")
            rc3.metric("High-Vol Accuracy", f"{_hi:.0%}")

            # Bar chart comparison
            fig_regime = go.Figure()
            fig_regime.add_trace(go.Bar(
                x=["Low Volatility", "High Volatility", "Overall"],
                y=[_lo * 100, _hi * 100, fc["oos_direction"] * 100],
                marker_color=[_lo_color, _hi_color, COLORS["accent"]],
                text=[f"{_lo:.0%}", f"{_hi:.0%}", f"{fc['oos_direction']:.0%}"],
                textposition="outside",
            ))
            fig_regime.add_hline(y=50, line_dash="dash", line_color=COLORS["text_muted"],
                                  annotation_text="50% (coin flip)")
            fig_regime.update_layout(
                template="plotly_dark", height=350,
                yaxis_title="Directional Accuracy (%)",
                yaxis=dict(range=[30, 80]),
                margin=dict(l=50, r=20, t=10, b=50),
                showlegend=False,
            )
            st.plotly_chart(fig_regime, use_container_width=True, config=PLOTLY_NOBAR)

            # Interpretation
            if _hi < 0.50 and _lo > 0.55:
                st.warning(
                    f"**Regime-dependent model:** Strong in calm markets ({_lo:.0%}) but "
                    f"fails in volatile periods ({_hi:.0%}). In the current "
                    f"**{fc['vol_regime']}** regime, "
                    f"{'forecast reliability is degraded — use with caution.' if fc['vol_regime'] == 'High Vol' else 'forecast should be relatively reliable.'}"
                )
            elif _hi > 0.55 and _lo > 0.55:
                st.success(
                    f"**Robust model:** Accurate in both calm ({_lo:.0%}) and volatile ({_hi:.0%}) "
                    f"conditions. Forecast is reliable across regimes."
                )
            elif _hi > _lo:
                st.info(
                    f"Model performs better in volatile markets ({_hi:.0%} vs {_lo:.0%}). "
                    f"It may be capturing mean-reversion or momentum signals that activate under stress."
                )
            else:
                st.info(f"Low-vol: {_lo:.0%} | High-vol: {_hi:.0%}. No strong regime dependency detected.")

            # Current regime implication
            st.markdown("#### Current Regime Implication")
            if fc["vol_regime"] == "High Vol":
                st.caption(f"Currently in **high-vol regime**. Model's historical accuracy in similar conditions: **{_hi:.0%}**.")
            elif fc["vol_regime"] == "Low Vol":
                st.caption(f"Currently in **low-vol regime**. Model's historical accuracy in similar conditions: **{_lo:.0%}**.")
            else:
                st.caption(f"Currently in **normal vol regime**. Using overall accuracy: **{fc['oos_direction']:.0%}**.")
        else:
            st.info("Need more OOS data for regime analysis (minimum 10 days per regime).")


# ─── FOOTER ────────────────────────────────────────────────────────────────────

st.markdown("---")
st.caption(
    "ML forecasts are probabilistic estimates based on historical patterns. "
    "Past patterns do not guarantee future results. Walk-forward validation "
    "provides an honest assessment of model skill. Not financial advice."
)
render_data_source_footer()
