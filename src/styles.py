"""Centralized color system and global CSS for AI Statcharts."""
import streamlit as st

COLORS = {
    "bg_primary": "#0e1117",
    "bg_secondary": "#1c1f26",
    "accent": "#00d1ff",
    "text_primary": "#e0e0e0",
    "text_muted": "#888888",
    "success": "#00ff96",
    "danger": "#ff4444",
    "warning": "#ffaa00",
    "card_bg": "#161b22",
    "card_border": "#30363d",
}

APP_VERSION = "2.1.0"


def inject_global_css():
    """Inject global CSS classes used across all pages. Call once per page."""
    st.markdown(f"""<style>
    /* ── Card containers ── */
    div[data-testid="stVerticalBlockBorderWrapper"] {{
        background-color: {COLORS['card_bg']} !important;
        border: 1px solid {COLORS['card_border']} !important;
        border-radius: 8px !important;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3) !important;
        padding: 0.5rem !important;
    }}

    /* ── Card header helper ── */
    .card-header {{
        font-size: 1.05rem;
        font-weight: 600;
        color: {COLORS['accent']};
        padding-bottom: 0.4rem;
        margin-bottom: 0.3rem;
        border-bottom: 1px solid {COLORS['card_border']};
    }}
    .card-header .icon {{
        margin-right: 6px;
    }}

    /* ── Sidebar branding ── */
    .sidebar-brand {{
        text-align: center;
        padding: 1rem 0 0.5rem 0;
        border-bottom: 1px solid {COLORS['card_border']};
        margin-bottom: 1rem;
    }}
    .sidebar-brand h2 {{
        color: {COLORS['accent']};
        font-size: 1.4rem;
        font-weight: 700;
        margin: 0;
        letter-spacing: 1px;
    }}
    .sidebar-brand p {{
        color: {COLORS['text_muted']};
        font-size: 0.75rem;
        margin: 4px 0 0 0;
    }}
    .sidebar-footer {{
        padding: 8px 16px;
        font-size: 0.7rem;
        color: {COLORS['text_muted']};
        border-top: 1px solid {COLORS['card_border']};
        margin-top: 2rem;
    }}

    /* ── Status bar ── */
    .status-bar {{
        display: flex;
        gap: 18px;
        padding: 5px 12px;
        background: {COLORS['bg_secondary']};
        border-radius: 4px;
        font-size: 0.72rem;
        color: {COLORS['text_muted']};
        margin-bottom: 10px;
        flex-wrap: wrap;
    }}
    .status-dot {{
        display: inline-block;
        width: 7px;
        height: 7px;
        border-radius: 50%;
        margin-right: 4px;
        vertical-align: middle;
    }}
    .status-fresh {{ background: {COLORS['success']}; }}
    .status-stale {{ background: {COLORS['warning']}; }}
    .status-error {{ background: {COLORS['danger']}; }}

    /* ── Error boundary card ── */
    .error-card {{
        background: rgba(255, 68, 68, 0.08);
        border: 1px solid {COLORS['danger']};
        border-radius: 8px;
        padding: 16px;
        margin: 8px 0;
    }}
    .error-card h4 {{
        color: {COLORS['danger']};
        margin: 0 0 6px 0;
        font-size: 0.95rem;
    }}
    .error-card p {{
        color: {COLORS['text_muted']};
        margin: 0;
        font-size: 0.85rem;
    }}

    /* ── Dataframe text wrap ── */
    [data-testid="stDataFrame"] td div[data-testid="stMarkdownContainer"] p,
    [data-testid="stDataFrame"] td {{
        white-space: normal !important;
        word-wrap: break-word !important;
    }}

    /* ── Metric styling ── */
    [data-testid="stMetric"] {{
        background: {COLORS['card_bg']};
        border: 1px solid {COLORS['card_border']};
        border-radius: 6px;
        padding: 10px 14px;
    }}

    /* ── Borders on charts, tables, and interactive elements ── */
    [data-testid="stPlotlyChart"],
    [data-testid="stDataFrame"],
    [data-testid="stTable"],
    [data-testid="stImage"],
    .stPlotlyChart,
    .stDataFrame {{
        border: 1px solid {COLORS['card_border']} !important;
        border-radius: 6px !important;
        overflow: hidden;
    }}

    /* Tabs container */
    .stTabs [data-baseweb="tab-panel"] {{
        border: 1px solid {COLORS['card_border']};
        border-top: none;
        border-radius: 0 0 6px 6px;
        padding: 12px 8px;
    }}

    /* Info, warning, success, error boxes */
    [data-testid="stAlert"] {{
        border: 1px solid {COLORS['card_border']} !important;
        border-radius: 6px !important;
    }}

    /* Expanders */
    [data-testid="stExpander"] {{
        border: 1px solid {COLORS['card_border']} !important;
        border-radius: 6px !important;
    }}

    /* ═══════════════════════════════════════
       LAYERED BACKGROUND SYSTEM
       5 layers composited for depth:
       1. Base gradient mesh (blue/purple blobs)
       2. Grid lines (faint graph paper)
       3. Noise texture (grain for depth)
       4. Topographic contours (SVG)
       5. Vignette (darkened edges)
    ═══════════════════════════════════════ */

    /* Hide the "app" entry in sidebar nav (the main app.py link) */
    [data-testid="stSidebarNav"] li:first-child {{
        display: none !important;
    }}

    /* Remove dead space at top of sidebar */
    [data-testid="stSidebarNav"] {{
        padding-top: 0 !important;
        margin-top: -1rem !important;
    }}
    section[data-testid="stSidebar"] > div:first-child {{
        padding-top: 0 !important;
    }}

    /* Remove Streamlit default top padding */
    .stMainBlockContainer, .block-container {{
        padding-top: 1rem !important;
    }}
    header[data-testid="stHeader"] {{
        background: transparent !important;
        height: 0 !important;
        min-height: 0 !important;
        padding: 0 !important;
    }}

    /* Target the main app container */
    .stApp {{
        background:
            /* Layer 5: Vignette — darkened edges */
            radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.5) 100%),
            /* Layer 2: Grid lines */
            linear-gradient(rgba(48,54,61,0.12) 1px, transparent 1px),
            linear-gradient(90deg, rgba(48,54,61,0.12) 1px, transparent 1px),
            /* Layer 1: Gradient mesh — 3 radial blobs */
            radial-gradient(ellipse at 15% 20%, rgba(0,60,120,0.18) 0%, transparent 50%),
            radial-gradient(ellipse at 85% 30%, rgba(40,0,80,0.14) 0%, transparent 50%),
            radial-gradient(ellipse at 50% 80%, rgba(0,80,100,0.10) 0%, transparent 50%),
            /* Base color */
            {COLORS['bg_primary']} !important;
        background-size:
            100% 100%,
            40px 40px,
            40px 40px,
            100% 100%,
            100% 100%,
            100% 100%,
            100% 100% !important;
        background-attachment: fixed !important;
    }}

    /* Layer 3: Noise texture via pseudo-element */
    .stApp::before {{
        content: "";
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
        background-repeat: repeat;
        background-size: 256px 256px;
        pointer-events: none;
        z-index: 0;
        opacity: 0.5;
    }}

    /* Layer 4: Topographic contour lines (SVG-based) */
    .stApp::after {{
        content: "";
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        background-image: url("data:image/svg+xml,%3Csvg width='600' height='600' xmlns='http://www.w3.org/2000/svg'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0%25' stop-color='%2300d1ff' stop-opacity='0.03'/%3E%3Cstop offset='100%25' stop-color='%239c27b0' stop-opacity='0.02'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cellipse cx='300' cy='300' rx='280' ry='200' fill='none' stroke='url(%23g)' stroke-width='0.5'/%3E%3Cellipse cx='300' cy='300' rx='230' ry='160' fill='none' stroke='url(%23g)' stroke-width='0.5'/%3E%3Cellipse cx='300' cy='300' rx='180' ry='120' fill='none' stroke='url(%23g)' stroke-width='0.5'/%3E%3Cellipse cx='300' cy='300' rx='130' ry='85' fill='none' stroke='url(%23g)' stroke-width='0.5'/%3E%3Cellipse cx='300' cy='300' rx='80' ry='50' fill='none' stroke='url(%23g)' stroke-width='0.5'/%3E%3Cellipse cx='150' cy='450' rx='120' ry='80' fill='none' stroke='url(%23g)' stroke-width='0.4'/%3E%3Cellipse cx='150' cy='450' rx='80' ry='50' fill='none' stroke='url(%23g)' stroke-width='0.4'/%3E%3Cellipse cx='480' cy='150' rx='100' ry='70' fill='none' stroke='url(%23g)' stroke-width='0.4'/%3E%3Cellipse cx='480' cy='150' rx='60' ry='40' fill='none' stroke='url(%23g)' stroke-width='0.4'/%3E%3C/svg%3E");
        background-repeat: repeat;
        background-size: 600px 600px;
        pointer-events: none;
        z-index: 0;
        opacity: 0.6;
    }}

    /* Ensure content sits above background layers */
    .stApp > * {{
        position: relative;
        z-index: 1;
    }}

    /* Make sidebar slightly different to distinguish */
    section[data-testid="stSidebar"] {{
        background:
            linear-gradient(180deg, rgba(0,40,80,0.15) 0%, rgba(0,0,0,0) 40%),
            {COLORS['bg_primary']} !important;
        overflow-y: auto !important;
    }}
    section[data-testid="stSidebar"] > div {{
        overflow-y: auto !important;
        max-height: 100vh !important;
    }}
    /* Fix sidebar footer blocking scroll */
    .sidebar-footer {{
        position: relative !important;
        margin-top: 2rem;
    }}

    /* Cards need opaque backgrounds so they float above the texture */
    div[data-testid="stVerticalBlockBorderWrapper"] {{
        background-color: rgba(22, 27, 34, 0.92) !important;
        backdrop-filter: blur(4px);
    }}

    [data-testid="stMetric"] {{
        background: rgba(22, 27, 34, 0.92) !important;
        backdrop-filter: blur(4px);
    }}

    /* Tabs need subtle treatment */
    .stTabs [data-baseweb="tab-list"] {{
        background: rgba(14, 17, 23, 0.7);
        border-radius: 6px;
        padding: 2px;
    }}

    /* Ticker tape needs solid bg */
    .ticker-tape-container {{
        background: rgba(0,0,0,0.85) !important;
        backdrop-filter: blur(8px);
    }}
</style>""", unsafe_allow_html=True)
