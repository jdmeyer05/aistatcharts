"""FastAPI backend for AI Statcharts.

Exposes the same Python logic that Streamlit pages use, as REST endpoints.
Run alongside Streamlit: uvicorn api.main:app --port 8000

All src/ modules work in both contexts — no dual-mode hacks needed.
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

logger = logging.getLogger(__name__)

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


async def _warm_caches() -> None:
    """Fire the slow cacheable dashboards in background so the first user hits
    warm Supabase caches instead of a cold 30-120s wait. Non-fatal if any fail.
    Each warm task runs in its own thread so one slow fetcher doesn't block the others."""
    def _warm_cftc() -> None:
        try:
            from src.cftc import positioning_dashboard
            from src.cta_model import cta_bias_scan, reconstructed_cta_pnl, historical_analog, all_vol_percentiles
            positioning_dashboard()
            all_vol_percentiles()
            cta_bias_scan()
            reconstructed_cta_pnl()
            historical_analog(5)
            logger.info("CFTC caches pre-warmed")
        except Exception as e:
            logger.warning(f"CFTC pre-warm failed: {e}")

    def _warm_vol_landscape() -> None:
        try:
            from api.routes.options import _compute_vol_landscape
            _compute_vol_landscape()
            logger.info("Vol landscape cache pre-warmed")
        except Exception as e:
            logger.warning(f"Vol landscape pre-warm failed: {e}")

    def _warm_sectors() -> None:
        """Warm the 3 sectors most likely to load first + the Compare tab's
        overview/valuation pair. Other sectors populate on first user hit and
        sit in Supabase for 12h thereafter."""
        try:
            from api.routes.sectors import _compute_sector_overview, _compute_sector_valuation
            hot = ["XLE", "XLF", "XLK"]
            for etf in hot:
                try:
                    _compute_sector_overview(etf)
                    _compute_sector_valuation(etf)
                except Exception as e:
                    logger.warning(f"Sector pre-warm failed for {etf}: {e}")
            logger.info(f"Sector caches pre-warmed for {', '.join(hot)}")
        except Exception as e:
            logger.warning(f"Sector pre-warm failed: {e}")

    loop = asyncio.get_event_loop()
    # Kick off in parallel so total warmup time ≈ slowest task, not the sum.
    await asyncio.gather(
        loop.run_in_executor(None, _warm_cftc),
        loop.run_in_executor(None, _warm_vol_landscape),
        loop.run_in_executor(None, _warm_sectors),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize Supabase client + pre-warm CFTC caches in the
    background. The app accepts requests immediately; caches fill asynchronously."""
    from src.db import get_client
    get_client()  # warm the connection
    # Fire-and-forget background warmup. Don't await — server starts now.
    asyncio.create_task(_warm_caches())
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

# Compress responses ≥ 1 KB. Dashboards are 5-25 KB JSON; compression ratio
# is typically 6-10× for pretty-printed JSON. Material win on mobile networks.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)


# Path-based Cache-Control hints. Tabular data that only updates weekly
# (CFTC, vol-landscape) can safely sit in the browser cache for a few
# minutes — saves a full round-trip when users jump tabs quickly.
_PATH_CACHE_HINTS = (
    ("/api/cftc/",              "public, max-age=300, stale-while-revalidate=3600"),
    ("/api/options/vol-landscape", "public, max-age=300, stale-while-revalidate=3600"),
    ("/api/fed-macro/",         "public, max-age=300, stale-while-revalidate=3600"),
    ("/api/sectors/",           "public, max-age=3600, stale-while-revalidate=21600"),
)


@app.middleware("http")
async def _cache_control_middleware(request, call_next):
    """Stamp Cache-Control on read endpoints so browsers + CDNs can hold
    responses for short windows. Write endpoints keep default (no-cache)."""
    response = await call_next(request)
    if request.method == "GET" and 200 <= response.status_code < 300:
        path = request.url.path
        for prefix, header in _PATH_CACHE_HINTS:
            if path.startswith(prefix):
                response.headers["Cache-Control"] = header
                break
    return response

# Register route modules
from api.routes import market, signals, positions, options, scanner, energy, edgar, tracking, trump, meta_analysis, scenario, quant_lab, fed_macro, sectors, alerts, ai, cftc

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
app.include_router(cftc.router, prefix="/api/cftc", tags=["CFTC Positioning"])


@app.get("/api/health")
async def health():
    from src.db import get_client
    db = get_client()
    return {
        "status": "ok",
        "database": "connected" if db else "unavailable",
    }
