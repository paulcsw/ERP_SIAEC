"""More tab SSR views (Branch 09 commit 4-c)."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.user_shop_access import UserShopAccess
from app.views import templates

router = APIRouter(tags=["more-views"])


def _ctx(request, user, **kw):
    """Build base template context for More tab."""
    return {
        "request": request,
        "current_user": {
            "user_id": user["user_id"],
            "display_name": user.get("display_name", ""),
            "roles": user.get("roles", []),
            "team": user.get("team"),
            "employee_no": user.get("employee_no", ""),
            "email": user.get("email", ""),
        },
        "active_tab": "more",
        **kw,
    }


# ── GET /more — More tab main list ──────────────────────────────────

@router.get("/more")
async def more_index(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = current_user.get("roles", [])
    if "ADMIN" in roles or "SUPERVISOR" in roles:
        task_access = True
    else:
        row = (await db.execute(
            select(UserShopAccess.id).where(UserShopAccess.user_id == current_user["user_id"]).limit(1)
        )).scalar_one_or_none()
        task_access = row is not None
    return templates.TemplateResponse("more/index.html", _ctx(request, current_user, has_task_access=task_access))


# ── GET /more/rfo-summary — RFO Summary (stub for now) ──────────────

@router.get("/more/rfo-summary")
async def more_rfo_summary(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # RFO metrics/blockers API is in Branch 11. Provide stub data.
    return templates.TemplateResponse("more/rfo_summary.html", _ctx(
        request, current_user,
        rfo=None,
        metrics=None,
        blockers=[],
    ))


# ── GET /more/help — Help page ──────────────────────────────────────

@router.get("/more/help")
async def more_help(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    return templates.TemplateResponse("more/help.html", _ctx(request, current_user))


# ── GET /more/font-size — Font Size settings ────────────────────────

@router.get("/more/font-size")
async def more_font_size(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    return templates.TemplateResponse("more/font_size.html", _ctx(request, current_user))


# ── GET /more/account — My Account (read-only) ──────────────────────

@router.get("/more/account")
async def more_account(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    return templates.TemplateResponse("more/account.html", _ctx(request, current_user))
