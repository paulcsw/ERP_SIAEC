"""RFO Detail SSR views (Branch 11 commit 3)."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.services.rfo_service import (
    build_rfo_analytics,
    can_view_rfo,
    get_rfo_selector_options,
    get_work_package,
)
from app.views import templates
from app.views.context import build_task_access_context

router = APIRouter(tags=["rfo-views"])


def _ctx(request, user, **kw):
    page = kw.pop("active_page", "rfo")
    return {
        "request": request,
        "current_user": {
            "user_id": user["user_id"],
            "display_name": user.get("display_name", ""),
            "roles": user.get("roles", []),
            "team": user.get("team"),
            "employee_no": user.get("employee_no", ""),
        },
        "active_tab": "tasks",
        "page": page,
        **kw,
    }


@router.get("/rfo/{work_package_id}")
async def rfo_by_path(
    request: Request,
    work_package_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SSOT §11 route: /rfo/{id}."""
    return await _rfo_page(request, work_package_id, current_user, db)


@router.get("/rfo")
async def rfo_index(
    request: Request,
    id: int | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Backward-compatible /rfo?id= entry point."""
    if id is not None:
        return RedirectResponse(f"/rfo/{id}", status_code=302)
    return await _rfo_page(request, None, current_user, db)


async def _rfo_page(
    request: Request,
    id: int | None,
    current_user: dict,
    db: AsyncSession,
):
    task_access_ctx = await build_task_access_context(db, current_user)
    if not can_view_rfo(current_user):
        return templates.TemplateResponse(
            request,
            "rfo/detail.html",
            _ctx(
                request,
                current_user,
                **task_access_ctx,
                error_title="Access denied",
                error_message="You do not have permission to view RFO detail.",
                rfo_options=[],
            ),
            status_code=403,
        )

    selected_wp = await get_work_package(db, id) if id is not None else None
    if id is not None and not selected_wp:
        return templates.TemplateResponse(
            request,
            "rfo/detail.html",
            _ctx(
                request,
                current_user,
                **task_access_ctx,
                error_title="RFO not found",
                error_message="The requested work package does not exist.",
                rfo_options=[],
            ),
            status_code=404,
        )

    rfo_options = await get_rfo_selector_options(db, selected_wp=selected_wp)
    detail_data = await build_rfo_analytics(db, selected_wp) if selected_wp else {}

    return templates.TemplateResponse(
        request,
        "rfo/detail.html",
        _ctx(
            request,
            current_user,
            **task_access_ctx,
            rfo_options=rfo_options,
            selected=detail_data.get("selected"),
            summary_strip=detail_data.get("summary_strip"),
            kpi=detail_data.get("kpi"),
            task_status_bar=detail_data.get("task_status_bar"),
            blockers_data=detail_data.get("blockers_data"),
            workers_data=detail_data.get("workers_data"),
            burndown_data=detail_data.get("burndown_data"),
        ),
    )
