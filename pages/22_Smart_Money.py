import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import logging
from src.layout import setup_page, error_boundary
from src.edgar import (
    TRACKED_FUNDS, fetch_13f_from_xml, fetch_recent_13d,
    fetch_recent_8k, fetch_congressional_trades, search_filings,
)

logger = logging.getLogger(__name__)

setup_page("22_Smart_Money")

st.title("Smart Money Tracker")
st.markdown("Track institutional holdings, activist investors, and congressional trades — all from SEC EDGAR (public domain).")

tab_13f, tab_congress, tab_activist, tab_8k = st.tabs([
    "13F Holdings",
    "Congressional Trades",
    "Activist Investors",
    "8-K Events",
])


# ── TAB 1: 13F Institutional Holdings ──
with tab_13f, error_boundary("13F Holdings"):
    st.subheader("Institutional Holdings (13F)")
    st.caption("Quarterly filings from funds with >$100M AUM. Data from SEC EDGAR.")

    fund_names = list(TRACKED_FUNDS.keys())
    selected_fund = st.selectbox("Select Fund", fund_names, index=0)
    fund_cik = TRACKED_FUNDS[selected_fund]

    if st.button("Load Holdings", type="primary", key="load_13f"):
        with st.spinner(f"Fetching {selected_fund} 13F from SEC EDGAR..."):
            holdings = fetch_13f_from_xml(fund_cik)

        if not holdings.empty:
            st.success(f"Found {len(holdings)} positions. Filed: {holdings['filing_date'].iloc[0] if 'filing_date' in holdings.columns else 'N/A'}")

            # Top holdings chart
            if "value" in holdings.columns:
                top = holdings.head(15)
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    y=top["company"], x=top["value"] / 1e6,
                    orientation="h", marker_color="#00d1ff",
                ))
                fig.update_layout(
                    template="plotly_dark", height=400,
                    title=f"{selected_fund} — Top Holdings by Value ($M)",
                    xaxis_title="Value ($M)", yaxis=dict(autorange="reversed"),
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

            # Full holdings table
            display_cols = [c for c in ["company", "class", "shares", "value", "cusip"] if c in holdings.columns]
            if display_cols:
                df_display = holdings[display_cols].copy()
                if "value" in df_display.columns:
                    df_display["value"] = df_display["value"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "N/A")
                if "shares" in df_display.columns:
                    df_display["shares"] = df_display["shares"].apply(lambda x: f"{x:,.0f}" if pd.notna(x) else "N/A")
                st.dataframe(df_display, use_container_width=True, hide_index=True)
        else:
            st.warning("No 13F data found. The fund may not have filed recently, or the filing format may differ.")


# ── TAB 2: Congressional Trades ──
with tab_congress, error_boundary("Congressional Trades"):
    st.subheader("Congressional Stock Trades")
    st.caption("Politicians must disclose trades within 45 days under the STOCK Act. Data from SEC EDGAR.")

    if st.button("Load Recent Trades", type="primary", key="load_congress"):
        with st.spinner("Searching SEC EDGAR for congressional disclosures..."):
            trades = fetch_congressional_trades()

        if not trades.empty:
            st.success(f"Found {len(trades)} recent disclosures.")
            st.dataframe(trades, use_container_width=True, hide_index=True)
        else:
            st.info("No recent congressional trade disclosures found via EDGAR search. "
                   "Congressional trades are filed via the Senate eFD and House disclosure portals, "
                   "which have limited EDGAR integration.")
            st.markdown(
                "**External sources for congressional trades:**\n"
                "- [Senate eFD](https://efdsearch.senate.gov/search/)\n"
                "- [House Disclosures](https://disclosures-clerk.house.gov/)\n"
                "- [Quiver Quantitative](https://www.quiverquant.com/congresstrading/)"
            )


# ── TAB 3: Activist Investors ──
with tab_activist, error_boundary("Activist Investors"):
    st.subheader("Activist Investor Positions (13D Filings)")
    st.caption("Filed when someone acquires >5% of a company with intent to influence. Often precedes major price moves.")

    days = st.slider("Lookback (days)", 30, 365, 90, key="activist_days")

    if st.button("Search 13D Filings", type="primary", key="load_13d"):
        with st.spinner("Searching SEC EDGAR for 13D filings..."):
            filings = fetch_recent_13d(days=days)

        if filings:
            st.success(f"Found {len(filings)} recent 13D filings.")
            for f in filings:
                filed = f.get("filed", "")
                company = f.get("company", "")
                form = f.get("form", "")
                st.markdown(
                    f'<div style="padding:8px 12px;border-left:3px solid #ffaa00;margin-bottom:6px;'
                    f'background:rgba(255,255,255,0.02);border-radius:0 6px 6px 0;">'
                    f'<span style="color:#888;font-size:0.8rem;">{filed}</span> &nbsp;'
                    f'<span style="color:#ffaa00;font-weight:600;">{form}</span> &nbsp;'
                    f'<span style="color:#e0e0e0;">{company}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("No recent 13D filings found.")


# ── TAB 4: 8-K Material Events ──
with tab_8k, error_boundary("8-K Events"):
    st.subheader("Material Events (8-K Filings)")
    st.caption("Major corporate events: earnings, M&A, leadership changes, contract awards. Filed within days of the event.")

    _c1, _c2, _c3 = st.columns([2, 1, 1])
    with _c1:
        search_ticker = st.text_input("Search Ticker or Keyword", value="", key="8k_search")
    with _c2:
        search_days = st.slider("Lookback (days)", 7, 365, 30, key="8k_days")
    with _c3:
        st.markdown("<br>", unsafe_allow_html=True)
        search_btn = st.button("Search", type="primary", key="search_8k", use_container_width=True)

    if search_btn and search_ticker:
        with st.spinner("Searching SEC EDGAR..."):
            events = fetch_recent_8k(search_ticker, days=search_days)

        if events:
            st.success(f"Found {len(events)} 8-K filings for '{search_ticker}'.")
            for evt in events:
                filed = evt.get("filed", "")
                company = evt.get("company", "")
                form = evt.get("form", "8-K")
                st.markdown(
                    f'<div style="padding:6px 10px;border-left:2px solid #00d1ff;margin-bottom:4px;'
                    f'background:rgba(255,255,255,0.02);border-radius:0 4px 4px 0;">'
                    f'<span style="color:#888;font-size:0.78rem;">{filed}</span> &nbsp;'
                    f'<span style="color:#00d1ff;font-weight:600;">{form}</span> &nbsp;'
                    f'<span style="color:#ccc;">{company}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info(f"No 8-K filings found for '{search_ticker}' in the last {search_days} days.")
