import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

st.title("📈 Simple Dashboard")

# Fake sample data
np.random.seed(0)
dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=30)
values = np.cumsum(np.random.randn(len(dates))) + 100
df = pd.DataFrame({"date": dates, "value": values})

# KPIs
c1, c2, c3 = st.columns(3)
c1.metric("Latest", f"{df['value'].iloc[-1]:.2f}")
c2.metric("Δ7d", f"{df['value'].iloc[-1] - df['value'].iloc[-7]:.2f}")
c3.metric("30d High", f"{df['value'].max():.2f}")

# Chart
fig = px.line(df, x="date", y="value", title="Trend")
st.plotly_chart(fig, use_container_width=True)

# Data table
st.subheader("Raw Data")
st.dataframe(df.tail(10), use_container_width=True)
