"""FastAPI backend for AI Statcharts.

Exposes the same Python logic that Streamlit pages use, as REST endpoints.
Run alongside Streamlit: uvicorn api.main:app --port 8000

All src/ modules work in both contexts — no dual-mode hacks needed.
"""

import asyncio
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

logger = logging.getLogger(__name__)

# Nothing in this project ever configured logging, so the root logger sat at its
# WARNING default with no handler. Application warnings still reached Cloud Run
# through `logging.lastResort`, which is why the gap went unnoticed — but every
# `logger.info` in api/ and src/ was discarded, including the six "pre-warmed"
# lines that are the ONLY evidence the startup warm-up did anything. Adding a
# pre-warm you cannot observe is not much better than not adding one.
#
# The handler goes on the root at WARNING and the level is raised only for the
# `api` and `src` trees: INFO on the root would switch on urllib3 and every
# other dependency, and Cloud Run bills by log volume. There are 26 info calls
# across the whole project and exactly one of them sits in a request path, so
# this is startup diagnostics, not per-request chatter.
logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s:%(name)s:%(message)s",
                    stream=sys.stdout)
for _tree in ("api", "src"):
    logging.getLogger(_tree).setLevel(
        os.environ.get("APP_LOG_LEVEL", "INFO").upper())

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

    Groups run on a dedicated pool, PREWARM_CONCURRENCY (default 3) at a time,
    in the priority order set at the bottom of this function — NOT one thread
    each, which is what caused the cold-start connection storm. See the comment
    above the pool for the reasoning."""
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
        """Pre-warm overview + valuation for all 11 SPDR sectors so the
        Compare All tab's 22-call fan-out hits a fully-warm Supabase cache
        regardless of which sector was viewed first. Runs in parallel
        threads to keep total warmup time ≈ slowest endpoint, not sum."""
        try:
            from api.routes.sectors import (
                _compute_sector_overview,
                _compute_sector_valuation,
                SECTOR_CONFIGS,
            )
            from concurrent.futures import ThreadPoolExecutor
            etfs = list(SECTOR_CONFIGS.keys())

            def _warm_one(etf: str) -> tuple[str, bool]:
                try:
                    _compute_sector_overview(etf)
                    _compute_sector_valuation(etf)
                    return etf, True
                except Exception as e:
                    logger.warning(f"Sector pre-warm failed for {etf}: {e}")
                    return etf, False

            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(_warm_one, etfs))
            ok = [e for e, ok in results if ok]
            fail = [e for e, ok in results if not ok]
            logger.info(
                f"Sector caches pre-warmed: {len(ok)}/{len(etfs)} sectors"
                + (f" (failed: {', '.join(fail)})" if fail else "")
            )
        except Exception as e:
            logger.warning(f"Sector pre-warm failed: {e}")

    def _warm_causality() -> None:
        try:
            from api.routes.causality import prewarm_causality
            prewarm_causality()
        except Exception as e:
            logger.warning(f"Causality pre-warm failed: {e}")

    def _warm_macro_pressure() -> None:
        """Prefill the macro scorecard. ~4s across 14 FRED/yfinance series on a
        cold instance, and it renders on the home page — without this the first
        visitor after a scale-up wears the whole fetch."""
        try:
            from api.routes.market import _macro_pressure_cached
            _macro_pressure_cached("3Y")
        except Exception as e:
            logger.warning(f"Macro pressure pre-warm failed: {e}")

    def _warm_sp_valuation() -> None:
        try:
            from api.routes.market import _sp_valuation_cached
            _sp_valuation_cached()
        except Exception as e:
            logger.warning(f"S&P valuation pre-warm failed: {e}")

    def _warm_sector_rrg() -> None:
        """Prefill the sector RRG — ~4s of yfinance on a cold instance, and it
        renders on the home page."""
        try:
            from api.routes.sectors import _sector_rrg_cached
            _sector_rrg_cached(4)
        except Exception as e:
            logger.warning(f"Sector RRG pre-warm failed: {e}")

    def _warm_es_brief() -> None:
        """Prefill the ES session briefing — the lead card on the home page.

        The heaviest first-call on the instance: intraday bars, the SPX option
        chain for dealer gamma, and a decade of index history for the base
        rates, plus the rest-of-session table over 1,222 sessions. Measured at ~47s
        on a brand-new revision against 0.2s warm, and
        the home page's server-side prefetch gives up at 20s — so without this
        the first visitor after a deploy loses the SSR seed for the one card
        the page is built around and falls back to a client fetch.
        """
        try:
            from api.routes.market import _es_brief_cached
            _es_brief_cached()
            # This line did not exist, which made the single most expensive warm
            # on the instance the one warm you could not confirm had finished.
            logger.info("ES brief pre-warmed")
        except Exception as e:
            logger.warning(f"ES brief pre-warm failed: {e}")

    def _warm_es_track_record() -> None:
        """Prefill the session-character calibration.

        8.3s cold — it replays the character read over the full session history
        — and it was left out of this list when it shipped, so the first
        visitor after a scale-up wore the whole thing. It is lazy-fetched on the
        card with a 6h staleTime so it never blocked the brief, but that made it
        a rough edge rather than a harmless one.

        It shares the ES brief's underlying bar caches, so it is warmed AFTER
        the brief rather than beside it — run concurrently the two would fetch
        the same history twice on a cold instance.
        """
        try:
            from src.es_track_record import character_track_record
            character_track_record()
            logger.info("ES track record pre-warmed")
        except Exception as e:
            logger.warning(f"ES track record pre-warm failed: {e}")

    def _warm_es() -> None:
        _warm_es_brief()
        _warm_es_track_record()

    def _warm_energy() -> None:
        """Prefill the oil + natgas bundle caches. Each bundle does ~10
        parallel EIA fetches the first time; on a cold instance that's the
        difference between a 0.4s and a 10-15s first paint for /oil and
        /natgas. Both pages share the same _get_bundle_cache layer."""
        try:
            from api.routes.energy import (
                _get_bundle_cache, _set_bundle_cache,
            )
            from concurrent.futures import ThreadPoolExecutor
            from src.eia_helpers import fetch_eia_data
            from api._json_safe import df_records

            def _to_records(df):
                if df is None or df.empty:
                    return []
                return df_records(df[["period", "value", "wow_change"]])

            def _warm_oil_bundle():
                existing = _get_bundle_cache("energy_oil_bundle_v3", ttl_minutes=30)
                if existing and existing.get("inventories"):
                    return  # L2 already fresh + complete — L1 hydrated by the read
                # Keep this list in lockstep with the /oil route in
                # api/routes/energy.py. If they drift, prewarm fills a bundle
                # the route would rebuild on first hit.
                series = [
                    ("PET.WCESTUS1.W", 520), ("PET.WCRFPUS2.W", 260),
                    ("PET.WCRSTUS1.W", 260), ("PET.WPULEUS3.W", 260),
                    ("PET.WCEIMUS2.W", 260), ("PET.WCREXUS2.W", 260),
                    ("PET.RWTC.W", 260),     ("PET.WGTSTUS1.W", 260),
                    ("PET.WDISTUS1.W", 260), ("PET.WRPUPUS2.W", 520),
                    ("PET.WCSSTUS1.W", 520),
                    ("PET.WCESTP11.W", 520), ("PET.WCESTP21.W", 520),
                    ("PET.WCESTP31.W", 520), ("PET.WCESTP41.W", 520),
                    ("PET.WCESTP51.W", 520),
                    ("STEO.PASC_OECD_T3.M", 144), ("STEO.PAPR_WORLD.M", 144),
                    ("STEO.PATC_WORLD.M", 144),   ("STEO.COPR_WORLD.M", 144),
                    ("STEO.T3_STCHANGE_WORLD.M", 144),
                ]
                with ThreadPoolExecutor(max_workers=10) as pool:
                    results = list(pool.map(lambda a: fetch_eia_data(*a), series))
                bundle = {
                    "inventories": _to_records(results[0]),
                    "production":  _to_records(results[1]),
                    "cushing":     _to_records(results[2]),
                    "refinery":    _to_records(results[3]),
                    "imports":     _to_records(results[4]),
                    "exports":     _to_records(results[5]),
                    "wti":         _to_records(results[6]),
                    "gasoline":    _to_records(results[7]),
                    "distillate":  _to_records(results[8]),
                    "supplied":    _to_records(results[9]),
                    "spr":         _to_records(results[10]),
                    "padd1":       _to_records(results[11]),
                    "padd2":       _to_records(results[12]),
                    "padd3":       _to_records(results[13]),
                    "padd4":       _to_records(results[14]),
                    "padd5":       _to_records(results[15]),
                    "oecd_stocks":        _to_records(results[16]),
                    "world_production":   _to_records(results[17]),
                    "world_consumption":  _to_records(results[18]),
                    "world_crude":        _to_records(results[19]),
                    "world_stock_change": _to_records(results[20]),
                }
                if bundle["inventories"]:  # don't pin a partial bundle (see /oil route)
                    _set_bundle_cache("energy_oil_bundle_v3", bundle, ttl_minutes=30)

            def _warm_natgas_bundle():
                if _get_bundle_cache("energy_natgas_bundle", ttl_minutes=30):
                    return
                # Match the series list in /natgas — keep them in lockstep.
                series = [
                    ("NG.NW2_EPG0_SWO_R48_BCF.W", 520),
                    ("NG.NW2_EPG0_SWO_R31_BCF.W", 260),
                    ("NG.NW2_EPG0_SWO_R32_BCF.W", 260),
                    ("NG.NW2_EPG0_SWO_R33_BCF.W", 260),
                    ("NG.NW2_EPG0_SWO_R34_BCF.W", 260),
                    ("NG.NW2_EPG0_SWO_R35_BCF.W", 260),
                    ("NG.RNGWHHD.W", 260),
                    ("NG.N9140US2.M", 60),
                ]
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(lambda a: fetch_eia_data(*a), series))
                bundle = {
                    "storage": _to_records(results[0]),
                    "regions": {
                        "East":           _to_records(results[1]),
                        "Midwest":        _to_records(results[2]),
                        "Mountain":       _to_records(results[3]),
                        "Pacific":        _to_records(results[4]),
                        "South Central":  _to_records(results[5]),
                    },
                    "henry_hub":   _to_records(results[6]),
                    "consumption": _to_records(results[7]),
                }
                _set_bundle_cache("energy_natgas_bundle", bundle, ttl_minutes=30)

            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda fn: fn(), [_warm_oil_bundle, _warm_natgas_bundle]))
            logger.info("Energy (oil + natgas) caches pre-warmed")
        except Exception as e:
            logger.warning(f"Energy pre-warm failed: {e}")

    def _warm_ercot() -> None:
        """Prefill the two ERCOT pages' cold paths.

        /ercot-power reads the `energy_ercot_bundle` bundle (4 parallel ERCOT
        dashboard fetches, same _get_bundle_cache layer as oil/natgas). Its TTL
        is only 5 min — live grid data — so this mainly buys a warm first paint
        for the visitor right after a deploy / revision restart.

        /ercot-capacity has its own module-level TTL cache in src.ercot_capacity;
        calling discover_months() + the latest month's fetch_capacity_file()
        fills it so the first visitor skips the file-server probe + Excel parse.
        """
        try:
            from api.routes.energy import _get_bundle_cache, _set_bundle_cache

            # Power dashboard bundle — mirror the /ercot-bundle route.
            if not _get_bundle_cache("energy_ercot_bundle", ttl_minutes=5):
                from concurrent.futures import ThreadPoolExecutor
                from src.ercot_api import fetch_dashboard
                endpoints = ["fuel-mix", "supply-demand", "loadForecastVsActual", "ancillary-services"]
                with ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(fetch_dashboard, endpoints))
                bundle = {
                    "fuel_mix": results[0],
                    "supply_demand": results[1],
                    "load_forecast": results[2],
                    "ancillary": results[3],
                }
                # Don't pin an empty bundle if ERCOT's dashboard was unreachable;
                # the next request will retry the fan-out.
                if bundle["fuel_mix"]:
                    _set_bundle_cache("energy_ercot_bundle", bundle, ttl_minutes=5)

            # Capacity pipeline — warm the internal cache for the default view
            # (latest month, all projects). The MoM tab's other months stay lazy.
            from src.ercot_capacity import discover_months, fetch_capacity_file
            months = discover_months(lookback=12)
            if months:
                latest = months[0]
                fetch_capacity_file(latest["date_path"], latest["month_label"], planned_only=False)
            logger.info("ERCOT caches pre-warmed")
        except Exception as e:
            logger.warning(f"ERCOT pre-warm failed: {e}")

    # Ordered by what the home page actually needs first. The ES brief is the
    # lead card AND the heaviest single warm, so it starts immediately rather
    # than queueing behind energy/ERCOT; causality and ERCOT render on pages
    # nobody lands on cold, so they go last.
    #
    # Measured on revision 00130, the first startup where per-group timing
    # existed at all: ES 95.8s (brief + track record), macro 56.2s, RRG 42.1s,
    # ERCOT 34.0s, S&P val 12.2s, sectors 9.7s, causality 3.8s. Energy 0.5s /
    # CFTC 0.4s / vol 0.0s hit a warm Supabase L2 from the prior revision and
    # are NOT cold costs — do not re-order on them.
    _groups = (
        ("ES brief + track record", _warm_es),
        ("Macro pressure", _warm_macro_pressure),
        ("Sector RRG", _warm_sector_rrg),
        ("S&P valuation", _warm_sp_valuation),
        ("Vol landscape", _warm_vol_landscape),
        ("CFTC", _warm_cftc),
        ("Sectors", _warm_sectors),
        ("Energy", _warm_energy),
        ("ERCOT", _warm_ercot),
        ("Causality", _warm_causality),
    )

    def _timed(label, fn):
        """Every group already swallows its own exceptions, so this only times."""
        def run() -> None:
            t0 = time.monotonic()
            try:
                fn()
            finally:
                logger.info(f"Pre-warm group '{label}' took {time.monotonic() - t0:.1f}s")
        return run

    # These ten ran on `run_in_executor(None, ...)`, i.e. the DEFAULT executor,
    # which on this 2-vCPU instance holds min(32, cpu+4) = 6 threads. Two costs,
    # both observed:
    #
    #   1. Six groups ran at once and each then opened its OWN pool (energy
    #      fans out to 18 EIA fetches, macro to 10, sectors to 4), so a cold
    #      instance opened 40-60 concurrent sockets. FRED answered with
    #      `SSL: UNEXPECTED_EOF` and Supabase with `[Errno 32] Broken pipe` —
    #      the startup error burst that made this worth fixing.
    #   2. Prewarms occupied all six default-executor slots for minutes, so
    #      request-path work sharing it — `_log_gate_snapshot` in
    #      api/routes/market.py — queued behind a 47s warm.
    #
    # A dedicated, bounded pool fixes both: it caps the fan-out and leaves the
    # default executor free for requests. Three lanes bounds the worst case to
    # three groups' inner pools instead of six — the socket count itself was
    # never counted directly, so treat it as a bound, not a measurement. What
    # WAS measured is the outcome: FRED `UNEXPECTED_EOF` in the first six
    # minutes of instance life went 26 (rev 00129) -> 1 (rev 00130), and the
    # survivor arrived after the warm-up finished, so it was request-path.
    # PREWARM_CONCURRENCY tunes it without a code change.
    # Parsed defensively: this runs inside a fire-and-forget task, so a bad
    # value would raise here and silently skip every warm — the failure mode
    # the whole function exists to prevent.
    try:
        max_par = max(1, int(os.environ.get("PREWARM_CONCURRENCY", "3")))
    except ValueError:
        logger.warning(
            f"PREWARM_CONCURRENCY={os.environ.get('PREWARM_CONCURRENCY')!r} "
            "is not an integer — falling back to 3")
        max_par = 3
    loop = asyncio.get_running_loop()
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max_par, thread_name_prefix="prewarm") as pool:
        # return_exceptions=True is load-bearing, not defensive habit. Without
        # it gather propagates the FIRST exception while the other groups are
        # still running, and the `with` block then calls pool.shutdown(wait=True)
        # on the EVENT LOOP THREAD — freezing the whole API for as long as the
        # slowest remaining warm takes (~96s measured). Every group already
        # catches its own exceptions, so this only covers what escapes one, but
        # the cost of being wrong about that is the server, not a warm.
        results = await asyncio.gather(*(
            loop.run_in_executor(pool, _timed(label, fn)) for label, fn in _groups
        ), return_exceptions=True)
    for (label, _), outcome in zip(_groups, results):
        if isinstance(outcome, BaseException):
            logger.warning(f"Pre-warm group '{label}' raised past its own handler: {outcome!r}")
    logger.info(
        f"Pre-warm complete: {len(_groups)} groups, {max_par} at a time, "
        f"{time.monotonic() - started:.1f}s total"
    )


def _validate_critical_config() -> None:
    """Fail fast on misconfiguration that would silently degrade a subsystem.

    OI_CAPTURE_KEY set-but-empty makes `require_admin_or_scheduler` fall
    through to the admin-JWT path, so Cloud Scheduler calls 403 with no
    obvious cause. Better to refuse to start than to serve in that state.
    """
    val = os.environ.get("OI_CAPTURE_KEY")
    if val is not None and not val.strip():
        raise RuntimeError(
            "OI_CAPTURE_KEY is set but empty — unset it or provide a value"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize Supabase client + pre-warm CFTC caches in the
    background. The app accepts requests immediately; caches fill asynchronously."""
    _validate_critical_config()
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

# Default regex covers this project's Vercel preview deployments only —
# `aistatcharts[.vercel.app]` and `aistatcharts-<hash>-<team>.vercel.app`.
# Matching every `*.vercel.app` would let any Vercel tenant's site (including
# an attacker's) hit this API. Override via env if the Vercel slug changes.
# `or` (not the 2-arg form of `get`) so a missing *or* empty-string env var
# both fall back to the default — empty regex would silently disable matching.
_default_origin_regex = r"^https://aistatcharts(-[a-z0-9-]+)?\.vercel\.app$"
_allow_origin_regex = os.environ.get("CORS_ALLOWED_ORIGIN_REGEX") or _default_origin_regex

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_origin_regex=_allow_origin_regex,
    allow_credentials=True,
    # Explicit method list — routes use GET/POST/PATCH/DELETE; OPTIONS is for
    # the preflight. `["*"]` would also accept e.g. TRACE which nothing serves.
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Rate limiting — protects AI vendor spend. See api/rate_limit.py for the
# key function; routes opt in with `@limiter.limit("20/minute;500/day")`.
# Wrapped in try/except so legacy images without slowapi still boot (the
# limiter module returns a no-op decorator in that case).
from api.rate_limit import limiter
app.state.limiter = limiter
try:
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
except ImportError:
    logger.warning("slowapi not installed — rate limiting disabled (legacy image or dev env)")

# A Supabase/PostgREST failure used to reach the browser as "Failed to fetch"
# with no status and no message, which is indistinguishable from the API being
# down. The cause is middleware ordering, and it is NOT fixable by reordering:
# Starlette builds ServerErrorMiddleware -> [user middleware] -> ExceptionMiddleware
# -> router, so ServerErrorMiddleware is ALWAYS outside CORSMiddleware and its
# 500 can never carry Access-Control-Allow-Origin. The browser then blocks the
# response and reports a network error rather than the real one.
#
# Verified by experiment: no handler -> no ACAO header; a handler registered for
# `Exception` -> still none (Starlette routes that one to ServerErrorMiddleware,
# also outside CORS); a handler registered for a CONCRETE class -> handled by
# ExceptionMiddleware, which is INSIDE CORSMiddleware, so the response flows out
# through it and arrives with headers intact. Hence the concrete class here.
try:
    from postgrest.exceptions import APIError as _PostgrestAPIError
    from fastapi.responses import JSONResponse

    @app.exception_handler(_PostgrestAPIError)
    async def _postgrest_error_handler(request, exc):
        logger.error(f"Supabase error on {request.url.path}: {exc}")
        return JSONResponse(
            status_code=503,
            content={"detail": "A database query failed", "source": "supabase"},
        )
except ImportError:
    logger.warning("postgrest not importable — Supabase errors will surface as opaque 500s")

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
    ("/api/causality/",         "public, max-age=3600, stale-while-revalidate=21600"),
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
from api.routes import market, signals, positions, options, scanner, energy, edgar, tracking, trump, meta_analysis, scenario, quant_lab, fed_macro, sectors, alerts, ai, cftc, wsb, causality, ai_infra

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
app.include_router(wsb.router, prefix="/api/wsb", tags=["WallStreetBets"])
app.include_router(causality.router, prefix="/api/causality", tags=["Causality"])
app.include_router(ai_infra.router, prefix="/api/ai-infra", tags=["AI Infrastructure"])


@app.get("/api/health")
async def health():
    from src.db import get_client
    db = get_client()
    return {
        "status": "ok",
        "database": "connected" if db else "unavailable",
    }
