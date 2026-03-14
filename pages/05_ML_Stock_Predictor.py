import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from src.data_engine import fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.chatbot import run_sidebar_chatbot

st.title("🧠 AI Trend Predictor (Random Forest)")
st.markdown("Trains a Machine Learning classifier on the fly to predict tomorrow's price direction based on engineered technical features.")

# --- Sidebar Controls ---
with st.sidebar:
    st.header("Model Parameters")
    with st.form("ml_settings"):
        raw_ticker = st.text_input("Ticker", value="QQQ")
        train_days = st.slider("Training Data (Days)", 365, 2000, 1000, step=100)
        estimators = st.slider("Number of Trees (Estimators)", 50, 500, 100, step=50)
        submit_button = st.form_submit_button(label="⚙️ Train & Predict")

# --- Execution ---
if submit_button:
    ticker = format_massive_ticker(raw_ticker)
    
    with st.spinner(f"Fetching data and training AI for {ticker}..."):
        # 1. Fetch Data
        df = fetch_massive_data(ticker, train_days)
        
        if df is not None and not df.empty and len(df) > 50:
            # 2. Feature Engineering
            data = df.copy()
            data['Returns'] = data['Close'].pct_change()
            data['SMA_10'] = data['Close'].rolling(window=10).mean()
            data['SMA_50'] = data['Close'].rolling(window=50).mean()
            data['Vol_10'] = data['Returns'].rolling(window=10).std()
            
            # Distance from moving averages
            data['Dist_SMA10'] = (data['Close'] - data['SMA_10']) / data['SMA_10']
            data['Dist_SMA50'] = (data['Close'] - data['SMA_50']) / data['SMA_50']
            
            # The Target: 1 if tomorrow goes up, 0 if it goes down
            data['Target'] = np.where(data['Close'].shift(-1) > data['Close'], 1, 0)
            
            # Drop NaNs created by rolling windows
            data = data.dropna()
            
            # 3. Train / Test Split (Chronological to prevent data leakage)
            features = ['Returns', 'Dist_SMA10', 'Dist_SMA50', 'Vol_10']
            
            X = data[features]
            y = data['Target']
            
            # Use last 80% for training, 20% for testing
            split_idx = int(len(data) * 0.8)
            X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:-1] # -1 excludes today's target
            y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:-1]
            
            # The row we want to predict (Today's features)
            X_predict = X.iloc[-1:] 
            
            # 4. Train the Random Forest Model
            model = RandomForestClassifier(n_estimators=estimators, random_state=42, max_depth=5)
            model.fit(X_train, y_train)
            
            # 5. Evaluate Model
            predictions = model.predict(X_test)
            accuracy = accuracy_score(y_test, predictions)
            
            # 6. Make Tomorrow's Prediction
            tomorrow_pred = model.predict(X_predict)[0]
            pred_text = "📈 UP" if tomorrow_pred == 1 else "📉 DOWN"
            pred_prob = model.predict_proba(X_predict)[0]
            confidence = pred_prob[1] if tomorrow_pred == 1 else pred_prob[0]

            # --- UI Display ---
            st.subheader("🔮 Prediction for Tomorrow")
            
            # Highlight metric
            c1, c2, c3 = st.columns(3)
            c1.metric("Current Price", f"${data['Close'].iloc[-1]:.2f}")
            c2.metric("AI Prediction", pred_text)
            c3.metric("Model Confidence", f"{confidence * 100:.1f}%")
            
            st.markdown("---")
            
            # Feature Importance & Backtest columns
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("Historical Accuracy")
                st.write(f"When tested against unseen historical data, this model structure was correct **{accuracy * 100:.1f}%** of the time.")
                st.info("Note: Stock market data is highly noisy. Accuracies between 52% and 58% are standard for basic directional classifiers.")
            
            with col2:
                # Plot Feature Importance
                st.subheader("Feature Importance")
                importance = model.feature_importances_
                
                fig = go.Figure(go.Bar(
                    x=importance,
                    y=features,
                    orientation='h',
                    marker_color='#00d1ff'
                ))
                fig.update_layout(
                    template="plotly_dark",
                    height=250,
                    margin=dict(l=0, r=0, t=0, b=0),
                    xaxis_title="Importance Weight",
                    yaxis_title="Feature"
                )
                st.plotly_chart(fig, use_container_width=True)

            # AI Chatbot integration
            ctx = f"Trained a Random Forest model on {ticker}. The model accuracy is {accuracy*100:.1f}%. It predicts the price will go {pred_text} tomorrow with {confidence*100:.1f}% confidence. The most important feature was {features[np.argmax(importance)]}."
            run_sidebar_chatbot(ctx)

        else:
            st.error("Not enough historical data to train the model. Try increasing the training days.")
            
    render_data_source_footer()
