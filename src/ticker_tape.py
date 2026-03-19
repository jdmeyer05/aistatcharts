import streamlit as st
import yfinance as yf

TICKER_SYMBOLS = ["^GSPC", "QQQ", "DIA", "IWM", "TLT", "GC=F", "CL=F", "BTC-USD", "DX-Y.NYB", "^VIX"]
TICKER_LABELS = {
    "^GSPC": "S&P 500", "QQQ": "Nasdaq (QQQ)", "DIA": "Dow (DIA)", "IWM": "Russell (IWM)",
    "TLT": "20Y Bond (TLT)", "GC=F": "Gold", "CL=F": "WTI Crude", "BTC-USD": "Bitcoin (BTC)",
    "DX-Y.NYB": "Dollar (DXY)", "^VIX": "VIX",
}


@st.cache_data(ttl=300, show_spinner=False)
def _get_ticker_tape():
    items = []
    for sym in TICKER_SYMBOLS:
        try:
            tk = yf.Ticker(sym)
            hist = tk.history(period="2d")
            if len(hist) >= 2:
                price = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2]
                chg = (price / prev - 1) * 100
                label = TICKER_LABELS.get(sym, sym)
                color = "#00ff96" if chg >= 0 else "#ff4444"
                arrow = "▲" if chg >= 0 else "▼"
                if sym in ("^VIX", "DX-Y.NYB"):
                    items.append(f'<span style="color:{color}">{label} {price:.1f} {arrow}{abs(chg):.1f}%</span>')
                elif sym == "^GSPC":
                    items.append(f'<span style="color:{color}">{label} {price:,.0f} {arrow}{abs(chg):.1f}%</span>')
                elif sym == "BTC-USD":
                    items.append(f'<span style="color:{color}">{label} ${price:,.0f} {arrow}{abs(chg):.1f}%</span>')
                elif sym == "CL=F":
                    items.append(f'<span style="color:{color}">{label} ${price:.2f}/bbl {arrow}{abs(chg):.1f}%</span>')
                elif sym == "GC=F":
                    items.append(f'<span style="color:{color}">{label} ${price:,.0f}/oz {arrow}{abs(chg):.1f}%</span>')
                else:
                    items.append(f'<span style="color:{color}">{label} ${price:.2f} {arrow}{abs(chg):.1f}%</span>')
        except Exception:
            pass
    return items


@st.cache_data(ttl=300, show_spinner=False)
def _get_ticker_tape_data() -> dict:
    """Return raw ticker data as a dict for use in the dashboard banner."""
    data = {}
    for sym in TICKER_SYMBOLS:
        try:
            tk = yf.Ticker(sym)
            hist = tk.history(period="2d")
            if len(hist) >= 2:
                price = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2]
                chg = (price / prev - 1) * 100
                data[sym] = {"price": price, "change": chg, "label": TICKER_LABELS.get(sym, sym)}
        except Exception:
            pass
    return data


def render_ticker_tape():
    """Render a scrolling ticker tape at the top of the page."""
    ticker_items = _get_ticker_tape()
    if ticker_items:
        separator = '&nbsp;&nbsp;&nbsp;│&nbsp;&nbsp;&nbsp;'
        tape_html = separator.join(ticker_items)
        # Use current time to compute a negative animation-delay so the scroll
        # picks up roughly where it "would have been" if it had been running continuously.
        import time
        elapsed = time.time() % 30  # position within the 30s animation cycle
        st.markdown(
            f"""<div style="overflow:hidden;white-space:nowrap;background:#111;padding:8px 0;margin-bottom:12px;border-radius:4px;font-size:14px;font-weight:500;">
<div style="display:inline-block;animation:scroll 30s linear infinite;animation-delay:-{elapsed:.1f}s;">
{tape_html}{separator}{tape_html}
</div></div>
<style>
@keyframes scroll {{
    0% {{ transform: translateX(0); }}
    100% {{ transform: translateX(-50%); }}
}}
</style>""",
            unsafe_allow_html=True,
        )
