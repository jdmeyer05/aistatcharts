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

# CORS — allow the Next.js frontend (and local dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8501", "https://aistatcharts.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register route modules
from api.routes import market, signals, positions, options

app.include_router(market.router, prefix="/api/market", tags=["Market Data"])
app.include_router(signals.router, prefix="/api/signals", tags=["Signals"])
app.include_router(positions.router, prefix="/api/positions", tags=["Positions"])
app.include_router(options.router, prefix="/api/options", tags=["Options"])


@app.get("/api/health")
async def health():
    from src.db import get_client
    db = get_client()
    return {
        "status": "ok",
        "database": "connected" if db else "unavailable",
    }
