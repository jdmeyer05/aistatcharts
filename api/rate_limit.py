"""Rate limiting for expensive AI vendor calls.

Cost model: each /api/ai/* call spends $0.001-$0.05 of Anthropic/OpenAI/Grok
budget. Without limits, an unauthenticated scraper or a runaway client loop
can drain the balance in minutes.

Key function pins authenticated traffic to a stable per-session identifier
(hash of the Bearer token) and anonymous traffic to remote IP. Token
fingerprint — not the decoded email — because slowapi's `key_func` runs
before FastAPI's dependency chain, so re-verifying the JWT here would pay
the verify cost twice per request.

Storage is in-memory per Cloud Run container; with max-instances=10 that
means effective limits are up to 10× spec at the tail. Still caps a bad
actor at ~$500/day instead of unbounded. Move to a shared backend (Redis)
if the in-memory skew ever matters.
"""

import hashlib

from fastapi import Request

try:
    from slowapi import Limiter
    _SLOWAPI_AVAILABLE = True
except ImportError:
    # Legacy image builds (e.g. the combined Streamlit+FastAPI container)
    # may not have slowapi installed yet. Fall back to a no-op decorator
    # so `api.main` + every route module still imports cleanly. The rate
    # limit isn't enforced in that environment, which is acceptable for
    # legacy/dev use; the prod `aistatcharts-api` image always has it.
    _SLOWAPI_AVAILABLE = False

    class _NoopLimiter:
        """Pass-through decorator when slowapi isn't installed."""
        def limit(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

    Limiter = None  # type: ignore[assignment,misc]


def _client_ip(request: Request) -> str:
    """Best-effort client IP — respects `X-Forwarded-For` set by the Cloud
    Run load balancer. `request.client.host` alone is the LB address, which
    would bucket every anonymous user together.

    Attackers can spoof XFF, but the worst outcome is choosing/rotating
    their own bucket — no worse than not limiting. The auth'd path uses a
    token hash instead, so prod traffic is unaffected.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return getattr(request.client, "host", None) or "unknown"


def _key_fn(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and auth[7:].strip():
        token = auth[7:].strip()
        return "u:" + hashlib.sha256(token.encode()).hexdigest()[:16]
    return "ip:" + _client_ip(request)


# Shared limiter — register once on `app.state.limiter` and decorate routes
# with `@limiter.limit("20/minute;500/day")`. Route handlers must accept a
# `request: Request` parameter for slowapi to wire the check.
if _SLOWAPI_AVAILABLE:
    limiter = Limiter(key_func=_key_fn, default_limits=[])
else:
    limiter = _NoopLimiter()
