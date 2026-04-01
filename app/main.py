import asyncio
import secrets
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import get_engine
from app.schemas.common import APIError, api_error_handler
from app.services.week_advancement_service import run_auto_week_advancement_loop


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        auto_week_stop_event = None
        auto_week_task = None
        should_start_auto_week = bool(settings.DATABASE_URL.strip()) and not getattr(
            app.state,
            "disable_auto_week_scheduler",
            False,
        )
        if should_start_auto_week:
            auto_week_stop_event = asyncio.Event()
            auto_week_task = asyncio.create_task(
                run_auto_week_advancement_loop(stop_event=auto_week_stop_event)
            )
            app.state.auto_week_stop_event = auto_week_stop_event
            app.state.auto_week_task = auto_week_task

        try:
            yield
        finally:
            if auto_week_stop_event is not None:
                auto_week_stop_event.set()
            if auto_week_task is not None:
                with suppress(asyncio.CancelledError):
                    await auto_week_task

    app = FastAPI(title="CIS ERP", version="0.1.0", lifespan=lifespan)

    # ── Exception handlers ────────────────────────────────────────
    app.add_exception_handler(APIError, api_error_handler)

    # ── Health check (no auth) ────────────────────────────────────
    @app.get("/health")
    async def health():
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    # ── Dev login (DEBUG only) ──────────────────────────────────
    if settings.DEBUG:

        @app.get("/dev/login")
        async def dev_login(
            request: Request,
            role: str = Query(default="WORKER"),
        ):
            """Fake login for local development — sets session directly."""
            valid_roles = {"WORKER", "SUPERVISOR", "ADMIN"}
            role = role.upper()
            if role not in valid_roles:
                role = "WORKER"

            request.session.clear()
            request.session["user_id"] = 1
            request.session["employee_no"] = f"DEV-{role}"
            request.session["display_name"] = f"Dev {role.title()}"
            request.session["roles"] = [role]
            request.session["team"] = "DEV"

            csrf_token = secrets.token_hex(32)
            request.session["csrf_token"] = csrf_token

            response = RedirectResponse("/dashboard", status_code=302)
            response.set_cookie(
                key="csrftoken",
                value=csrf_token,
                path="/",
                samesite="lax",
                httponly=False,
            )
            return response

    # ── Routers ───────────────────────────────────────────────────
    from app.api.auth import router as auth_router
    from app.api.users import router as users_router
    from app.api.reference import router as reference_router
    from app.api.config import router as config_router
    from app.api.ot import router as ot_router
    from app.api.shops import router as shops_router
    from app.api.shop_access import router as shop_access_router
    from app.api.tasks import router as tasks_router
    from app.views.dashboard import router as dashboard_router
    from app.views.ot import router as ot_views_router
    from app.views.tasks import router as task_views_router
    from app.views.more import router as more_views_router
    from app.api.stats import router as stats_router
    from app.api.rfo import router as rfo_router
    from app.views.stats import router as stats_views_router
    from app.views.rfo import router as rfo_views_router
    from app.views.admin import router as admin_views_router

    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(users_router)
    app.include_router(reference_router)
    app.include_router(config_router)
    app.include_router(ot_router)
    app.include_router(shops_router)
    app.include_router(shop_access_router)
    app.include_router(tasks_router)
    app.include_router(stats_router)
    app.include_router(rfo_router)
    app.include_router(ot_views_router)
    app.include_router(task_views_router)
    app.include_router(more_views_router)
    app.include_router(stats_views_router)
    app.include_router(rfo_views_router)
    app.include_router(admin_views_router)

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
