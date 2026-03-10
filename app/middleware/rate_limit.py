"""Rate limiting — 120 req/min per user (§8.1.4).

Key policy: authenticated → user:{user_id}, unauthenticated → ip:{remote_addr}.
Uses slowapi Limiter for per-route overrides and an in-memory sliding-window
middleware for global enforcement.
"""
import time
from collections import defaultdict

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

MAX_REQUESTS = 120
WINDOW_SECONDS = 60


def _key_func(request: Request) -> str:
    """user:{id} if authenticated, ip:{addr} otherwise."""
    session = request.scope.get("session")
    if isinstance(session, dict):
        user_id = session.get("user_id")
        if user_id:
            return f"user:{user_id}"
    return f"ip:{get_remote_address(request)}"


# slowapi Limiter — available for per-route @limiter.limit() overrides
limiter = Limiter(key_func=_key_func)

# Module-level bucket store (resettable for tests)
_buckets: dict[str, list[float]] = defaultdict(list)


def reset_rate_limits():
    """Clear all rate-limit buckets (for testing)."""
    _buckets.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global sliding-window rate limiter. 429 RATE_LIMIT on exceed."""

    async def dispatch(self, request: Request, call_next):
        key = _key_func(request)
        now = time.time()
        cutoff = now - WINDOW_SECONDS

        bucket = [t for t in _buckets.get(key, []) if t > cutoff]

        if len(bucket) >= MAX_REQUESTS:
            _buckets[key] = bucket
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded", "code": "RATE_LIMIT"},
            )

        bucket.append(now)
        _buckets[key] = bucket
        return await call_next(request)
