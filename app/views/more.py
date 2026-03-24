"""More and global search SSR views."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.ot import OtRequest
from app.models.reference import Aircraft, WorkPackage
from app.models.shop import Shop
from app.models.task import TaskItem, TaskSnapshot
from app.models.user import User
from app.models.user_shop_access import UserShopAccess
from app.services.ot_service import (
    apply_ot_role_scope,
    apply_ot_search_filter,
    get_visible_ot_user_ids,
    normalize_ot_search,
)
from app.views import templates
from app.views.tasks import _entry_visibility_clause, _get_allowed_shop_ids

router = APIRouter(tags=["more-views"])


def _ctx(request, user, **kw):
    """Build base template context for More and search pages."""
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
        "active_tab": kw.pop("active_tab", "more"),
        **kw,
    }


def _normalize_search_query(value: str | None) -> str:
    return (value or "").strip()


def _build_href(path: str, **params) -> str:
    filtered = {
        key: value
        for key, value in params.items()
        if value is not None and value != ""
    }
    if not filtered:
        return path
    return f"{path}?{urlencode(filtered)}"


async def _search_tasks(db: AsyncSession, user: dict, query: str, limit: int = 8) -> dict:
    allowed_shop_ids = await _get_allowed_shop_ids(db, user)
    visibility_clause = _entry_visibility_clause(user, allowed_shop_ids)
    if not query:
        return {"title": "Task Results", "results": [], "has_more": False}

    term = f"%{query}%"
    exact_id = int(query) if query.isdigit() else None

    task_query = (
        select(TaskSnapshot, TaskItem, Aircraft, WorkPackage, User, Shop)
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .join(Aircraft, TaskItem.aircraft_id == Aircraft.id)
        .outerjoin(WorkPackage, TaskItem.work_package_id == WorkPackage.id)
        .outerjoin(User, TaskItem.assigned_worker_id == User.id)
        .outerjoin(Shop, TaskItem.shop_id == Shop.id)
        .where(TaskItem.is_active == True, TaskSnapshot.is_deleted == False)  # noqa: E712
    )
    if visibility_clause is not None:
        task_query = task_query.where(visibility_clause)

    search_clauses = [
        Aircraft.ac_reg.ilike(term),
        TaskItem.task_text.ilike(term),
        WorkPackage.rfo_no.ilike(term),
        WorkPackage.title.ilike(term),
        Shop.code.ilike(term),
        Shop.name.ilike(term),
    ]
    if exact_id is not None:
        search_clauses.append(TaskItem.id == exact_id)

    rows = (
        await db.execute(
            task_query
            .where(or_(*search_clauses))
            .order_by(TaskSnapshot.meeting_date.desc(), TaskSnapshot.last_updated_at.desc(), TaskItem.id.desc())
            .limit(limit + 1)
        )
    ).all()

    has_more = len(rows) > limit
    results = []
    for snap, task, aircraft, wp, worker, shop in rows[:limit]:
        results.append({
            "href": _build_href(
                "/tasks/entry",
                ac=aircraft.ac_reg,
                meeting_date=snap.meeting_date.isoformat() if snap.meeting_date else None,
                edit=task.id,
            ),
            "title": task.task_text,
            "eyebrow": aircraft.ac_reg,
            "meta": " | ".join(filter(None, [
                wp.rfo_no if wp and wp.rfo_no else None,
                snap.meeting_date.isoformat() if snap.meeting_date else None,
                snap.status,
                shop.code if shop else None,
                worker.name if worker else None,
            ])),
            "summary": (snap.critical_issue or snap.remarks or "").strip(),
        })

    return {
        "title": "Task Results",
        "description": "Direct links into Data Entry for matching tasks.",
        "results": results,
        "has_more": has_more,
    }


async def _search_ot(db: AsyncSession, user: dict, query: str, limit: int = 8) -> dict:
    normalized = normalize_ot_search(query)
    if not normalized:
        return {"title": "OT Results", "results": [], "has_more": False}

    ot_query = (
        select(OtRequest, User, WorkPackage)
        .join(User, OtRequest.user_id == User.id)
        .outerjoin(WorkPackage, OtRequest.work_package_id == WorkPackage.id)
    )
    visible_user_ids = await get_visible_ot_user_ids(db, user)
    ot_query = apply_ot_role_scope(ot_query, visible_user_ids)
    ot_query = apply_ot_search_filter(ot_query, normalized)

    rows = (
        await db.execute(
            ot_query
            .order_by(OtRequest.created_at.desc(), OtRequest.id.desc())
            .limit(limit + 1)
        )
    ).all()

    has_more = len(rows) > limit
    results = []
    for ot, ot_user, wp in rows[:limit]:
        reason_text = (ot.reason_text or "").strip()
        results.append({
            "href": f"/ot/{ot.id}",
            "title": f"OT-{ot.id:03d}",
            "eyebrow": ot_user.name,
            "meta": " | ".join(filter(None, [
                ot.date.isoformat() if ot.date else None,
                f"{round(ot.requested_minutes / 60, 1)}h",
                ot.status,
                wp.rfo_no if wp and wp.rfo_no else None,
            ])),
            "summary": reason_text or ot.reason_code.replace("_", " ").title(),
        })

    return {
        "title": "OT Results",
        "description": "Requests you can open directly from the result list.",
        "results": results,
        "has_more": has_more,
        "list_href": _build_href("/ot", search=normalized),
        "link_label": "Open OT List",
    }


async def _search_rfo(db: AsyncSession, user: dict, query: str, limit: int = 8) -> dict | None:
    roles = user.get("roles", [])
    if "SUPERVISOR" not in roles and "ADMIN" not in roles:
        return None
    if not query:
        return {"title": "RFO Results", "results": [], "has_more": False}

    term = f"%{query}%"
    exact_id = int(query) if query.isdigit() else None

    rfo_query = (
        select(WorkPackage, Aircraft)
        .join(Aircraft, WorkPackage.aircraft_id == Aircraft.id)
    )
    search_clauses = [
        WorkPackage.rfo_no.ilike(term),
        WorkPackage.title.ilike(term),
        Aircraft.ac_reg.ilike(term),
    ]
    if exact_id is not None:
        search_clauses.append(WorkPackage.id == exact_id)

    rows = (
        await db.execute(
            rfo_query
            .where(or_(*search_clauses))
            .order_by(WorkPackage.status.asc(), WorkPackage.rfo_no.asc(), WorkPackage.id.desc())
            .limit(limit + 1)
        )
    ).all()

    has_more = len(rows) > limit
    results = []
    for wp, aircraft in rows[:limit]:
        results.append({
            "href": _build_href("/rfo", id=wp.id),
            "title": wp.rfo_no or f"WP-{wp.id}",
            "eyebrow": aircraft.ac_reg,
            "meta": " | ".join(filter(None, [
                wp.title,
                wp.status,
                wp.start_date.isoformat() if wp.start_date else None,
                wp.end_date.isoformat() if wp.end_date else None,
            ])),
            "summary": "",
        })

    return {
        "title": "RFO Results",
        "description": "Supervisor and admin work package matches.",
        "results": results,
        "has_more": has_more,
    }


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
    return templates.TemplateResponse(request, "more/index.html", _ctx(request, current_user, has_task_access=task_access))


@router.get("/search")
async def global_search(
    request: Request,
    q: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = _normalize_search_query(q)
    sections = [
        await _search_tasks(db, current_user, query),
        await _search_ot(db, current_user, query),
    ]
    rfo_section = await _search_rfo(db, current_user, query)
    if rfo_section is not None:
        sections.append(rfo_section)

    return templates.TemplateResponse(request, "more/search.html", _ctx(
        request,
        current_user,
        active_tab="",
        page="search",
        header_search_query=query,
        query=query,
        search_sections=sections,
        has_any_results=any(section["results"] for section in sections),
    ))


@router.get("/more/rfo-summary")
async def more_rfo_summary(
    request: Request,
    wp_id: int | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.reference import Aircraft, WorkPackage
    from app.models.task import TaskItem, TaskSnapshot
    from sqlalchemy import func
    from datetime import datetime, timezone

    # List active work packages for selector
    wps = (await db.execute(
        select(WorkPackage, Aircraft)
        .join(Aircraft, WorkPackage.aircraft_id == Aircraft.id)
        .where(WorkPackage.status == "ACTIVE")
        .order_by(WorkPackage.rfo_no)
    )).all()
    wp_options = [
        {"id": w.id, "rfo_no": w.rfo_no or f"WP-{w.id}", "ac_reg": a.ac_reg}
        for w, a in wps
    ]

    rfo = None
    metrics = None
    blockers_list: list[dict] = []

    selected_wp = None
    if wp_id:
        selected_wp = next((w for w, _ in wps if w.id == wp_id), None)
    elif wps:
        selected_wp = wps[0][0]
        wp_id = selected_wp.id

    if selected_wp:
        ac = next((a for w, a in wps if w.id == selected_wp.id), None)
        rfo = {
            "id": selected_wp.id,
            "rfo_no": selected_wp.rfo_no or f"WP-{selected_wp.id}",
            "ac_reg": ac.ac_reg if ac else "N/A",
        }

        tasks = (await db.execute(
            select(TaskItem).where(
                TaskItem.work_package_id == selected_wp.id,
                TaskItem.is_active == True,  # noqa: E712
            )
        )).scalars().all()

        planned_mh = sum(float(t.planned_mh or 0) for t in tasks)
        actual_mh = 0.0
        completed = 0
        overdue = 0
        blocker_count = 0
        total = len(tasks)
        now = datetime.now(timezone.utc)

        for ti in tasks:
            snap = (await db.execute(
                select(TaskSnapshot)
                .where(TaskSnapshot.task_id == ti.id, TaskSnapshot.is_deleted == False)  # noqa: E712
                .order_by(TaskSnapshot.meeting_date.desc()).limit(1)
            )).scalar_one_or_none()
            if not snap:
                continue
            mh = float(snap.mh_incurred_hours or 0)
            actual_mh += mh
            if snap.status == "COMPLETED":
                completed += 1
            if snap.status != "COMPLETED" and snap.deadline_date and snap.deadline_date < now.date():
                overdue += 1
            if snap.status == "WAITING" and snap.has_issue:
                blocker_count += 1
                days = (now.date() - snap.meeting_date).days if snap.meeting_date else 0
                blockers_list.append({
                    "task_text": ti.task_text,
                    "days_waiting": max(0, days),
                })

        blockers_list.sort(key=lambda x: -x["days_waiting"])
        progress_pct = round(completed / total * 100) if total else 0
        remaining_mh = round(max(0, planned_mh - actual_mh), 1)

        metrics = {
            "progress_pct": progress_pct,
            "overdue_count": overdue,
            "blocker_count": blocker_count,
            "remaining_mh": remaining_mh,
        }

    return templates.TemplateResponse(request, "more/rfo_summary.html", _ctx(
        request, current_user,
        rfo=rfo,
        metrics=metrics,
        blockers=blockers_list[:5],
        wp_options=wp_options,
        selected_wp_id=wp_id,
    ))


@router.get("/more/help")
async def more_help(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    return templates.TemplateResponse(request, "more/help.html", _ctx(request, current_user))


@router.get("/more/font-size")
async def more_font_size(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    return templates.TemplateResponse(request, "more/font_size.html", _ctx(request, current_user))


@router.get("/more/account")
async def more_account(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    return templates.TemplateResponse(request, "more/account.html", _ctx(request, current_user))
