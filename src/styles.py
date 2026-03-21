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

# Default Plotly config — disables scroll zoom so mobile users can scroll the page
PLOTLY_CONFIG = {
    "scrollZoom": False,
    "displayModeBar": False,
    "doubleClick": "reset",
}


def _set_plotly_defaults():
    """Set Plotly defaults globally so all charts inherit uirevision and dark theme.
    Call once at import time."""
    try:
        import plotly.io as pio
        import plotly.graph_objects as go
        # Set uirevision on the default template so charts don't re-animate on Streamlit reruns
        pio.templates["plotly_dark"].layout.uirevision = "stable"
        pio.templates.default = "plotly_dark"
    except Exception:
        pass


_set_plotly_defaults()


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

    /* (sidebar branding removed — sidebar is fully hidden) */

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
    .stPlotlyChart {{
        border: 1px solid {COLORS['card_border']} !important;
        border-radius: 6px !important;
        overflow: hidden !important;
        padding: 0 !important;
    }}
    [data-testid="stDataFrame"],
    [data-testid="stTable"],
    [data-testid="stImage"],
    .stDataFrame {{
        border: 1px solid {COLORS['card_border']} !important;
        border-radius: 6px !important;
        overflow: hidden !important;
        padding: 6px !important;
    }}
    /* Prevent chart annotations/text from overflowing */
    [data-testid="stPlotlyChart"] .plotly .main-svg {{
        overflow: hidden !important;
    }}
    [data-testid="stPlotlyChart"] .plotly {{
        overflow: hidden !important;
    }}
    /* Disable Plotly drag-to-zoom on touch devices so page scroll works */
    @media (pointer: coarse) {{
        [data-testid="stPlotlyChart"] .plotly .drag,
        [data-testid="stPlotlyChart"] .plotly .scrollbox,
        [data-testid="stPlotlyChart"] .plotly .nsewdrag {{
            pointer-events: none !important;
        }}
    }}

    /* Tabs — full border treatment */
    .stTabs {{
        border: 1px solid {COLORS['card_border']} !important;
        border-radius: 8px !important;
        overflow: hidden;
    }}
    .stTabs [data-baseweb="tab-list"] {{
        background: rgba(14, 17, 23, 0.7) !important;
        border-bottom: 1px solid {COLORS['card_border']} !important;
        border-radius: 8px 8px 0 0 !important;
        padding: 4px 4px 0 4px !important;
    }}
    .stTabs [data-baseweb="tab"] {{
        border-radius: 6px 6px 0 0 !important;
        padding: 8px 16px !important;
        color: {COLORS['text_muted']} !important;
    }}
    .stTabs [data-baseweb="tab"][aria-selected="true"] {{
        color: {COLORS['accent']} !important;
        border-bottom: 2px solid {COLORS['accent']} !important;
    }}
    .stTabs [data-baseweb="tab-panel"] {{
        padding: 14px 12px !important;
    }}

    /* Info, warning, success, error boxes */
    [data-testid="stAlert"] {{
        border: 1px solid {COLORS['card_border']} !important;
        border-radius: 6px !important;
    }}

    /* Style Streamlit's default exception/traceback display */
    .stException {{
        background: rgba(255, 68, 68, 0.08) !important;
        border: 1px solid {COLORS['danger']} !important;
        border-radius: 8px !important;
        padding: 16px !important;
    }}
    .stException pre {{
        font-size: 0.75rem !important;
        color: {COLORS['text_muted']} !important;
        max-height: 200px !important;
        overflow-y: auto !important;
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

    /* Hide sidebar completely — all controls moved to header */
    section[data-testid="stSidebar"],
    [data-testid="stSidebarNav"],
    [data-testid="stSidebarCollapseButton"],
    [data-testid="collapsedControl"] {{
        display: none !important;
        width: 0 !important;
        min-width: 0 !important;
        opacity: 0 !important;
        visibility: hidden !important;
        pointer-events: none !important;
    }}

    /* Hide Streamlit's built-in loading spinners — we use fun_loader instead */
    [data-testid="stStatusWidget"],
    .stSpinner,
    [data-testid="stSpinner"],
    [data-testid="stAppViewBlockContainer"] > div > div > [data-testid="stSpinner"] {{
        display: none !important;
    }}

    /* Remove Streamlit default top padding — nuclear approach */
    header[data-testid="stHeader"] {{
        display: none !important;
    }}
    .stApp > header {{
        display: none !important;
    }}
    .stMainBlockContainer, .block-container {{
        padding-top: 0 !important;
        padding-bottom: 40px !important;
        margin-top: 0 !important;
    }}
    [data-testid="stAppViewContainer"] {{
        padding-top: 0 !important;
        margin-top: 0 !important;
    }}
    [data-testid="stAppViewContainer"] > div {{
        padding-top: 0 !important;
        margin-top: 0 !important;
    }}
    .appview-container {{
        padding-top: 0 !important;
        margin-top: 0 !important;
    }}
    .main .block-container {{
        padding-top: 0 !important;
        margin-top: 0 !important;
    }}
    .stApp [data-testid="stAppViewContainer"] > section > div > div > div {{
        padding-top: 0 !important;
    }}
    /* Collapse zero-height component iframes (footer, mobile guard) */
    iframe[height="0"], div:has(> iframe[height="0"]) {{
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        line-height: 0 !important;
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

    /* Cards need opaque backgrounds so they float above the texture */
    div[data-testid="stVerticalBlockBorderWrapper"] {{
        background-color: rgba(22, 27, 34, 0.92) !important;
        backdrop-filter: blur(4px);
    }}

    [data-testid="stMetric"] {{
        background: rgba(22, 27, 34, 0.92) !important;
        backdrop-filter: blur(4px);
    }}

    /* (tab styles defined above) */

    /* Ticker tape needs solid bg */
    .ticker-tape-container {{
        background: rgba(0,0,0,0.85) !important;
        backdrop-filter: blur(8px);
    }}

    /* ── Site header bar ── */
    .site-header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 6px 0;
        flex-wrap: wrap;
        gap: 6px;
    }}
    .site-header-brand {{
        display: flex;
        align-items: center;
        gap: 8px;
    }}
    .site-header-badges {{
        display: flex;
        align-items: center;
        gap: 6px;
        flex-wrap: wrap;
    }}
    .header-badge {{
        font-size: 10px;
        font-weight: 600;
        border: 1px solid;
        padding: 2px 8px;
        border-radius: 10px;
        white-space: nowrap;
    }}

    /* Top nav — page links & dropdown buttons */
    .stPageLink > a {{
        padding: 2px 6px !important;
        font-size: 0.85rem !important;
        border-radius: 4px !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
    }}
    .stPageLink > a:hover {{
        background: rgba(0, 209, 255, 0.1) !important;
    }}
    .stPopover > button {{
        font-size: clamp(0.65rem, 1.1vw, 0.9rem) !important;
        padding: clamp(2px, 0.4vw, 6px) clamp(6px, 1vw, 14px) !important;
        border: 1px solid {COLORS['card_border']} !important;
        border-radius: 6px !important;
        background: {COLORS['card_bg']} !important;
        color: {COLORS['text_primary']} !important;
        white-space: nowrap !important;
    }}
    .stPopover > button:hover {{
        border-color: {COLORS['accent']} !important;
    }}
    /* Nav row buttons (Summary) — match popover style */
    .stMainBlockContainer .stButton > button[kind="secondary"] {{
        font-size: clamp(0.65rem, 1.1vw, 0.9rem) !important;
        padding: clamp(2px, 0.4vw, 6px) clamp(6px, 1vw, 14px) !important;
        border: 1px solid {COLORS['card_border']} !important;
        border-radius: 6px !important;
        background: {COLORS['card_bg']} !important;
        color: {COLORS['text_primary']} !important;
        white-space: nowrap !important;
    }}
    .stMainBlockContainer .stButton > button[kind="secondary"]:hover {{
        border-color: {COLORS['accent']} !important;
    }}
    /* (duplicate nav button styles removed — clamp-based styles above handle all sizes) */

    /* (sidebar styles removed — sidebar is fully hidden) */

    /* ═══════════════════════════════════════
       RESPONSIVE BREAKPOINTS
    ═══════════════════════════════════════ */

    /* ── Responsive: Tablet (< 1200px) ── */
    @media (max-width: 1200px) {{
        .site-header-brand span {{
            font-size: 16px !important;
        }}
        .stPopover > button {{
            font-size: 0.75rem !important;
            padding: 3px 8px !important;
        }}
        .stPageLink > a {{
            font-size: 0.75rem !important;
        }}
        [data-testid="stMetric"] {{
            padding: 6px 8px !important;
        }}
        [data-testid="stMetric"] [data-testid="stMetricValue"] {{
            font-size: 1.1rem !important;
        }}
        .stHorizontalBlock {{
            gap: 0.5rem !important;
        }}
    }}

    /* ── Responsive: Small tablet (< 900px) ── */
    @media (max-width: 900px) {{
        .site-header {{
            flex-direction: column;
            align-items: flex-start;
            gap: 4px;
        }}
        .site-header-brand span {{
            font-size: 14px !important;
        }}
        .stPopover > button {{
            font-size: 0.7rem !important;
            padding: 2px 6px !important;
        }}
        [data-testid="stMetric"] {{
            padding: 4px 6px !important;
        }}
        [data-testid="stMetric"] [data-testid="stMetricValue"] {{
            font-size: 0.95rem !important;
        }}
        [data-testid="stMetric"] [data-testid="stMetricLabel"] {{
            font-size: 0.7rem !important;
        }}
        .stMainBlockContainer, .block-container {{
            padding-left: 1rem !important;
            padding-right: 1rem !important;
        }}
        .stTabs [data-baseweb="tab"] {{
            font-size: 0.8rem !important;
            padding: 6px 10px !important;
        }}
    }}

    /* ── Responsive: Phone (< 640px) ── */
    @media (max-width: 640px) {{
        .site-header-brand span {{
            font-size: 13px !important;
        }}
        .header-badge {{
            font-size: 8px !important;
            padding: 1px 5px !important;
        }}
        .stMainBlockContainer, .block-container {{
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
        }}
        .stHorizontalBlock {{
            flex-wrap: wrap !important;
            gap: 0.35rem !important;
        }}
        .stHorizontalBlock > div {{
            min-width: 30% !important;
            flex: 1 1 30% !important;
        }}
        /* Nav buttons — 44px minimum touch target (Apple HIG) */
        .stPopover > button {{
            font-size: 0.8rem !important;
            padding: 10px 8px !important;
            min-height: 44px !important;
        }}
        .stMainBlockContainer .stButton > button[kind="secondary"] {{
            font-size: 0.8rem !important;
            padding: 10px 8px !important;
            min-height: 44px !important;
        }}
        [data-testid="stMetric"] [data-testid="stMetricValue"] {{
            font-size: 0.85rem !important;
        }}
        .stTabs [data-baseweb="tab-list"] {{
            overflow-x: auto !important;
            flex-wrap: nowrap !important;
        }}
        .stTabs [data-baseweb="tab"] {{
            font-size: 0.7rem !important;
            padding: 4px 8px !important;
            white-space: nowrap !important;
        }}
    }}

    /* ── Skeleton loading animation ── */
    @keyframes shimmer {{
        0% {{ background-position: -400px 0; }}
        100% {{ background-position: 400px 0; }}
    }}
    .skeleton {{
        background: linear-gradient(90deg, {COLORS['card_bg']} 25%, #2a2f3a 50%, {COLORS['card_bg']} 75%);
        background-size: 800px 100%;
        animation: shimmer 1.5s ease-in-out infinite;
        border-radius: 6px;
        border: 1px solid {COLORS['card_border']};
    }}
    .skeleton-card {{
        padding: 16px;
        margin-bottom: 8px;
    }}
    .skeleton-line {{
        height: 12px;
        margin: 8px 0;
        border-radius: 4px;
    }}
    .skeleton-line.title {{
        width: 60%;
        height: 16px;
    }}
    .skeleton-line.price {{
        width: 40%;
        height: 24px;
    }}
    .skeleton-line.chart {{
        width: 100%;
        height: 80px;
    }}

    /* ── Responsive: Small phone (< 400px) ── */
    @media (max-width: 400px) {{
        .stHorizontalBlock > div {{
            min-width: 45% !important;
            flex: 1 1 45% !important;
        }}
        .stPopover > button,
        .stMainBlockContainer .stButton > button[kind="secondary"] {{
            font-size: 0.75rem !important;
            min-height: 48px !important;
        }}
    }}
</style>
""", unsafe_allow_html=True)

    # Disable Plotly scrollZoom globally via JS (prevents mobile scroll hijack)
    st.markdown("""<script>
(function() {
    var obs = new MutationObserver(function() {
        var plots = document.querySelectorAll('.js-plotly-plot');
        plots.forEach(function(p) {
            if (p._fullLayout && p._fullLayout.scrollZoom !== false) {
                Plotly.relayout(p, {scrollZoom: false});
            }
        });
    });
    obs.observe(document.body, {childList: true, subtree: true});
})();
</script>""", unsafe_allow_html=True)
