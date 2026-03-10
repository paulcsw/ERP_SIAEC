"""CSRF Double Submit Cookie middleware (§8.1.2).

Verifies X-CSRFToken header matches session["csrf_token"] on POST/PATCH/DELETE.
GET/HEAD/OPTIONS are always passed through.
Unauthenticated requests (no csrf_token in session) skip CSRF — the auth
dependency will reject them with 401 instead.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        session = request.scope.get("session") or {}
        session_token = session.get("csrf_token")

        # If user has a session with csrf_token, enforce CSRF
        if session_token:
            header_token = request.headers.get("X-CSRFToken")
            if not header_token or header_token != session_token:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "CSRF token missing or invalid",
                        "code": "CSRF_INVALID",
                    },
                )

        return await call_next(request)
