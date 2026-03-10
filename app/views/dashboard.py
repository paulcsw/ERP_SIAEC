"""Dashboard SSR views."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.ot import OtRequest
from app.models.reference import Aircraft
from app.models.task import TaskItem, TaskSnapshot
from app.models.user import User
from app.views import templates

router = APIRouter(tags=["dashboard-views"])

COUNTABLE_OT_STATUSES = ("APPROVED", "PENDING", "ENDORSED")


def _ctx(request, user, **kw):
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
        "page": "dashboard",
        **kw,
    }


def _task_scope(stmt, current_user: dict):
    roles = set(current_user.get("roles", []))
    if "ADMIN" in roles:
        return stmt
    if "SUPERVISOR" in roles:
        return stmt.where(TaskItem.assigned_supervisor_id == current_user["user_id"])
    return stmt.where(TaskItem.assigned_worker_id == current_user["user_id"])


def _ot_scope(stmt, current_user: dict):
    roles = set(current_user.get("roles", []))
    if "ADMIN" in roles:
        return stmt
    if "SUPERVISOR" in roles:
        return stmt.where(User.team == current_user.get("team"))
    return stmt.where(OtRequest.user_id == current_user["user_id"])


@router.get("/")
async def root_redirect(current_user: dict = Depends(get_current_user)):
    """Authenticated app landing page."""
    _ = current_user
    return RedirectResponse("/dashboard", status_code=302)


@router.get("/dashboard")
async def dashboard_page(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    month_start = today.replace(day=1)

    # Latest non-deleted snapshot per task.
    latest_snapshot_sq = (
        select(
            TaskSnapshot.task_id.label("task_id"),
            func.max(TaskSnapshot.meeting_date).label("meeting_date"),
        )
        .where(TaskSnapshot.is_deleted == False)  # noqa: E712
        .group_by(TaskSnapshot.task_id)
        .subquery()
    )

    task_base = (
        select(TaskSnapshot, TaskItem, Aircraft)
        .join(
            latest_snapshot_sq,
            and_(
                TaskSnapshot.task_id == latest_snapshot_sq.c.task_id,
                TaskSnapshot.meeting_date == latest_snapshot_sq.c.meeting_date,
            ),
        )
        .join(TaskItem, TaskItem.id == TaskSnapshot.task_id)
        .outerjoin(Aircraft, Aircraft.id == TaskItem.aircraft_id)
        .where(
            TaskItem.is_active == True,  # noqa: E712
            TaskSnapshot.is_deleted == False,  # noqa: E712
        )
    )
    task_base = _task_scope(task_base, current_user)

    status_rows = (
        await db.execute(
            task_base.with_only_columns(
                TaskSnapshot.status,
                func.count().label("count"),
            ).group_by(TaskSnapshot.status)
        )
    ).all()
    status_map = {status: count for status, count in status_rows}
    ordered_statuses = ("NOT_STARTED", "IN_PROGRESS", "WAITING", "COMPLETED")
    total_tasks = sum(status_map.values())
    completed_tasks = status_map.get("COMPLETED", 0)
    completion_pct = round((completed_tasks / total_tasks * 100), 1) if total_tasks else 0.0

    blockers_count = (
        await db.execute(
            task_base.with_only_columns(func.count()).where(
                TaskSnapshot.has_issue == True,  # noqa: E712
                TaskSnapshot.status != "COMPLETED",
            )
        )
    ).scalar() or 0

    active_aircraft = (
        await db.execute(
            task_base.with_only_columns(func.count(func.distinct(TaskItem.aircraft_id)))
        )
    ).scalar() or 0

    recent_task_rows = (
        await db.execute(
            task_base.order_by(TaskSnapshot.last_updated_at.desc()).limit(8)
        )
    ).all()
    recent_tasks = []
    for snap, task, ac in recent_task_rows:
        recent_tasks.append(
            {
                "task_id": task.id,
                "task_text": task.task_text,
                "ac_reg": ac.ac_reg if ac else "-",
                "status": snap.status,
                "status_class": {
                    "NOT_STARTED": "badge-not-started",
                    "IN_PROGRESS": "badge-in-progress",
                    "WAITING": "badge-waiting",
                    "COMPLETED": "badge-completed",
                }.get(snap.status, "badge-not-started"),
                "has_issue": bool(snap.has_issue),
                "updated_at": snap.last_updated_at.strftime("%Y-%m-%d %H:%M"),
            }
        )

    status_bars = []
    for status in ordered_statuses:
        count = status_map.get(status, 0)
        pct = round((count / total_tasks * 100), 1) if total_tasks else 0.0
        status_bars.append(
            {
                "status": status,
                "count": count,
                "pct": pct,
            }
        )

    ot_month_stmt = (
        select(func.coalesce(func.sum(OtRequest.requested_minutes), 0))
        .select_from(OtRequest)
        .join(User, User.id == OtRequest.user_id)
        .where(
            OtRequest.date >= month_start,
            OtRequest.date <= today,
            OtRequest.status.in_(COUNTABLE_OT_STATUSES),
        )
    )
    ot_month_stmt = _ot_scope(ot_month_stmt, current_user)
    ot_month_minutes = (await db.execute(ot_month_stmt)).scalar() or 0
    ot_month_hours = round(ot_month_minutes / 60, 1)

    roles = set(current_user.get("roles", []))
    if "ADMIN" in roles:
        awaiting_stmt = select(func.count()).select_from(OtRequest).where(
            OtRequest.status == "ENDORSED",
            OtRequest.user_id != current_user["user_id"],
        )
    elif "SUPERVISOR" in roles:
        awaiting_stmt = (
            select(func.count())
            .select_from(OtRequest)
            .join(User, User.id == OtRequest.user_id)
            .where(
                OtRequest.status == "PENDING",
                User.team == current_user.get("team"),
                OtRequest.user_id != current_user["user_id"],
            )
        )
    else:
        awaiting_stmt = select(func.count()).select_from(OtRequest).where(
            OtRequest.status == "PENDING",
            OtRequest.user_id == current_user["user_id"],
        )
    ot_awaiting_count = (await db.execute(awaiting_stmt)).scalar() or 0

    recent_ot_stmt = (
        select(OtRequest, User)
        .join(User, User.id == OtRequest.user_id)
        .order_by(OtRequest.created_at.desc())
        .limit(8)
    )
    recent_ot_stmt = _ot_scope(recent_ot_stmt, current_user)
    recent_ot_rows = (await db.execute(recent_ot_stmt)).all()
    recent_ots = []
    for ot, user in recent_ot_rows:
        status_class = {
            "PENDING": "badge-pending",
            "ENDORSED": "badge-endorsed",
            "APPROVED": "badge-approved",
            "REJECTED": "badge-rejected",
            "CANCELLED": "badge-cancelled",
        }.get(ot.status, "badge-pending")
        recent_ots.append(
            {
                "id": ot.id,
                "employee_name": user.name,
                "date": ot.date.isoformat(),
                "hours": round(ot.requested_minutes / 60, 1),
                "status": ot.status,
                "status_class": status_class,
            }
        )

    ot_trend_start = today - timedelta(days=6)
    trend_stmt = (
        select(
            OtRequest.date,
            func.coalesce(func.sum(OtRequest.requested_minutes), 0).label("minutes"),
        )
        .select_from(OtRequest)
        .join(User, User.id == OtRequest.user_id)
        .where(
            OtRequest.date >= ot_trend_start,
            OtRequest.date <= today,
            OtRequest.status.in_(COUNTABLE_OT_STATUSES),
        )
        .group_by(OtRequest.date)
    )
    trend_stmt = _ot_scope(trend_stmt, current_user)
    trend_rows = (await db.execute(trend_stmt)).all()
    trend_map = {d: minutes for d, minutes in trend_rows}
    trend_items = []
    max_hours = 0.0
    for i in range(7):
        day = ot_trend_start + timedelta(days=i)
        hours = round((trend_map.get(day, 0) or 0) / 60, 1)
        max_hours = max(max_hours, hours)
        trend_items.append({"label": day.strftime("%a"), "hours": hours})
    for item in trend_items:
        if max_hours <= 0:
            item["width"] = 4
        else:
            item["width"] = max(4, int((item["hours"] / max_hours) * 100))

    return templates.TemplateResponse(
        "dashboard/index.html",
        _ctx(
            request,
            current_user,
            summary={
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "completion_pct": completion_pct,
                "open_blockers": blockers_count,
                "active_aircraft": active_aircraft,
                "ot_month_hours": ot_month_hours,
                "ot_awaiting_count": ot_awaiting_count,
            },
            status_bars=status_bars,
            recent_tasks=recent_tasks,
            recent_ots=recent_ots,
            ot_trend=trend_items,
        ),
    )
