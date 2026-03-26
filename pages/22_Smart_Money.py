import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import logging
from src.layout import setup_page, error_boundary
from src.edgar import (
    TRACKED_FUNDS, fetch_13f_from_xml, fetch_recent_13d,
    fetch_recent_8k, fetch_congressional_trades,
    fetch_parsed_congressional_trades, search_filings,
    fetch_guidance_history, fetch_recent_earnings_calendar,
    fetch_transcript_guidance, discover_fool_transcript_urls,
)
from src.market_data import (
    fetch_analyst_estimates, fetch_earnings_history,
    fetch_institutional_holders, fetch_insider_transactions,
    fetch_fred_macro_dashboard, fetch_fred_series, FRED_SERIES,
)

logger = logging.getLogger(__name__)

setup_page("22_Smart_Money")

PLOTLY_NOBAR = {"displayModeBar": False}

st.title("Smart Money Tracker")
st.markdown("Track institutional holdings, activist investors, congressional trades, company guidance, and macro indicators — all from public data sources.")

tab_13f, tab_congress, tab_activist, tab_guidance, tab_macro, tab_8k = st.tabs([
    "13F Holdings",
    "Congressional Trades",
    "Activist Investors",
    "Company Guidance",
    "Macro & Rates",
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
        st.session_state["13f_fund"] = selected_fund

    if st.session_state.get("13f_fund"):
        active_fund = st.session_state["13f_fund"]
        active_cik = TRACKED_FUNDS[active_fund]
        with st.spinner(f"Fetching {active_fund} 13F from SEC EDGAR..."):
            holdings = fetch_13f_from_xml(active_cik)

        if not holdings.empty:
            st.success(f"Found {len(holdings)} positions for {active_fund}. Filed: {holdings['filing_date'].iloc[0] if 'filing_date' in holdings.columns else 'N/A'}")

            # Top holdings chart
            if "value" in holdings.columns:
                top = holdings.head(15)
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    y=top["company"], x=top["value"] / 1e6,
                    orientation="h", marker_color="#00d1ff",
                    hovertemplate="%{y}<br>$%{x:,.0f}M<extra></extra>",
                ))
                fig.update_layout(
                    template="plotly_dark", height=400,
                    title=f"{active_fund} — Top Holdings by Value ($M)",
                    xaxis_title="Value ($M)", yaxis=dict(autorange="reversed"),
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig, use_container_width=True, config=PLOTLY_NOBAR)

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
            st.warning(f"No 13F data found for {active_fund}. The fund may not have filed recently, or the filing format may differ.")


# ── TAB 2: Congressional Trades ──
with tab_congress, error_boundary("Congressional Trades"):
    st.subheader("Congressional Stock Trades")
    st.caption("House members must disclose trades within 45 days under the STOCK Act. Data parsed from official PTR filings (clerk.house.gov).")

    _cc1, _cc2, _cc3 = st.columns([1.5, 1.5, 1])
    with _cc1:
        congress_year = st.selectbox("Year", [2026, 2025, 2024], index=0, key="congress_year")
    with _cc2:
        max_filings = st.selectbox("Filings to parse", [25, 50, 100, 200], index=1, key="max_filings",
                                   help="More filings = more data but slower load")
    with _cc3:
        st.markdown("<br>", unsafe_allow_html=True)
        load_congress = st.button("Analyze Trades", type="primary", key="load_congress", use_container_width=True)

    if load_congress:
        st.session_state["congress_loaded"] = True

    if st.session_state.get("congress_loaded"):
        trades = fetch_parsed_congressional_trades(year=congress_year, max_filings=max_filings)

        if not trades.empty:
            buys = trades[trades["type"] == "Purchase"]
            sells = trades[trades["type"] == "Sale"]

            # ── Metrics row ──
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Trades", f"{len(trades):,}")
            m2.metric("Unique Tickers", trades["ticker"].nunique())
            m3.metric("Purchases", len(buys), delta=f"{len(buys)/(len(trades) or 1)*100:.0f}%")
            m4.metric("Sales", len(sells), delta=f"{len(sells)/(len(trades) or 1)*100:.0f}%", delta_color="inverse")

            st.markdown("---")

            # ── Most bought & most sold side by side ──
            col_buy, col_sell = st.columns(2)
            with col_buy:
                top_bought = buys["ticker"].value_counts().head(12)
                if not top_bought.empty:
                    fig_b = go.Figure()
                    fig_b.add_trace(go.Bar(
                        y=top_bought.index, x=top_bought.values,
                        orientation="h", marker_color="#00d1ff",
                    ))
                    fig_b.update_layout(
                        template="plotly_dark", height=380,
                        title="Most Purchased Tickers",
                        xaxis_title="# of Buy Transactions",
                        yaxis=dict(autorange="reversed"),
                        margin=dict(l=0, r=0, t=40, b=0),
                    )
                    st.plotly_chart(fig_b, use_container_width=True, config=PLOTLY_NOBAR)

            with col_sell:
                top_sold = sells["ticker"].value_counts().head(12)
                if not top_sold.empty:
                    fig_s = go.Figure()
                    fig_s.add_trace(go.Bar(
                        y=top_sold.index, x=top_sold.values,
                        orientation="h", marker_color="#ff6b6b",
                    ))
                    fig_s.update_layout(
                        template="plotly_dark", height=380,
                        title="Most Sold Tickers",
                        xaxis_title="# of Sell Transactions",
                        yaxis=dict(autorange="reversed"),
                        margin=dict(l=0, r=0, t=40, b=0),
                    )
                    st.plotly_chart(fig_s, use_container_width=True, config=PLOTLY_NOBAR)

            # ── Most active members ──
            top_members = trades["member"].value_counts().head(10)
            fig_m = go.Figure()
            fig_m.add_trace(go.Bar(
                y=top_members.index, x=top_members.values,
                orientation="h", marker_color="#ffaa00",
            ))
            fig_m.update_layout(
                template="plotly_dark", height=350,
                title="Most Active Members (by # of Trades)",
                xaxis_title="Number of Trades",
                yaxis=dict(autorange="reversed"),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_m, use_container_width=True, config=PLOTLY_NOBAR)

            # ── Filters + full trade table ──
            st.subheader("Trade Details")
            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                type_filter = st.multiselect("Type", ["Purchase", "Sale"], default=["Purchase", "Sale"], key="txn_type_filter")
            with fc2:
                all_members = sorted(trades["member"].unique())
                member_filter = st.multiselect("Member", all_members, default=[], key="member_filter",
                                               placeholder="All members")
            with fc3:
                ticker_search = st.text_input("Ticker search", value="", key="ticker_search",
                                              placeholder="e.g. AAPL, TSLA")

            filtered = trades[trades["type"].isin(type_filter)]
            if member_filter:
                filtered = filtered[filtered["member"].isin(member_filter)]
            if ticker_search:
                tickers = [t.strip().upper() for t in ticker_search.split(",")]
                filtered = filtered[filtered["ticker"].isin(tickers)]

            display_cols = ["member", "state", "ticker", "type", "date", "amount"]
            df_show = filtered[display_cols].copy()
            df_show["date"] = df_show["date"].dt.strftime("%Y-%m-%d")
            df_show.columns = ["Member", "State", "Ticker", "Type", "Trade Date", "Amount"]
            st.dataframe(df_show, use_container_width=True, hide_index=True, height=450)
        else:
            st.warning(f"No trade data could be parsed for {congress_year}.")

    st.markdown(
        "<div style='margin-top:12px;padding:8px;border:1px solid #333;border-radius:6px;font-size:0.82rem;color:#888;'>"
        "<b>Note:</b> Senate trades are filed separately via the "
        "<a href='https://efdsearch.senate.gov/search/' style='color:#00d1ff;'>Senate eFD portal</a>. "
        "House data shown here is parsed directly from official PTR filings."
        "</div>",
        unsafe_allow_html=True,
    )


# ── TAB 3: Activist Investors ──
with tab_activist, error_boundary("Activist Investors"):
    st.subheader("Activist Investor Positions (13D Filings)")
    st.caption("Filed when someone acquires >5% of a company with intent to influence. Often precedes major price moves.")

    _ac1, _ac2, _ac3 = st.columns([1.5, 1.5, 1])
    with _ac1:
        days = st.slider("Lookback (days)", 30, 365, 90, key="activist_days")
    with _ac2:
        ticker_13d = st.text_input("Search ticker", value="", key="ticker_13d",
                                   placeholder="e.g. CVNA, PAYC")
    with _ac3:
        st.markdown("<br>", unsafe_allow_html=True)
        load_13d = st.button("Search 13D Filings", type="primary", key="load_13d", use_container_width=True)

    if load_13d:
        st.session_state["13d_loaded"] = True

    if st.session_state.get("13d_loaded"):
        with st.spinner("Searching SEC EDGAR for 13D filings..."):
            filings = fetch_recent_13d(days=days)

        if not filings.empty:
            # Apply ticker filter if provided
            if ticker_13d.strip():
                search_tickers = [t.strip().upper() for t in ticker_13d.split(",")]
                filings = filings[filings["ticker"].isin(search_tickers)]
                if filings.empty:
                    st.warning(f"No 13D filings found for {', '.join(search_tickers)}.")

            if not filings.empty:
                new_filings = filings[filings["is_new"]]
                amendments = filings[~filings["is_new"]]

                # ── Metrics ──
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Total Filings", len(filings))
                m2.metric("New Positions", len(new_filings),
                          help="Initial SC 13D — someone just crossed the 5% threshold")
                m3.metric("Amendments", len(amendments),
                          help="SC 13D/A — updates to existing activist positions")
                m4.metric("Unique Targets", filings["target"].nunique())

                st.markdown("---")

                # ── New positions highlight ──
                if not new_filings.empty:
                    st.markdown("##### New Activist Positions (Initial 13D)")
                    for _, row in new_filings.iterrows():
                        ticker_badge = f'<span style="background:#ffaa00;color:#000;padding:2px 8px;border-radius:4px;font-weight:700;font-size:0.85rem;">{row["ticker"]}</span> ' if row["ticker"] else ""
                        link = f' <a href="{row["url"]}" target="_blank" style="color:#666;font-size:0.75rem;">Filing →</a>' if row["url"] else ""
                        st.markdown(
                            f'<div style="padding:10px 14px;border-left:4px solid #00d1ff;margin-bottom:8px;'
                            f'background:rgba(0,209,255,0.04);border-radius:0 6px 6px 0;border:1px solid #333;">'
                            f'{ticker_badge}'
                            f'<span style="color:#e0e0e0;font-weight:600;">{row["target"][:60]}</span><br>'
                            f'<span style="color:#888;font-size:0.82rem;">Activist: </span>'
                            f'<span style="color:#ffaa00;font-size:0.82rem;">{row["activist"][:55]}</span> &nbsp;'
                            f'<span style="color:#666;font-size:0.78rem;">Filed {str(row["filed"])[:10]}</span>'
                            f'{link}'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                    st.markdown("")

                # ── Most active activists chart ──
                top_activists = filings["activist"].value_counts().head(10)
                if len(top_activists) > 1:
                    fig_act = go.Figure()
                    fig_act.add_trace(go.Bar(
                        y=top_activists.index, x=top_activists.values,
                        orientation="h", marker_color="#ffaa00",
                    ))
                    fig_act.update_layout(
                        template="plotly_dark", height=350,
                        title="Most Active Filers",
                        xaxis_title="Number of 13D Filings",
                        yaxis=dict(autorange="reversed"),
                        margin=dict(l=0, r=0, t=40, b=0),
                    )
                    st.plotly_chart(fig_act, use_container_width=True, config=PLOTLY_NOBAR)

                # ── Filing timeline ──
                if len(filings) > 5:
                    timeline = filings.copy()
                    timeline["week"] = timeline["filed"].dt.to_period("W").dt.start_time
                    weekly = timeline.groupby(["week", "is_new"]).size().reset_index(name="count")
                    fig_tl = go.Figure()
                    for is_new, color, name in [(True, "#00d1ff", "New 13D"), (False, "#555", "Amendment")]:
                        subset = weekly[weekly["is_new"] == is_new]
                        if not subset.empty:
                            fig_tl.add_trace(go.Bar(
                                x=subset["week"], y=subset["count"],
                                name=name, marker_color=color,
                            ))
                    fig_tl.update_layout(
                        template="plotly_dark", height=280, barmode="stack",
                        title="Filing Activity by Week",
                        yaxis_title="Filings", legend=dict(orientation="h", y=-0.15),
                        margin=dict(l=0, r=0, t=40, b=0),
                    )
                    st.plotly_chart(fig_tl, use_container_width=True, config=PLOTLY_NOBAR)

                # ── Full table ──
                st.subheader("All Filings")
                _f1, _f2 = st.columns(2)
                with _f1:
                    show_type = st.multiselect("Filing type", ["New 13D", "Amendment"],
                                               default=["New 13D", "Amendment"], key="13d_type_filter")
                with _f2:
                    activist_filter = st.multiselect("Activist", sorted(filings["activist"].unique()),
                                                     default=[], key="activist_filter",
                                                     placeholder="All activists")

                display = filings.copy()
                type_map = {"New 13D": True, "Amendment": False}
                selected_new = [type_map[t] for t in show_type]
                display = display[display["is_new"].isin(selected_new)]
                if activist_filter:
                    display = display[display["activist"].isin(activist_filter)]

                df_show = display[["filed", "form", "ticker", "target", "activist", "url"]].copy()
                df_show["filed"] = df_show["filed"].dt.strftime("%Y-%m-%d")
                df_show.columns = ["Filed", "Form", "Ticker", "Target", "Activist", "Filing"]
                st.dataframe(
                    df_show, use_container_width=True, hide_index=True, height=400,
                    column_config={"Filing": st.column_config.LinkColumn("Filing", display_text="View")},
                )
        else:
            st.info("No recent 13D filings found.")


# ── TAB 4: Company Guidance ──
with tab_guidance, error_boundary("Company Guidance"):
    st.subheader("Company Guidance Tracker")
    st.caption("Forward guidance from SEC 8-K press releases and Motley Fool earnings call transcripts.")

    _gc1, _gc2, _gc3 = st.columns([2, 1, 1])
    with _gc1:
        guidance_ticker = st.text_input("Ticker", value="NVDA", key="guidance_ticker",
                                        placeholder="e.g. NVDA, AMZN, AAPL")
    with _gc2:
        guidance_quarters = st.selectbox("Quarters", [4, 6, 8, 10], index=1, key="guidance_quarters")
    with _gc3:
        st.markdown("<br>", unsafe_allow_html=True)
        load_guidance = st.button("Load Guidance", type="primary", key="load_guidance", use_container_width=True)

    # Transcript URLs input
    transcript_urls_raw = st.text_area(
        "Motley Fool transcript URLs (optional — paste one per line for earnings call guidance)",
        value="", height=68, key="transcript_urls",
        placeholder="https://www.fool.com/earnings/call-transcripts/2026/02/25/nvidia-nvda-q4-2026-earnings-call-transcript/",
    )

    if load_guidance and guidance_ticker.strip():
        ticker_clean = guidance_ticker.strip().upper()

        # ── Source 1: 8-K press releases ──
        with st.spinner(f"Searching 8-K press releases for {ticker_clean}..."):
            guidance_df = fetch_guidance_history(ticker_clean, num_quarters=guidance_quarters)

        # ── Source 2: Earnings call transcripts ──
        transcript_urls = [u.strip() for u in transcript_urls_raw.strip().split("\n") if u.strip().startswith("http")]
        call_df = pd.DataFrame()
        if not transcript_urls:
            # Auto-discover transcript URLs from Motley Fool
            with st.spinner(f"Searching for {ticker_clean} earnings call transcripts on Motley Fool..."):
                transcript_urls = discover_fool_transcript_urls(ticker_clean, limit=4)
            if transcript_urls:
                st.caption(f"Auto-discovered {len(transcript_urls)} transcript(s) on Motley Fool.")
        if transcript_urls:
            with st.spinner(f"Parsing {len(transcript_urls)} earnings call transcript(s)..."):
                call_df = fetch_transcript_guidance(ticker_clean, transcript_urls)

        # Combine both sources
        all_dfs = []
        if not guidance_df.empty:
            guidance_df["source"] = "8-K Press Release"
            all_dfs.append(guidance_df)
        if not call_df.empty:
            call_df["source"] = "Earnings Call"
            # Ensure matching columns
            for col in ["revenue", "revenue_high", "gross_margin", "eps", "eps_high", "opex",
                        "operating_income", "oi_high", "outlook"]:
                if col not in call_df.columns:
                    call_df[col] = None
            all_dfs.append(call_df)

        if all_dfs:
            combined = pd.concat(all_dfs, ignore_index=True)
            combined["filed"] = pd.to_datetime(combined["filed"], errors="coerce")
            combined = combined.sort_values("filed").reset_index(drop=True)

            n_press = len(guidance_df) if not guidance_df.empty else 0
            n_call = len(call_df) if not call_df.empty else 0
            sources = []
            if n_press:
                sources.append(f"{n_press} from 8-K press releases")
            if n_call:
                sources.append(f"{n_call} from earnings calls")
            st.success(f"Found guidance for {ticker_clean}: {', '.join(sources)}.")

            # ── Revenue guidance trend ──
            if combined["revenue"].notna().any():
                fig_rev = go.Figure()
                labels = combined["quarter"].fillna(combined["filed"].dt.strftime("%Y-%m-%d"))

                # Color by source
                for src, color, dash in [("8-K Press Release", "#00d1ff", "solid"), ("Earnings Call", "#ffaa00", "dash")]:
                    mask = combined["source"] == src
                    sub = combined[mask]
                    if sub["revenue"].notna().any():
                        sub_labels = sub["quarter"].fillna(sub["filed"].dt.strftime("%Y-%m-%d"))
                        fig_rev.add_trace(go.Scatter(
                            x=sub_labels, y=sub["revenue"] / 1e9,
                            mode="lines+markers", name=f"Revenue ({src})",
                            line=dict(color=color, width=3, dash=dash),
                            marker=dict(size=10),
                        ))
                        if sub["revenue_high"].notna().any():
                            fig_rev.add_trace(go.Scatter(
                                x=sub_labels, y=sub["revenue_high"] / 1e9,
                                mode="markers", name=f"Revenue High ({src})",
                                line=dict(color=color, width=1, dash="dot"),
                                marker=dict(size=6, symbol="diamond"),
                            ))

                fig_rev.update_layout(
                    template="plotly_dark", height=380,
                    title=f"{ticker_clean} — Revenue Guidance Trend ($B)",
                    yaxis_title="Revenue ($B)",
                    margin=dict(l=0, r=0, t=40, b=0),
                    legend=dict(orientation="h", y=-0.15),
                )
                st.plotly_chart(fig_rev, use_container_width=True, config=PLOTLY_NOBAR)

            # ── Margin + OpEx side by side ──
            has_gm = combined["gross_margin"].notna().any()
            has_opex = combined.get("opex") is not None and combined["opex"].notna().any()
            if has_gm or has_opex:
                col_gm, col_opex = st.columns(2)
                labels = combined["quarter"].fillna(combined["filed"].dt.strftime("%Y-%m-%d"))

                if has_gm:
                    with col_gm:
                        gm_data = combined[combined["gross_margin"].notna()]
                        gm_labels = gm_data["quarter"].fillna(gm_data["filed"].dt.strftime("%Y-%m-%d"))
                        colors = ["#00d1ff" if s == "8-K Press Release" else "#ffaa00" for s in gm_data["source"]]
                        fig_gm = go.Figure()
                        fig_gm.add_trace(go.Bar(
                            x=gm_labels, y=gm_data["gross_margin"],
                            marker_color=colors,
                        ))
                        fig_gm.update_layout(
                            template="plotly_dark", height=300,
                            title="Gross Margin Guidance (%)",
                            yaxis_title="%",
                            margin=dict(l=0, r=0, t=40, b=0),
                        )
                        st.plotly_chart(fig_gm, use_container_width=True, config=PLOTLY_NOBAR)

                if has_opex:
                    with col_opex:
                        fig_opex = go.Figure()
                        fig_opex.add_trace(go.Bar(
                            x=labels, y=combined["opex"] / 1e9,
                            name="OpEx ($B)", marker_color="#ff6b6b",
                        ))
                        fig_opex.update_layout(
                            template="plotly_dark", height=300,
                            title="Operating Expenses Guidance ($B)",
                            yaxis_title="OpEx ($B)",
                            margin=dict(l=0, r=0, t=40, b=0),
                        )
                        st.plotly_chart(fig_opex, use_container_width=True, config=PLOTLY_NOBAR)

            # ── Summary table ──
            st.subheader("Guidance History")

            def fmt_dollar(v, scale=1e9, suffix="B"):
                if pd.isna(v) or v is None:
                    return ""
                return f"${v / scale:,.1f}{suffix}"

            def fmt_range(row, col, col_high, scale=1e9, suffix="B"):
                low = row.get(col)
                high = row.get(col_high)
                if pd.isna(low) or low is None:
                    # Check for growth rate
                    gl = row.get("revenue_growth_low")
                    gh = row.get("revenue_growth_high")
                    if pd.notna(gl):
                        return f"+{gl:.0f}% – +{gh:.0f}% YoY"
                    return ""
                s = f"${low / scale:,.1f}{suffix}"
                if pd.notna(high) and high is not None and high != low:
                    s += f" – ${high / scale:,.1f}{suffix}"
                return s

            display_rows = []
            for _, row in combined.iterrows():
                display_rows.append({
                    "Filed": str(row["filed"])[:10] if pd.notna(row["filed"]) else "",
                    "Source": row.get("source", ""),
                    "Quarter": row.get("quarter", "") or "",
                    "Revenue": fmt_range(row, "revenue", "revenue_high"),
                    "Gross Margin": f"{row['gross_margin']:.1f}%" if pd.notna(row.get("gross_margin")) else "",
                    "EPS": f"${row['eps']:.2f}" if pd.notna(row.get("eps")) else "",
                    "OpEx": fmt_dollar(row.get("opex")),
                })
            st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)

            # ── Raw outlook text (expandable) ──
            with st.expander("Raw Outlook Text"):
                for _, row in combined.iterrows():
                    outlook = row.get("outlook", "")
                    if outlook:
                        src_badge = "8-K" if row.get("source") == "8-K Press Release" else "Call"
                        st.markdown(f"**[{src_badge}] {row.get('quarter') or str(row['filed'])[:10]}**")
                        st.text(outlook[:600])
                        st.markdown("---")
        else:
            st.warning(
                f"No guidance data found for {ticker_clean} from 8-K press releases.\n\n"
                "**To add earnings call data:** paste Motley Fool transcript URLs above. "
                "Find them at [fool.com/earnings-call-transcripts](https://www.fool.com/earnings-call-transcripts/) "
                "or search Google for `site:fool.com {ticker} earnings call transcript`."
            )

        # ── Yahoo Finance: Analyst Estimates, Earnings Surprises, Insider Trades ──
        st.markdown("---")
        st.subheader(f"Wall Street Consensus — {ticker_clean}")
        st.caption("Analyst estimates, price targets, earnings surprises, and insider activity via Yahoo Finance.")

        with st.spinner("Loading analyst data..."):
            yf_data = fetch_analyst_estimates(ticker_clean)
            yf_earnings = fetch_earnings_history(ticker_clean)
            yf_insiders = fetch_insider_transactions(ticker_clean)

        if yf_data:
            # ── Analyst metrics row ──
            am1, am2, am3, am4, am5 = st.columns(5)
            price = yf_data.get("current_price")
            target = yf_data.get("price_target_mean")
            am1.metric("Price", f"${price:,.2f}" if price else "N/A")
            if target and price:
                upside = (target - price) / price * 100
                am2.metric("Target (Mean)", f"${target:,.0f}", delta=f"{upside:+.1f}%")
            else:
                am2.metric("Target (Mean)", f"${target:,.0f}" if target else "N/A")
            am3.metric("Recommendation", (yf_data.get("recommendation") or "N/A").replace("_", " ").title())
            am4.metric("Forward P/E", f"{yf_data['forward_pe']:.1f}" if yf_data.get("forward_pe") else "N/A")
            am5.metric("Short % Float", f"{yf_data['short_pct_float']*100:.1f}%" if yf_data.get("short_pct_float") else "N/A")

            # ── Consensus estimates ──
            est_cols = st.columns(2)
            with est_cols[0]:
                rev_q = yf_data.get("rev_est_current_q")
                rev_y = yf_data.get("rev_est_current_y")
                st.markdown("**Revenue Estimates**")
                if rev_q:
                    st.markdown(f"- Current Quarter: **${rev_q/1e9:,.1f}B**")
                if rev_y:
                    growth = yf_data.get("rev_growth_current_y")
                    g_str = f" ({growth*100:+.0f}% YoY)" if growth else ""
                    st.markdown(f"- Current Year: **${rev_y/1e9:,.1f}B**{g_str}")
            with est_cols[1]:
                eps_q = yf_data.get("eps_est_current_q")
                eps_y = yf_data.get("eps_est_current_y")
                eps_ny = yf_data.get("eps_est_next_y")
                st.markdown("**EPS Estimates**")
                if eps_q:
                    st.markdown(f"- Current Quarter: **${eps_q:.2f}**")
                if eps_y:
                    st.markdown(f"- Current Year: **${eps_y:.2f}**")
                if eps_ny:
                    st.markdown(f"- Next Year: **${eps_ny:.2f}**")

        # ── Earnings surprise chart ──
        if not yf_earnings.empty:
            fig_surp = go.Figure()
            fig_surp.add_trace(go.Bar(
                x=yf_earnings["quarter"].astype(str),
                y=yf_earnings["surprise_pct"] * 100,
                marker_color=["#00d1ff" if v >= 0 else "#ff6b6b" for v in yf_earnings["surprise_pct"]],
                text=[f"{v*100:+.1f}%" for v in yf_earnings["surprise_pct"]],
                textposition="outside",
            ))
            fig_surp.update_layout(
                template="plotly_dark", height=300,
                title="Earnings Surprise History (% Beat/Miss)",
                yaxis_title="Surprise %",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_surp, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Insider transactions ──
        if not yf_insiders.empty:
            st.subheader("Recent Insider Transactions")
            # Show most recent trades
            display_ins = yf_insiders.head(15).copy()
            show_cols = [c for c in ["Start Date", "Insider", "Position", "Transaction", "Shares", "Value", "Text"]
                        if c in display_ins.columns]
            if show_cols:
                st.dataframe(display_ins[show_cols], use_container_width=True, hide_index=True)

    # ── Earnings Calendar ──
    st.markdown("---")
    st.subheader("Recent Earnings Releases")
    cal_days = st.selectbox("Lookback", [3, 7, 14, 30], index=1, key="cal_days",
                            format_func=lambda d: f"Last {d} days")
    cal = fetch_recent_earnings_calendar(days=cal_days)
    if not cal.empty:
        st.caption(f"{len(cal)} earnings releases in the last {cal_days} days.")
        cal_display = cal[["filed", "ticker", "company"]].copy()
        cal_display["filed"] = cal_display["filed"].dt.strftime("%Y-%m-%d")
        cal_display.columns = ["Filed", "Ticker", "Company"]
        st.dataframe(cal_display, use_container_width=True, hide_index=True, height=350)
    else:
        st.info(f"No earnings releases found in the last {cal_days} days.")


    # Energy Sector moved to its own page: 24_Energy_Sector.py


# ── TAB 5: Macro & Rates ──
with tab_macro, error_boundary("Macro & Rates"):
    st.subheader("Macro & Rates Dashboard")
    st.caption("Key economic indicators from FRED (Federal Reserve Economic Data). Requires a free FRED API key set as FRED_API_KEY environment variable.")

    if st.button("Load Macro Data", type="primary", key="load_macro"):
        st.session_state["macro_loaded"] = True

    if st.session_state.get("macro_loaded"):
        macro = fetch_fred_macro_dashboard()
    else:
        macro = {}

    if macro:
        # ── Latest values row ──
        latest_vals = {}
        for sid, df_m in macro.items():
            if not df_m.empty:
                latest_vals[sid] = df_m.iloc[-1]["value"]

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Fed Funds Rate", f"{latest_vals.get('DFF', 0):.2f}%" if "DFF" in latest_vals else "N/A")
        m2.metric("10Y Treasury", f"{latest_vals.get('DGS10', 0):.2f}%" if "DGS10" in latest_vals else "N/A")
        m3.metric("2Y Treasury", f"{latest_vals.get('DGS2', 0):.2f}%" if "DGS2" in latest_vals else "N/A")
        spread = latest_vals.get("T10Y2Y")
        m4.metric("Yield Curve", f"{spread:.2f}%" if spread is not None else "N/A",
                  delta="Inverted" if spread is not None and spread < 0 else None,
                  delta_color="inverse" if spread is not None and spread < 0 else "normal")
        m5.metric("Unemployment", f"{latest_vals.get('UNRATE', 0):.1f}%" if "UNRATE" in latest_vals else "N/A")

        st.markdown("---")

        # ── Yield curve chart ──
        if "T10Y2Y" in macro:
            fig_yc = go.Figure()
            yc = macro["T10Y2Y"]
            fig_yc.add_trace(go.Scatter(
                x=yc["date"], y=yc["value"],
                mode="lines", name="10Y-2Y Spread",
                line=dict(color="#00d1ff", width=2),
                fill="tozeroy", fillcolor="rgba(0,209,255,0.1)",
            ))
            fig_yc.add_hline(y=0, line_dash="dash", line_color="#ff6b6b", annotation_text="Inversion")
            fig_yc.update_layout(
                template="plotly_dark", height=320,
                title="Yield Curve (10Y - 2Y Treasury Spread)",
                yaxis_title="Spread (%)",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_yc, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Oil & Gas prices ──
        oil_col, gas_col = st.columns(2)
        with oil_col:
            if "DCOILWTICO" in macro:
                wti = macro["DCOILWTICO"]
                fig_oil = go.Figure()
                fig_oil.add_trace(go.Scatter(x=wti["date"], y=wti["value"],
                                             mode="lines", name="WTI Crude",
                                             line=dict(color="#ffaa00", width=2)))
                fig_oil.update_layout(
                    template="plotly_dark", height=300,
                    title="WTI Crude Oil Price ($/bbl)",
                    yaxis_title="$/barrel",
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig_oil, use_container_width=True, config=PLOTLY_NOBAR)

        with gas_col:
            if "DHHNGSP" in macro:
                gas = macro["DHHNGSP"]
                fig_gas = go.Figure()
                fig_gas.add_trace(go.Scatter(x=gas["date"], y=gas["value"],
                                             mode="lines", name="Henry Hub",
                                             line=dict(color="#00d1ff", width=2)))
                fig_gas.update_layout(
                    template="plotly_dark", height=300,
                    title="Henry Hub Natural Gas Price ($/MMBtu)",
                    yaxis_title="$/MMBtu",
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig_gas, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Fed Funds Rate ──
        if "DFF" in macro:
            fig_ff = go.Figure()
            ff = macro["DFF"]
            fig_ff.add_trace(go.Scatter(x=ff["date"], y=ff["value"],
                                        mode="lines", name="Fed Funds",
                                        line=dict(color="#ff6b6b", width=2)))
            fig_ff.update_layout(
                template="plotly_dark", height=300,
                title="Federal Funds Effective Rate (%)",
                yaxis_title="%",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_ff, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Custom FRED series ──
        with st.expander("Explore FRED Series"):
            fred_id = st.text_input("FRED Series ID", value="CPIAUCSL", key="fred_custom",
                                    placeholder="e.g. CPIAUCSL, GDP, UNRATE")
            if st.button("Fetch", key="fetch_fred"):
                custom = fetch_fred_series(fred_id, periods=252)
                if not custom.empty:
                    fig_c = go.Figure()
                    fig_c.add_trace(go.Scatter(x=custom["date"], y=custom["value"], mode="lines",
                                               line=dict(color="#00d1ff", width=2)))
                    fig_c.update_layout(
                        template="plotly_dark", height=300,
                        title=FRED_SERIES.get(fred_id, fred_id),
                        margin=dict(l=0, r=0, t=40, b=0),
                    )
                    st.plotly_chart(fig_c, use_container_width=True, config=PLOTLY_NOBAR)
                else:
                    st.warning("No data returned. Check the series ID or ensure FRED_API_KEY is set.")
    elif st.session_state.get("macro_loaded"):
        st.info(
            "**FRED API key not configured.** To enable macro data:\n\n"
            "1. Get a free key at [fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html)\n"
            "2. Set it as environment variable: `FRED_API_KEY=your_key_here`\n\n"
            "This unlocks: Fed Funds Rate, Treasury yields, yield curve, oil/gas prices, CPI, unemployment, and GDP."
        )


# ── TAB 6: 8-K Material Events ──
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

            # Item code descriptions
            item_names = {
                "1.01": "Entry into Agreement", "1.02": "Termination of Agreement",
                "2.01": "Acquisition/Disposition", "2.02": "Results of Operations",
                "2.03": "Obligation Trigger", "2.05": "Costs for Exit",
                "3.01": "Delisting", "3.02": "Unregistered Sales",
                "4.01": "Auditor Change", "4.02": "Non-Reliance on Financials",
                "5.01": "Change of Control", "5.02": "Officer Departure/Appointment",
                "5.03": "Amendments to Articles", "7.01": "Regulation FD Disclosure",
                "8.01": "Other Events", "9.01": "Financial Statements and Exhibits",
            }

            for evt in events:
                filed = evt.get("filed", "")
                items_raw = evt.get("items", "")
                items_tags = [i.strip() for i in items_raw.split(",") if i.strip()] if items_raw else []
                items_desc = ", ".join(item_names.get(i, i) for i in items_tags if i != "9.01")
                items_badge = f'<span style="color:#888;font-size:0.75rem;">{items_desc}</span>' if items_desc else ""
                st.markdown(
                    f'<div style="padding:6px 10px;border-left:2px solid #00d1ff;margin-bottom:4px;'
                    f'background:rgba(255,255,255,0.02);border-radius:0 4px 4px 0;">'
                    f'<span style="color:#888;font-size:0.78rem;">{filed}</span> &nbsp;'
                    f'<span style="color:#00d1ff;font-weight:600;">8-K</span> &nbsp;'
                    f'{items_badge}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info(f"No 8-K filings found for '{search_ticker}' in the last {search_days} days.")
