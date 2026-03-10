"""OT SSR views (Branch 04 commits 5-6)."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_role
from app.models.ot import OtApproval, OtRequest
from app.models.reference import WorkPackage
from app.models.user import User
from app.services.ot_service import MONTHLY_LIMIT_MINUTES, _monthly_used_minutes
from app.views import templates

router = APIRouter(tags=["ot-views"])


# ── Helpers ──────────────────────────────────────────────────────────

STATUS_BADGES = {
    "PENDING": ("badge-pending", "PENDING"),
    "ENDORSED": ("badge-endorsed", "ENDORSED"),
    "APPROVED": ("badge-approved", "APPROVED"),
    "REJECTED": ("badge-rejected", "REJECTED"),
    "CANCELLED": ("badge-cancelled", "CANCELLED"),
}


async def _team_users_with_hours(db: AsyncSession, team: str | None, month: date) -> list[dict]:
    """Get team users with their monthly OT hours for the roster."""
    if not team:
        return []
    users = (
        await db.execute(select(User).where(User.team == team, User.is_active == True))
    ).scalars().all()
    result = []
    for u in users:
        used = await _monthly_used_minutes(db, u.id, month)
        pct = round(used / MONTHLY_LIMIT_MINUTES * 100, 1) if MONTHLY_LIMIT_MINUTES else 0
        result.append({
            "id": u.id,
            "name": u.name,
            "employee_no": u.employee_no,
            "used_hours": round(used / 60, 1),
            "limit_hours": round(MONTHLY_LIMIT_MINUTES / 60, 1),
            "used_pct": pct,
            "at_limit": used >= MONTHLY_LIMIT_MINUTES,
            "bar_color": "#b93a3a" if pct >= 100 else "#c8850a" if pct >= 70 else "#2e5a8a",
        })
    return result


async def _enrich_ot_list(db: AsyncSession, rows: list) -> list[dict]:
    """Enrich OT rows with user names and approval info."""
    user_ids = set()
    for r in rows:
        user_ids.add(r.user_id)
        if r.submitted_by:
            user_ids.add(r.submitted_by)
    users_map = {}
    if user_ids:
        us = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        users_map = {u.id: u for u in us}

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


def _ctx(request, user, **kw):
    """Build base template context."""
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
        **kw,
    }


# ── Desktop: /ot/new — Submit form ──────────────────────────────────

@router.get("/ot/new")
async def ot_submit_page(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    my_used = await _monthly_used_minutes(db, current_user["user_id"], today)
    roles = current_user.get("roles", [])
    is_sup_or_admin = "SUPERVISOR" in roles or "ADMIN" in roles

    team_users = []
    if is_sup_or_admin:
        team_users = await _team_users_with_hours(db, current_user.get("team"), today)

    # Work packages for RFO dropdown
    wps = (await db.execute(select(WorkPackage))).scalars().all()
    rfo_options = [{"id": wp.id, "rfo_no": wp.rfo_no or f"WP-{wp.id}"} for wp in wps]

    return templates.TemplateResponse("ot/submit.html", _ctx(
        request, current_user,
        active_page="ot_new",
        my_used_hours=round(my_used / 60, 1),
        my_limit_hours=round(MONTHLY_LIMIT_MINUTES / 60, 1),
        my_used_pct=round(my_used / MONTHLY_LIMIT_MINUTES * 100, 1),
        my_remaining_hours=round((MONTHLY_LIMIT_MINUTES - my_used) / 60, 1),
        team_users=team_users,
        is_sup_or_admin=is_sup_or_admin,
        rfo_options=rfo_options,
        today=today.isoformat(),
    ))


# ── Desktop: /ot — List + Mobile segment shell ──────────────────────

@router.get("/ot")
async def ot_list_page(
    request: Request,
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = current_user.get("roles", [])
    q = select(OtRequest)
    cq = select(func.count()).select_from(OtRequest)

    # Role scoping
    if "ADMIN" not in roles:
        if "SUPERVISOR" in roles:
            team_uids = (await db.execute(
                select(User.id).where(User.team == current_user.get("team"))
            )).scalars().all()
            q = q.where(OtRequest.user_id.in_(team_uids))
            cq = cq.where(OtRequest.user_id.in_(team_uids))
        else:
            q = q.where(OtRequest.user_id == current_user["user_id"])
            cq = cq.where(OtRequest.user_id == current_user["user_id"])

    if status:
        q = q.where(OtRequest.status == status)
        cq = cq.where(OtRequest.status == status)

    total = (await db.execute(cq)).scalar() or 0
    offset = (page - 1) * per_page
    rows = (await db.execute(
        q.order_by(OtRequest.created_at.desc()).offset(offset).limit(per_page)
    )).scalars().all()

    items = await _enrich_ot_list(db, rows)
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Mobile data: monthly hours + pending/endorsed for O3
    today = date.today()
    my_used = await _monthly_used_minutes(db, current_user["user_id"], today)
    team_users = []
    if "SUPERVISOR" in roles or "ADMIN" in roles:
        team_users = await _team_users_with_hours(db, current_user.get("team"), today)

    # Work packages for RFO dropdown (mobile O1)
    wps = (await db.execute(select(WorkPackage))).scalars().all()
    rfo_options = [{"id": wp.id, "rfo_no": wp.rfo_no or f"WP-{wp.id}"} for wp in wps]

    return templates.TemplateResponse("ot/list.html", _ctx(
        request, current_user,
        active_page="ot_list",
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        status_filter=status or "",
        my_used_hours=round(my_used / 60, 1),
        my_limit_hours=round(MONTHLY_LIMIT_MINUTES / 60, 1),
        my_used_pct=round(my_used / MONTHLY_LIMIT_MINUTES * 100, 1),
        my_remaining_hours=round((MONTHLY_LIMIT_MINUTES - my_used) / 60, 1),
        team_users=team_users,
        is_sup_or_admin="SUPERVISOR" in roles or "ADMIN" in roles,
        rfo_options=rfo_options,
        today=today.isoformat(),
    ))


# ── Desktop: /ot/{id} — Detail ──────────────────────────────────────

@router.get("/ot/{ot_id}")
async def ot_detail_page(
    request: Request,
    ot_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ot = (await db.execute(select(OtRequest).where(OtRequest.id == ot_id))).scalar_one_or_none()
    if not ot:
        return templates.TemplateResponse("ot/detail.html", _ctx(
            request, current_user, active_page="ot_list", ot=None, error="OT request not found",
        ), status_code=404)

    items = await _enrich_ot_list(db, [ot])
    item = items[0] if items else None

    # Get user monthly hours
    used = await _monthly_used_minutes(db, ot.user_id, ot.date)

    return templates.TemplateResponse("ot/detail.html", _ctx(
        request, current_user,
        active_page="ot_list",
        ot=item,
        used_hours=round(used / 60, 1),
        limit_hours=round(MONTHLY_LIMIT_MINUTES / 60, 1),
        used_pct=round(used / MONTHLY_LIMIT_MINUTES * 100, 1),
    ))


# ── Desktop: /admin/ot-approve — ENDORSED queue ─────────────────────

@router.get("/admin/ot-approve")
async def ot_approve_page(
    request: Request,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(OtRequest)
        .where(OtRequest.status == "ENDORSED")
        .order_by(OtRequest.created_at.desc())
    )).scalars().all()

    items = await _enrich_ot_list(db, rows)

    # Add monthly hours for each user
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

    return templates.TemplateResponse("ot/approve.html", _ctx(
        request, current_user,
        active_page="ot_approve",
        items=items,
        count=len(items),
    ))


# ── Mobile HTMX: /ot/segment/{seg} ──────────────────────────────────

@router.get("/ot/segment/{segment}")
async def ot_segment(
    request: Request,
    segment: str,
    status: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    roles = current_user.get("roles", [])

    if segment == "o1":
        my_used = await _monthly_used_minutes(db, current_user["user_id"], today)
        team_users = []
        if "SUPERVISOR" in roles or "ADMIN" in roles:
            team_users = await _team_users_with_hours(db, current_user.get("team"), today)
        wps = (await db.execute(select(WorkPackage))).scalars().all()
        rfo_options = [{"id": wp.id, "rfo_no": wp.rfo_no or f"WP-{wp.id}"} for wp in wps]

        return templates.TemplateResponse("ot/partials/_o1_submit.html", {
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
        q = select(OtRequest)
        if "ADMIN" not in roles:
            if "SUPERVISOR" in roles:
                team_uids = (await db.execute(
                    select(User.id).where(User.team == current_user.get("team"))
                )).scalars().all()
                q = q.where(OtRequest.user_id.in_(team_uids))
            else:
                q = q.where(OtRequest.user_id == current_user["user_id"])
        if status:
            q = q.where(OtRequest.status == status)
        rows = (await db.execute(q.order_by(OtRequest.created_at.desc()).limit(50))).scalars().all()
        items = await _enrich_ot_list(db, rows)

        return templates.TemplateResponse("ot/partials/_o2_list.html", {
            "request": request,
            "current_user": {"user_id": current_user["user_id"], "roles": roles},
            "items": items,
            "status_filter": status or "",
        })

    elif segment == "o3":
        # Supervisor sees PENDING (not own), Admin sees ENDORSED (not own)
        approve_items = []
        if "SUPERVISOR" in roles and "ADMIN" not in roles:
            rows = (await db.execute(
                select(OtRequest).where(
                    OtRequest.status == "PENDING",
                    OtRequest.user_id != current_user["user_id"],
                ).order_by(OtRequest.created_at.desc())
            )).scalars().all()
            # Filter to same team
            filtered = []
            for r in rows:
                u = (await db.execute(select(User).where(User.id == r.user_id))).scalar_one_or_none()
                if u and u.team == current_user.get("team"):
                    filtered.append(r)
            approve_items = await _enrich_ot_list(db, filtered)
            for item in approve_items:
                used = await _monthly_used_minutes(db, item["user_id"], item["date"])
                item["monthly_used_hours"] = round(used / 60, 1)
                item["monthly_limit_hours"] = round(MONTHLY_LIMIT_MINUTES / 60, 1)
                item["monthly_pct"] = round(used / MONTHLY_LIMIT_MINUTES * 100, 1)
                item["monthly_bar_color"] = (
                    "#b93a3a" if item["monthly_pct"] >= 100
                    else "#c8850a" if item["monthly_pct"] >= 70
                    else "#2e5a8a"
                )

        elif "ADMIN" in roles:
            rows = (await db.execute(
                select(OtRequest).where(
                    OtRequest.status == "ENDORSED",
                    OtRequest.user_id != current_user["user_id"],
                ).order_by(OtRequest.created_at.desc())
            )).scalars().all()
            approve_items = await _enrich_ot_list(db, rows)
            for item in approve_items:
                used = await _monthly_used_minutes(db, item["user_id"], item["date"])
                item["monthly_used_hours"] = round(used / 60, 1)
                item["monthly_limit_hours"] = round(MONTHLY_LIMIT_MINUTES / 60, 1)
                item["monthly_pct"] = round(used / MONTHLY_LIMIT_MINUTES * 100, 1)
                item["monthly_bar_color"] = (
                    "#b93a3a" if item["monthly_pct"] >= 100
                    else "#c8850a" if item["monthly_pct"] >= 70
                    else "#2e5a8a"
                )

        return templates.TemplateResponse("ot/partials/_o3_approve.html", {
            "request": request,
            "current_user": {"user_id": current_user["user_id"], "roles": roles},
            "items": approve_items,
            "count": len(approve_items),
        })

    # Unknown segment
    return templates.TemplateResponse("ot/partials/_o1_submit.html", {
        "request": request,
        "current_user": {"user_id": current_user["user_id"], "roles": roles},
    })


# ── Mobile HTMX: /ot/detail/{id} — O2 detail ───────────────────────

@router.get("/ot/detail/{ot_id}")
async def ot_mobile_detail(
    request: Request,
    ot_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ot = (await db.execute(select(OtRequest).where(OtRequest.id == ot_id))).scalar_one_or_none()
    if not ot:
        return templates.TemplateResponse("ot/partials/_o2_detail.html", {
            "request": request, "ot": None, "current_user": {"user_id": current_user["user_id"], "roles": current_user.get("roles", [])},
        })
    items = await _enrich_ot_list(db, [ot])
    item = items[0]
    used = await _monthly_used_minutes(db, ot.user_id, ot.date)
    item["monthly_used_hours"] = round(used / 60, 1)
    item["monthly_limit_hours"] = round(MONTHLY_LIMIT_MINUTES / 60, 1)
    item["monthly_pct"] = round(used / MONTHLY_LIMIT_MINUTES * 100, 1)

    return templates.TemplateResponse("ot/partials/_o2_detail.html", {
        "request": request,
        "ot": item,
        "current_user": {"user_id": current_user["user_id"], "roles": current_user.get("roles", [])},
    })
