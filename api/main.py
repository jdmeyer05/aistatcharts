"""FastAPI backend for AI Statcharts.

Exposes the same Python logic that Streamlit pages use, as REST endpoints.
Run alongside Streamlit: uvicorn api.main:app --port 8000

All src/ modules work in both contexts — no dual-mode hacks needed.
"""

import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch @st.cache_data to be a no-op outside Streamlit runtime.
# The decorated functions just run uncached — simpler and more reliable
# than trying to use the real cache without a Streamlit session.
try:
    import streamlit as st

    def _noop_cache_data(*args, **kwargs):
        """Replace @st.cache_data with a passthrough — no caching in FastAPI."""
        if args and callable(args[0]):
            return args[0]  # @st.cache_data without parens
        def decorator(func):
            return func     # @st.cache_data(ttl=...) with parens
        return decorator

    st.cache_data = _noop_cache_data
except Exception:
    pass

# Load secrets from .streamlit/secrets.toml into env vars (local dev)
try:
    import toml
    _secrets_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".streamlit", "secrets.toml")
    if os.path.exists(_secrets_path):
        for k, v in toml.load(_secrets_path).items():
            if isinstance(v, str):
                os.environ.setdefault(k, v)
except Exception:
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize Supabase client."""
    from src.db import get_client
    get_client()  # warm the connection
    yield


app = FastAPI(
    title="AI Statcharts API",
    description="Quantitative trading platform API — market data, signals, options analytics, AI analysis.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# CORS — allow the Next.js frontend.
#
# CORSMiddleware's `allow_origins` list is an exact-match check; wildcards
# like `https://*.vercel.app` in that list DO NOT work. Vercel preview URLs
# need `allow_origin_regex`. Origins are env-configurable so new Vercel
# previews or custom domains don't need a code change.
#
# CORS_ALLOWED_ORIGINS — comma-separated list of exact origins
# CORS_ALLOWED_ORIGIN_REGEX — single regex for wildcard-matching origins
_default_origins = [
    "http://localhost:3000",
    "http://localhost:3001",  # Next.js auto-bumps to 3001 when 3000 is taken
    "http://localhost:3002",
    "http://localhost:8501",
    "https://aistatcharts.com",
    "https://www.aistatcharts.com",
]
_env_origins = [o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
_allow_origins = _env_origins or _default_origins

# Default regex covers Vercel preview deployments for this project. Override
# via env if the Vercel project slug changes. `or` (not the 2-arg form of
# `get`) so a missing *or* empty-string env var both fall back to the default
# — empty regex would silently disable Vercel preview matching.
_default_origin_regex = r"^https://.*\.vercel\.app$"
_allow_origin_regex = os.environ.get("CORS_ALLOWED_ORIGIN_REGEX") or _default_origin_regex

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_origin_regex=_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register route modules
from api.routes import market, signals, positions, options, scanner, energy, edgar, tracking, trump, meta_analysis, scenario, quant_lab, fed_macro, sectors, alerts, ai

app.include_router(market.router, prefix="/api/market", tags=["Market Data"])
app.include_router(signals.router, prefix="/api/signals", tags=["Signals"])
app.include_router(positions.router, prefix="/api/positions", tags=["Positions"])
app.include_router(options.router, prefix="/api/options", tags=["Options"])
app.include_router(scanner.router, prefix="/api/scan", tags=["Scanners"])
app.include_router(energy.router, prefix="/api/energy", tags=["Energy"])
app.include_router(edgar.router, prefix="/api/edgar", tags=["EDGAR"])
app.include_router(tracking.router, prefix="/api/tracking", tags=["Tracking"])
app.include_router(trump.router, prefix="/api/trump", tags=["Trump Decoder"])
app.include_router(meta_analysis.router, prefix="/api/meta", tags=["Meta Analysis"])
app.include_router(scenario.router, prefix="/api/scenario", tags=["Scenario Analysis"])
app.include_router(quant_lab.router, prefix="/api/quant-lab", tags=["Quant Lab"])
app.include_router(fed_macro.router, prefix="/api/fed-macro", tags=["Fed Macro"])
app.include_router(sectors.router, prefix="/api/sectors", tags=["Sector Analysis"])
app.include_router(alerts.router, prefix="/api", tags=["Smart Money Alerts"])
app.include_router(ai.router, prefix="/api/ai", tags=["AI Interpretation"])


@app.get("/api/health")
async def health():
    from src.db import get_client
    db = get_client()
    return {
        "status": "ok",
        "database": "connected" if db else "unavailable",
    }
