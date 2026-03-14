@st.cache_data(show_spinner="Training Advanced Multi-Output ML Forecast...")
def predict_30d_random_forest(px_close: pd.Series, n_estimators: int = 200, lookback_days: int = 1000):
    """
    Advanced Direct Multi-Step Random Forest.
    Predicts stationary returns, utilizes momentum features (RSI/MACD), 
    and generates uncertainty bands via tree-level simulation paths.
    """
    if len(px_close) < 100:
        return np.empty((0, 0)), pd.DatetimeIndex([])

    df = pd.DataFrame(px_close).rename(columns={px_close.name: 'Close'})
    
    # 1. STATIONARITY: Predict Returns, not Prices
    df['Returns'] = df['Close'].pct_change()
    
    # 2. ADVANCED FEATURE ENGINEERING
    # Autoregressive Lags
    for l in [1, 2, 3, 5, 10]:
        df[f'lag_ret_{l}'] = df['Returns'].shift(l)
        
    # Volatility Cluster Detection
    df['Vol_20'] = df['Returns'].rolling(20).std()
    
    # RSI (14-day Wilder's Smoothing)
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss
    df['RSI_14'] = 100 - (100 / (1 + rs))
    
    # MACD & Momentum
    ema_12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema_12 - ema_26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
    
    # 3. DIRECT MULTI-STEP TARGETS
    # Instead of a recursive loop, we predict T+1 through T+30 simultaneously
    T = 30
    for i in range(1, T + 1):
        df[f'Target_Ret_{i}'] = df['Returns'].shift(-i)
        
    df = df.dropna().tail(lookback_days)
    
    features = [c for c in df.columns if 'lag' in c or 'Vol' in c or 'RSI' in c or 'MACD' in c]
    targets = [f'Target_Ret_{i}' for i in range(1, T + 1)]
    
    X = df[features]
    y = df[targets]
    
    # 4. TRAIN MULTI-OUTPUT MODEL
    model = RandomForestRegressor(n_estimators=n_estimators, max_depth=10, random_state=42, n_jobs=-1)
    model.fit(X, y)
    
    # 5. GENERATE SIMULATION PATHS FROM TREE VARIANCE
    current_features = X.iloc[-1:].values
    current_price = px_close.iloc[-1]
    
    # Extract the (30,) return prediction from every individual tree
    tree_preds = [tree.predict(current_features)[0] for tree in model.estimators_]
    tree_preds = np.array(tree_preds) # Shape: (n_estimators, 30)
    
    # Convert forecasted daily returns into cumulative price paths
    tree_growth = 1.0 + tree_preds
    tree_cum_returns = np.cumprod(tree_growth, axis=1)
    tree_price_paths = current_price * tree_cum_returns 
    
    # Calculate P5, P50, and P95 across the simulated price paths
    p50_path = np.percentile(tree_price_paths, 50, axis=0)
    p5_path = np.percentile(tree_price_paths, 5, axis=0)
    p95_path = np.percentile(tree_price_paths, 95, axis=0)
    
    last_ts = pd.Timestamp(px_close.index[-1])
    future_dates = pd.bdate_range(start=last_ts + pd.Timedelta(days=1), periods=T)
    
    return {
        'mean': p50_path,
        'lower': p5_path,
        'upper': p95_path
    }, future_dates
