"""OT SSR views (Branch 04 commits 5-6)."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_role
from app.models.ot import OtApproval, OtRequest
from app.models.reference import WorkPackage
from app.models.user import Role, User, user_roles
from app.services.ot_service import (
    MONTHLY_LIMIT_MINUTES,
    _monthly_used_minutes,
    apply_ot_role_scope,
    apply_ot_search_filter,
    get_visible_ot_user_ids,
    normalize_ot_search,
)
from app.views import templates
from app.views.context import build_href, build_task_access_context

router = APIRouter(tags=["ot-views"])


# ?? Helpers ??????????????????????????????????????????????????????????

STATUS_BADGES = {
    "PENDING": ("badge-pending", "PENDING"),
    "ENDORSED": ("badge-endorsed", "ENDORSED"),
    "APPROVED": ("badge-approved", "APPROVED"),
    "REJECTED": ("badge-rejected", "REJECTED"),
    "CANCELLED": ("badge-cancelled", "CANCELLED"),
}
MOBILE_O2_PER_PAGE = 20


async def _team_users_with_hours(
    db: AsyncSession,
    team: str | None,
    month: date,
    scope: str = "team",
) -> list[dict]:
    """Get a worker roster with monthly OT usage."""
    q = (
        select(User)
        .join(user_roles, User.id == user_roles.c.user_id)
        .join(Role, Role.id == user_roles.c.role_id)
        .where(
            User.is_active == True,  # noqa: E712
            Role.name == "WORKER",
        )
        .order_by(User.name.asc())
    )
    if scope == "team":
        if not team:
            users = []
        else:
            users = (
                await db.execute(q.where(User.team == team))
            ).scalars().unique().all()
    else:
        users = (await db.execute(q)).scalars().unique().all()


    result = []
    for u in users:
        used = await _monthly_used_minutes(db, u.id, month)
        pct = round(used / MONTHLY_LIMIT_MINUTES * 100, 1) if MONTHLY_LIMIT_MINUTES else 0
        result.append({
            "id": u.id,
            "name": u.name,
            "employee_no": u.employee_no,
            "team": u.team,
            "used_hours": round(used / 60, 1),
            "limit_hours": round(MONTHLY_LIMIT_MINUTES / 60, 1),
            "used_pct": pct,
            "at_limit": used >= MONTHLY_LIMIT_MINUTES,
            "bar_color": "#b93a3a" if pct >= 100 else "#c8850a" if pct >= 70 else "#2e5a8a",
        })
    return result


async def _can_view_ot_request(
    db: AsyncSession,
    current_user: dict,
    ot: OtRequest,
) -> bool:
    """Return whether the current user can view the OT request detail."""
    roles = current_user.get("roles", [])
    if "ADMIN" in roles:
        return True
    if ot.user_id == current_user["user_id"]:
        return True
    if "SUPERVISOR" in roles:
        ot_user_team = (
            await db.execute(select(User.team).where(User.id == ot.user_id))
        ).scalar_one_or_none()
        return ot_user_team == current_user.get("team")
    return False


def _build_ot_list_state_params(
    *,
    status: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int | None = None,
) -> dict:
    return {
        "status": status or None,
        "search": search or None,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "page": page if page and page > 1 else None,
    }


def _ot_detail_action_flags(current_user: dict, ot: OtRequest) -> dict:
    """Return detail action permissions for an already-visible OT request."""
    roles = current_user.get("roles", [])
    is_admin = "ADMIN" in roles
    is_supervisor_only = "SUPERVISOR" in roles and not is_admin
    return {
        "can_cancel": ot.status == "PENDING" and ot.user_id == current_user["user_id"],
        "can_endorse": (
            ot.status == "PENDING"
            and is_supervisor_only
            and ot.user_id != current_user["user_id"]
        ),
        "can_approve": (
            ot.status == "ENDORSED"
            and is_admin
            and ot.user_id != current_user["user_id"]
        ),
    }


def _ot_detail_back_href(
    *,
    status: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int | None = None,
) -> str:
    return build_href(
        "/ot",
        **_build_ot_list_state_params(
            status=status,
            search=search,
            date_from=date_from,
            date_to=date_to,
            page=page,
        ),
    )


def _ot_mobile_history_href(
    status: str | None = None,
    page: int | None = None,
) -> str:
    return build_href(
        "/ot/segment/o2",
        status=status or None,
        page=page if page and page > 1 else None,
    )


async def _enrich_ot_list(db: AsyncSession, rows: list) -> list[dict]:
    """Enrich OT rows with user names and approval info."""
    user_ids = set()
    wp_ids = set()
    for r in rows:
        user_ids.add(r.user_id)
        if r.submitted_by:
            user_ids.add(r.submitted_by)
        if r.work_package_id:
            wp_ids.add(r.work_package_id)
    users_map = {}
    work_packages_map = {}
    if user_ids:
        us = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        users_map = {u.id: u for u in us}
    if wp_ids:
        wps = (await db.execute(select(WorkPackage).where(WorkPackage.id.in_(wp_ids)))).scalars().all()
        work_packages_map = {wp.id: wp for wp in wps}

    ot_ids = [r.id for r in rows]
    approvals_map: dict[int, list] = {}
    if ot_ids:
        apps = (await db.execute(
            select(OtApproval).where(OtApproval.ot_request_id.in_(ot_ids))
        )).scalars().all()
        for a in apps:
            approvals_map.setdefault(a.ot_request_id, []).append(a)
            if a.approver_id not in users_map:
                approver = (await db.execute(select(User).where(User.id == a.approver_id))).scalar_one_or_none()
                if approver:
                    users_map[approver.id] = approver

    items = []
    for r in rows:
        user = users_map.get(r.user_id)
        sub_user = users_map.get(r.submitted_by) if r.submitted_by else None
        wp = work_packages_map.get(r.work_package_id)
        apps = approvals_map.get(r.id, [])
        endorse = next((a for a in apps if a.stage == "ENDORSE"), None)
        approve = next((a for a in apps if a.stage == "APPROVE"), None)
        endorse_user = users_map.get(endorse.approver_id) if endorse else None
        approve_user = users_map.get(approve.approver_id) if approve else None

        badge_cls, badge_text = STATUS_BADGES.get(r.status, ("badge-pending", r.status))
        items.append({
            "id": r.id,
            "user_id": r.user_id,
            "user_name": user.name if user else "?",
            "user_employee_no": user.employee_no if user else "",
            "user_team": user.team if user else "",
            "submitted_by": r.submitted_by,
            "submitted_by_name": sub_user.name if sub_user else None,
            "date": r.date,
            "start_time": r.start_time,
            "end_time": r.end_time,
            "requested_minutes": r.requested_minutes,
            "hours": round(r.requested_minutes / 60, 1),
            "reason_code": r.reason_code,
            "reason_text": r.reason_text,
            "work_package_id": r.work_package_id,
            "work_package_rfo_no": wp.rfo_no if wp else None,
            "status": r.status,
            "badge_cls": badge_cls,
            "badge_text": badge_text,
            "created_at": r.created_at,
            "endorse": {
                "approver_name": endorse_user.name if endorse_user else None,
                "action": endorse.action if endorse else None,
                "acted_at": endorse.acted_at if endorse else None,
                "comment": endorse.comment if endorse else None,
            } if endorse else None,
            "approve": {
                "approver_name": approve_user.name if approve_user else None,
                "action": approve.action if approve else None,
                "acted_at": approve.acted_at if approve else None,
                "comment": approve.comment if approve else None,
            } if approve else None,
        })
    return items


async def _add_monthly_usage_to_ot_items(
    db: AsyncSession,
    items: list[dict],
) -> list[dict]:
    for item in items:
        used = await _monthly_used_minutes(db, item["user_id"], item["date"])
        item["monthly_used_hours"] = round(used / 60, 1)
        item["monthly_limit_hours"] = round(MONTHLY_LIMIT_MINUTES / 60, 1)
        item["monthly_pct"] = round(used / MONTHLY_LIMIT_MINUTES * 100, 1)
        item["monthly_bar_color"] = (
            "#b93a3a" if item["monthly_pct"] >= 100
            else "#c8850a" if item["monthly_pct"] >= 70
            else "#2e5a8a"
        )
    return items


async def _load_admin_ot_approve_items(
    db: AsyncSession,
    current_user: dict,
) -> list[dict]:
    rows = (await db.execute(
        select(OtRequest)
        .where(
            OtRequest.status == "ENDORSED",
            OtRequest.user_id != current_user["user_id"],
        )
        .order_by(OtRequest.created_at.desc(), OtRequest.id.desc())
    )).scalars().all()
    items = await _enrich_ot_list(db, rows)
    return await _add_monthly_usage_to_ot_items(db, items)


async def _load_supervisor_ot_endorse_items(
    db: AsyncSession,
    current_user: dict,
) -> list[dict]:
    team = current_user.get("team")
    if not team:
        return []

    rows = (await db.execute(
        select(OtRequest)
        .join(User, OtRequest.user_id == User.id)
        .where(
            OtRequest.status == "PENDING",
            OtRequest.user_id != current_user["user_id"],
            User.team == team,
        )
        .order_by(OtRequest.created_at.desc(), OtRequest.id.desc())
    )).scalars().all()
    items = await _enrich_ot_list(db, rows)
    return await _add_monthly_usage_to_ot_items(db, items)


def _ctx(request, user, **kw):
    """Build base template context."""
    # Map active_page ??page for sidebar highlighting
    page = kw.get("active_page", "")
    return {
        "request": request,
        "current_user": {
            "user_id": user["user_id"],
            "display_name": user.get("display_name", ""),
            "roles": user.get("roles", []),
            "team": user.get("team"),
            "employee_no": user.get("employee_no", ""),
        },
        "active_tab": "ot",
        "page": page,
        **kw,
    }


async def _ctx_with_task_access(request: Request, user: dict, db: AsyncSession, **kw):
    return _ctx(
        request,
        user,
        **(await build_task_access_context(db, user)),
        **kw,
    )


def _is_htmx_request(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


# ?? Desktop: /ot/new ??Submit form ??????????????????????????????????

@router.get("/ot/new")
async def ot_submit_page(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    my_used = await _monthly_used_minutes(db, current_user["user_id"], today)
    roles = current_user.get("roles", [])
    is_admin = "ADMIN" in roles
    is_sup_or_admin = "SUPERVISOR" in roles or is_admin

    team_users = []
    roster_scope_label = current_user.get("team") or "All Teams"
    if is_sup_or_admin:
        roster_scope = "all" if is_admin else "team"
        team_users = await _team_users_with_hours(
            db,
            current_user.get("team"),
            today,
            scope=roster_scope,
        )
        if is_admin:
            roster_scope_label = "All Teams"

    # Work packages for RFO dropdown
    wps = (await db.execute(select(WorkPackage))).scalars().all()
    rfo_options = [{"id": wp.id, "rfo_no": wp.rfo_no or f"WP-{wp.id}"} for wp in wps]

    return templates.TemplateResponse(
        request,
        "ot/submit.html",
        await _ctx_with_task_access(
            request,
            current_user,
            db,
            active_page="ot_new",
            my_used_hours=round(my_used / 60, 1),
            my_limit_hours=round(MONTHLY_LIMIT_MINUTES / 60, 1),
            my_used_pct=round(my_used / MONTHLY_LIMIT_MINUTES * 100, 1),
            my_remaining_hours=round((MONTHLY_LIMIT_MINUTES - my_used) / 60, 1),
            team_users=team_users,
            roster_scope_label=roster_scope_label,
            is_sup_or_admin=is_sup_or_admin,
            rfo_options=rfo_options,
            today=today.isoformat(),
        ),
    )


# ?? Desktop: /ot ??List + Mobile segment shell ??????????????????????

@router.get("/ot")
async def ot_list_page(
    request: Request,
    status: str | None = Query(None),
    search: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = current_user.get("roles", [])
    search_filter = normalize_ot_search(search)
    visible_user_ids = await get_visible_ot_user_ids(db, current_user)
    q = (
        select(OtRequest)
        .join(User, OtRequest.user_id == User.id)
        .outerjoin(WorkPackage, OtRequest.work_package_id == WorkPackage.id)
    )
    cq = (
        select(func.count())
        .select_from(OtRequest)
        .join(User, OtRequest.user_id == User.id)
        .outerjoin(WorkPackage, OtRequest.work_package_id == WorkPackage.id)
    )

    q = apply_ot_role_scope(q, visible_user_ids)
    cq = apply_ot_role_scope(cq, visible_user_ids)
    q = apply_ot_search_filter(q, search_filter)
    cq = apply_ot_search_filter(cq, search_filter)

    if status:
        q = q.where(OtRequest.status == status)
        cq = cq.where(OtRequest.status == status)
    if date_from:
        q = q.where(OtRequest.date >= date_from)
        cq = cq.where(OtRequest.date >= date_from)
    if date_to:
        q = q.where(OtRequest.date <= date_to)
        cq = cq.where(OtRequest.date <= date_to)

    total = (await db.execute(cq)).scalar() or 0
    offset = (page - 1) * per_page
    rows = (await db.execute(
        q.order_by(OtRequest.created_at.desc(), OtRequest.id.desc()).offset(offset).limit(per_page)
    )).scalars().all()

    items = await _enrich_ot_list(db, rows)
    for item in items:
        item["detail_href"] = build_href(
            f"/ot/{item['id']}",
            **_build_ot_list_state_params(
                status=status,
                search=search_filter,
                date_from=date_from,
                date_to=date_to,
                page=page,
            ),
        )
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Mobile data: monthly hours + pending/endorsed for O3
    today = date.today()
    my_used = await _monthly_used_minutes(db, current_user["user_id"], today)
    team_users = []
    if "SUPERVISOR" in roles or "ADMIN" in roles:
        roster_scope = "all" if "ADMIN" in roles else "team"
        team_users = await _team_users_with_hours(
            db,
            current_user.get("team"),
            today,
            scope=roster_scope,
        )

    # Work packages for RFO dropdown (mobile O1)
    wps = (await db.execute(select(WorkPackage))).scalars().all()
    rfo_options = [{"id": wp.id, "rfo_no": wp.rfo_no or f"WP-{wp.id}"} for wp in wps]

    return templates.TemplateResponse(
        request,
        "ot/list.html",
        await _ctx_with_task_access(
            request,
            current_user,
            db,
            active_page="ot_list",
            items=items,
            total=total,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            status_filter=status or "",
            search_filter=search_filter,
            date_from_filter=date_from.isoformat() if date_from else "",
            date_to_filter=date_to.isoformat() if date_to else "",
            my_used_hours=round(my_used / 60, 1),
            my_limit_hours=round(MONTHLY_LIMIT_MINUTES / 60, 1),
            my_used_pct=round(my_used / MONTHLY_LIMIT_MINUTES * 100, 1),
            my_remaining_hours=round((MONTHLY_LIMIT_MINUTES - my_used) / 60, 1),
            team_users=team_users,
            is_sup_or_admin="SUPERVISOR" in roles or "ADMIN" in roles,
            rfo_options=rfo_options,
            today=today.isoformat(),
            can_export_csv="SUPERVISOR" in roles or "ADMIN" in roles,
        ),
    )


# ?? Desktop: /ot/{id} ??Detail ??????????????????????????????????????

@router.get("/ot/{ot_id}")
async def ot_detail_page(
    request: Request,
    ot_id: int,
    status: str | None = Query(None),
    search: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    page: int | None = Query(None, ge=1),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task_access_ctx = await build_task_access_context(db, current_user)
    search_filter = normalize_ot_search(search)
    back_href = _ot_detail_back_href(
        status=status,
        search=search_filter,
        date_from=date_from,
        date_to=date_to,
        page=page,
    )
    detail_href = build_href(
        f"/ot/{ot_id}",
        **_build_ot_list_state_params(
            status=status,
            search=search_filter,
            date_from=date_from,
            date_to=date_to,
            page=page,
        ),
    )
    ot = (await db.execute(select(OtRequest).where(OtRequest.id == ot_id))).scalar_one_or_none()
    if not ot:
        return templates.TemplateResponse(request, "ot/detail.html", _ctx(
            request,
            current_user,
            **task_access_ctx,
            active_page="ot_list",
            ot=None,
            error_title="OT request not found",
            error_message="The OT request you are looking for does not exist.",
            back_href=back_href,
            detail_href=detail_href,
            can_cancel=False,
            can_endorse=False,
            can_approve=False,
        ), status_code=404)

    can_view = await _can_view_ot_request(db, current_user, ot)
    if not can_view:
        return templates.TemplateResponse(request, "ot/detail.html", _ctx(
            request,
            current_user,
            **task_access_ctx,
            active_page="ot_list",
            ot=None,
            error_title="Access denied",
            error_message="You do not have permission to view this OT request.",
            back_href=back_href,
            detail_href=detail_href,
            can_cancel=False,
            can_endorse=False,
            can_approve=False,
        ), status_code=403)

    items = await _enrich_ot_list(db, [ot])
    item = items[0] if items else None

    # Get user monthly hours
    used = await _monthly_used_minutes(db, ot.user_id, ot.date)
    action_flags = _ot_detail_action_flags(current_user, ot)

    return templates.TemplateResponse(request, "ot/detail.html", _ctx(
        request, current_user,
        **task_access_ctx,
        active_page="ot_list",
        ot=item,
        back_href=back_href,
        detail_href=detail_href,
        used_hours=round(used / 60, 1),
        limit_hours=round(MONTHLY_LIMIT_MINUTES / 60, 1),
        used_pct=round(used / MONTHLY_LIMIT_MINUTES * 100, 1),
        **action_flags,
    ))


# ?? Desktop: /admin/ot-approve ??ENDORSED queue ?????????????????????

@router.get("/admin/ot-approve")
async def ot_approve_page(
    request: Request,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    items = await _load_admin_ot_approve_items(db, current_user)

    return templates.TemplateResponse(
        request,
        "ot/approve.html",
        await _ctx_with_task_access(
            request,
            current_user,
            db,
            active_page="ot_approve",
            items=items,
            count=len(items),
        ),
    )


# ?? Mobile HTMX: /ot/segment/{seg} ??????????????????????????????????

@router.get("/ot/segment/{segment}")
async def ot_segment(
    request: Request,
    segment: str,
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _is_htmx_request(request):
        return RedirectResponse("/ot", status_code=302)

    today = date.today()
    roles = current_user.get("roles", [])

    if segment == "o1":
        my_used = await _monthly_used_minutes(db, current_user["user_id"], today)
        team_users = []
        if "SUPERVISOR" in roles or "ADMIN" in roles:
            roster_scope = "all" if "ADMIN" in roles else "team"
            team_users = await _team_users_with_hours(
                db,
                current_user.get("team"),
                today,
                scope=roster_scope,
            )
        wps = (await db.execute(select(WorkPackage))).scalars().all()
        rfo_options = [{"id": wp.id, "rfo_no": wp.rfo_no or f"WP-{wp.id}"} for wp in wps]

        return templates.TemplateResponse(request, "ot/partials/_o1_submit.html", {
            "request": request,
            "current_user": {"user_id": current_user["user_id"], "roles": roles,
                             "team": current_user.get("team"), "display_name": current_user.get("display_name", "")},
            "my_used_hours": round(my_used / 60, 1),
            "my_limit_hours": round(MONTHLY_LIMIT_MINUTES / 60, 1),
            "my_used_pct": round(my_used / MONTHLY_LIMIT_MINUTES * 100, 1),
            "my_remaining_hours": round((MONTHLY_LIMIT_MINUTES - my_used) / 60, 1),
            "is_sup_or_admin": "SUPERVISOR" in roles or "ADMIN" in roles,
            "team_users": team_users,
            "rfo_options": rfo_options,
            "today": today.isoformat(),
        })

    elif segment == "o2":
        visible_user_ids = await get_visible_ot_user_ids(db, current_user)
        q = apply_ot_role_scope(select(OtRequest), visible_user_ids)
        cq = apply_ot_role_scope(select(func.count()).select_from(OtRequest), visible_user_ids)
        if status:
            q = q.where(OtRequest.status == status)
            cq = cq.where(OtRequest.status == status)

        total = (await db.execute(cq)).scalar() or 0
        total_pages = max(1, (total + MOBILE_O2_PER_PAGE - 1) // MOBILE_O2_PER_PAGE)
        current_page = min(page, total_pages)
        offset = (current_page - 1) * MOBILE_O2_PER_PAGE
        rows = (await db.execute(
            q.order_by(OtRequest.created_at.desc(), OtRequest.id.desc())
            .offset(offset)
            .limit(MOBILE_O2_PER_PAGE)
        )).scalars().all()
        items = await _enrich_ot_list(db, rows)
        for item in items:
            item["detail_href"] = build_href(
                f"/ot/detail/{item['id']}",
                status=status or None,
                page=current_page if current_page > 1 else None,
            )

        return templates.TemplateResponse(request, "ot/partials/_o2_list.html", {
            "request": request,
            "current_user": {"user_id": current_user["user_id"], "roles": roles},
            "items": items,
            "status_filter": status or "",
            "page": current_page,
            "total": total,
            "total_pages": total_pages,
        })

    elif segment == "o3":
        # Supervisor sees PENDING (not own), Admin sees ENDORSED (not own)
        approve_items = []
        if "SUPERVISOR" in roles and "ADMIN" not in roles:
            approve_items = await _load_supervisor_ot_endorse_items(db, current_user)

        elif "ADMIN" in roles:
            approve_items = await _load_admin_ot_approve_items(db, current_user)

        return templates.TemplateResponse(request, "ot/partials/_o3_approve.html", {
            "request": request,
            "current_user": {"user_id": current_user["user_id"], "roles": roles},
            "items": approve_items,
            "count": len(approve_items),
        })

    # Unknown segment
    return templates.TemplateResponse(request, "ot/partials/_o1_submit.html", {
        "request": request,
        "current_user": {"user_id": current_user["user_id"], "roles": roles},
    })


# ?? Mobile HTMX: /ot/detail/{id} ??O2 detail ???????????????????????

@router.get("/ot/detail/{ot_id}")
async def ot_mobile_detail(
    request: Request,
    ot_id: int,
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _is_htmx_request(request):
        return RedirectResponse("/ot", status_code=302)

    back_href = _ot_mobile_history_href(status, page)
    detail_href = build_href(
        f"/ot/detail/{ot_id}",
        status=status or None,
        page=page if page > 1 else None,
    )
    ot = (await db.execute(select(OtRequest).where(OtRequest.id == ot_id))).scalar_one_or_none()
    if not ot:
        return templates.TemplateResponse(request, "ot/partials/_o2_detail.html", {
            "request": request,
            "ot": None,
            "error_title": "OT request not found",
            "error_message": "The OT request you are looking for does not exist.",
            "back_href": back_href,
            "detail_href": detail_href,
            "can_cancel": False,
            "current_user": {"user_id": current_user["user_id"], "roles": current_user.get("roles", [])},
        }, status_code=404)
    can_view = await _can_view_ot_request(db, current_user, ot)
    if not can_view:
        return templates.TemplateResponse(request, "ot/partials/_o2_detail.html", {
            "request": request,
            "ot": None,
            "error_title": "Access denied",
            "error_message": "You do not have permission to view this OT request.",
            "back_href": back_href,
            "detail_href": detail_href,
            "can_cancel": False,
            "current_user": {"user_id": current_user["user_id"], "roles": current_user.get("roles", [])},
        }, status_code=403)
    items = await _enrich_ot_list(db, [ot])
    item = items[0]
    used = await _monthly_used_minutes(db, ot.user_id, ot.date)
    item["monthly_used_hours"] = round(used / 60, 1)
    item["monthly_limit_hours"] = round(MONTHLY_LIMIT_MINUTES / 60, 1)
    item["monthly_pct"] = round(used / MONTHLY_LIMIT_MINUTES * 100, 1)
    action_flags = _ot_detail_action_flags(current_user, ot)

    return templates.TemplateResponse(request, "ot/partials/_o2_detail.html", {
        "request": request,
        "ot": item,
        "back_href": back_href,
        "detail_href": detail_href,
        "current_user": {"user_id": current_user["user_id"], "roles": current_user.get("roles", [])},
        **action_flags,
    })
