from fastapi import FastAPI
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import get_engine
from app.schemas.common import APIError, api_error_handler


def create_app() -> FastAPI:
    app = FastAPI(title="CIS ERP", version="0.1.0")

    # ── Exception handlers ────────────────────────────────────────
    app.add_exception_handler(APIError, api_error_handler)

    # ── Health check (no auth) ────────────────────────────────────
    @app.get("/health")
    async def health():
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    # ── Routers ───────────────────────────────────────────────────
    from app.api.auth import router as auth_router
    from app.api.users import router as users_router
    from app.api.reference import router as reference_router
    from app.api.config import router as config_router
    from app.api.ot import router as ot_router
    from app.api.shops import router as shops_router
    from app.api.shop_access import router as shop_access_router
    from app.api.tasks import router as tasks_router
    from app.views.ot import router as ot_views_router

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(reference_router)
    app.include_router(config_router)
    app.include_router(ot_router)
    app.include_router(shops_router)
    app.include_router(shop_access_router)
    app.include_router(tasks_router)
    app.include_router(ot_views_router)

    # ── Middleware (last added = outermost in Starlette) ──────────
    # Execution order: Session → CSRF → RateLimit → handler
    from app.middleware.csrf import CSRFMiddleware
    from app.middleware.rate_limit import RateLimitMiddleware

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="session",
        max_age=settings.SESSION_MAX_AGE,
    )

    return app


app = create_app()
