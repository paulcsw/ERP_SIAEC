"""Dashboard SSR view — landing page with KPIs, OT Quota, RFO Progress, Pipeline."""
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.stats import COUNTABLE_STATUSES, _parse_month
from app.models.audit import AuditLog
from app.models.ot import OtApproval, OtRequest
from app.models.reference import Aircraft, WorkPackage
from app.models.shop import Shop
from app.models.task import TaskItem, TaskSnapshot
from app.models.user import User
from app.services.ot_service import MONTHLY_LIMIT_MINUTES
from app.views import templates

router = APIRouter(tags=["dashboard"])

LIMIT_HOURS = round(MONTHLY_LIMIT_MINUTES / 60, 1)


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
        "page": "dashboard",
        **kw,
    }


@router.get("/")
async def root_redirect():
    """Redirect root to dashboard (post-login landing page)."""
    return RedirectResponse("/dashboard", status_code=302)


@router.get("/dashboard")
async def dashboard_page(
    request: Request,
    shop_id: int | None = Query(None, ge=1),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = current_user.get("roles", [])
    is_admin = "ADMIN" in roles
    first, last = _parse_month(None)  # current month
    month_label = first.strftime("%B %Y")

    shop_rows = (await db.execute(select(Shop).order_by(Shop.code.asc()))).scalars().all()
    shop_options = [{"id": s.id, "code": s.code, "name": s.name} for s in shop_rows]
    shop_by_id = {s["id"]: s for s in shop_options}

    selected_shop_id: int | None = None
    selected_shop_code: str | None = None
    dashboard_scope_label = "All Shops"
    shop_select_disabled = False

    if is_admin:
        if shop_id and shop_id in shop_by_id:
            selected_shop_id = shop_id
            selected_shop_code = shop_by_id[shop_id]["code"]
            dashboard_scope_label = shop_by_id[shop_id]["name"] or selected_shop_code
    else:
        shop_select_disabled = True
        user_team = (current_user.get("team") or "").strip()
        team_shop = next(
            (s for s in shop_options if s["code"] == user_team or s["name"] == user_team),
            None,
        )
        if team_shop:
            selected_shop_id = team_shop["id"]
            selected_shop_code = team_shop["code"]
            dashboard_scope_label = team_shop["name"] or team_shop["code"]
        elif user_team:
            dashboard_scope_label = user_team

    user_scope_ids: list[int] | None = None
    if selected_shop_code:
        user_scope_ids = (
            await db.execute(
                select(User.id).where(
                    User.team == selected_shop_code,
                    User.is_active == True,  # noqa: E712
                )
            )
        ).scalars().all()

    # ── KPI 1: Active Tasks ────────────────────────────────────────
    active_tasks_q = (
        select(func.count())
        .select_from(TaskSnapshot)
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .where(
            TaskItem.is_active == True,
            TaskSnapshot.is_deleted == False,
            TaskSnapshot.status != "COMPLETED",
        )
    )
    if selected_shop_id is not None:
        active_tasks_q = active_tasks_q.where(TaskItem.shop_id == selected_shop_id)
    active_tasks = (await db.execute(active_tasks_q)).scalar() or 0

    # ── KPI 2-3: OT Pending / Endorsed ─────────────────────────────
    ot_pending_q = select(func.count()).select_from(OtRequest).where(OtRequest.status == "PENDING")
    ot_endorsed_q = select(func.count()).select_from(OtRequest).where(OtRequest.status == "ENDORSED")
    if user_scope_ids is not None:
        if user_scope_ids:
            ot_pending_q = ot_pending_q.where(OtRequest.user_id.in_(user_scope_ids))
            ot_endorsed_q = ot_endorsed_q.where(OtRequest.user_id.in_(user_scope_ids))
        else:
            ot_pending_q = ot_pending_q.where(OtRequest.id == -1)
            ot_endorsed_q = ot_endorsed_q.where(OtRequest.id == -1)
    ot_pending = (await db.execute(ot_pending_q)).scalar() or 0
    ot_endorsed = (await db.execute(ot_endorsed_q)).scalar() or 0

    # ── KPI 4: Critical Issues ──────────────────────────────────────
    critical_issues_q = (
        select(func.count())
        .select_from(TaskSnapshot)
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .where(
            TaskItem.is_active == True,
            TaskSnapshot.is_deleted == False,
            TaskSnapshot.has_issue == True,
        )
    )
    if selected_shop_id is not None:
        critical_issues_q = critical_issues_q.where(TaskItem.shop_id == selected_shop_id)
    critical_issues = (await db.execute(critical_issues_q)).scalar() or 0

    # ── KPI 5: Total MH (latest snapshots) ─────────────────────────
    total_mh_q = (
        select(func.coalesce(func.sum(TaskSnapshot.mh_incurred_hours), 0))
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .where(
            TaskItem.is_active == True,
            TaskSnapshot.is_deleted == False,
        )
    )
    if selected_shop_id is not None:
        total_mh_q = total_mh_q.where(TaskItem.shop_id == selected_shop_id)
    total_mh_raw = (await db.execute(total_mh_q)).scalar() or 0
    total_mh = round(float(total_mh_raw), 1)

    # ── Monthly OT Quota (per user) ────────────────────────────────
    if is_admin:
        if selected_shop_code:
            user_rows = (await db.execute(
                select(User).where(
                    User.team == selected_shop_code,
                    User.is_active == True,  # noqa: E712
                )
            )).scalars().all()
        else:
            user_rows = (await db.execute(
                select(User).where(User.is_active == True)
            )).scalars().all()
    else:
        user_rows = (await db.execute(
            select(User).where(
                User.team == current_user.get("team"),
                User.is_active == True,
            )
        )).scalars().all()

    usage_list = []
    for u in user_rows:
        used = (await db.execute(
            select(func.coalesce(func.sum(OtRequest.requested_minutes), 0))
            .where(
                OtRequest.user_id == u.id,
                OtRequest.date >= first,
                OtRequest.date <= last,
                OtRequest.status.in_(COUNTABLE_STATUSES),
            )
        )).scalar() or 0
        used_hours = round(used / 60, 1)
        pct = round(used / MONTHLY_LIMIT_MINUTES * 100, 1) if MONTHLY_LIMIT_MINUTES else 0
        bar_color = "bg-st-red" if pct >= 100 else "bg-gold-500" if pct >= 70 else "bg-st-blue"
        usage_list.append({
            "name": u.name,
            "employee_no": u.employee_no,
            "used_hours": used_hours,
            "limit_hours": LIMIT_HOURS,
            "pct": pct,
            "bar_color": bar_color,
            "at_limit": pct >= 100,
        })
    usage_list.sort(key=lambda x: -x["pct"])

    # Team average
    if usage_list:
        avg_hours = round(sum(u["used_hours"] for u in usage_list) / len(usage_list), 1)
    else:
        avg_hours = 0

    # ── RFO Progress (per work package) ─────────────────────────────
    wp_q = (
        select(WorkPackage, Aircraft)
        .join(Aircraft, WorkPackage.aircraft_id == Aircraft.id)
        .where(WorkPackage.status == "ACTIVE")
        .order_by(WorkPackage.rfo_no)
    )
    if selected_shop_id is not None:
        wp_q = (
            wp_q.join(TaskItem, TaskItem.work_package_id == WorkPackage.id)
            .where(TaskItem.shop_id == selected_shop_id, TaskItem.is_active == True)
            .distinct()
        )
    wp_rows = (await db.execute(wp_q)).all()

    rfo_list = []
    grand_total_tasks = 0
    grand_total_mh = 0.0
    grand_planned_mh = 0.0
    for wp, ac in wp_rows:
        # Count tasks by status for this WP
        snap_q = (
            select(TaskSnapshot.status, func.count(), func.coalesce(func.sum(TaskSnapshot.mh_incurred_hours), 0))
            .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
            .where(
                TaskItem.work_package_id == wp.id,
                TaskItem.is_active == True,
                TaskSnapshot.is_deleted == False,
            )
            .group_by(TaskSnapshot.status)
        )
        if selected_shop_id is not None:
            snap_q = snap_q.where(TaskItem.shop_id == selected_shop_id)
        status_rows = (await db.execute(snap_q)).all()

        counts = {"COMPLETED": 0, "IN_PROGRESS": 0, "WAITING": 0, "NOT_STARTED": 0}
        wp_mh = 0.0
        wp_task_count = 0
        for status_val, cnt, mh in status_rows:
            counts[status_val] = cnt
            wp_mh += float(mh)
            wp_task_count += cnt

        # Planned MH sum
        planned_q = (
            select(func.coalesce(func.sum(TaskItem.planned_mh), 0))
            .where(TaskItem.work_package_id == wp.id, TaskItem.is_active == True)
        )
        if selected_shop_id is not None:
            planned_q = planned_q.where(TaskItem.shop_id == selected_shop_id)
        planned_raw = (await db.execute(planned_q)).scalar() or 0
        planned = float(planned_raw)

        total = wp_task_count or 1
        # Overdue count
        overdue_q = (
            select(func.count())
            .select_from(TaskSnapshot)
            .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
            .where(
                TaskItem.work_package_id == wp.id,
                TaskItem.is_active == True,
                TaskSnapshot.is_deleted == False,
                TaskSnapshot.status != "COMPLETED",
                TaskSnapshot.deadline_date < date.today(),
            )
        )
        if selected_shop_id is not None:
            overdue_q = overdue_q.where(TaskItem.shop_id == selected_shop_id)
        overdue = (await db.execute(overdue_q)).scalar() or 0

        if selected_shop_id is not None and wp_task_count == 0 and planned <= 0:
            continue

        done_pct = round(counts["COMPLETED"] / total * 100, 1)
        active_pct = round(counts["IN_PROGRESS"] / total * 100, 1)
        wait_pct = round(counts["WAITING"] / total * 100, 1)

        if done_pct + active_pct + wait_pct > 100:
            wait_pct = max(0, 100 - done_pct - active_pct)

        # Determine status label
        if overdue > 0:
            status_label = f"{overdue} overdue"
            status_class = "text-st-red"
        elif done_pct >= 80:
            status_label = "near done"
            status_class = "text-st-green"
        else:
            status_label = "on track"
            status_class = "text-st-blue"

        rfo_list.append({
            "rfo_no": wp.rfo_no,
            "ac_reg": ac.ac_reg,
            "description": wp.description,
            "done": counts["COMPLETED"],
            "active": counts["IN_PROGRESS"],
            "wait": counts["WAITING"],
            "open": counts["NOT_STARTED"],
            "done_pct": done_pct,
            "active_pct": active_pct,
            "wait_pct": wait_pct,
            "mh": round(wp_mh, 1),
            "planned_mh": round(planned, 1),
            "overdue": overdue,
            "status_label": status_label,
            "status_class": status_class,
        })

        grand_total_tasks += wp_task_count
        grand_total_mh += wp_mh
        grand_planned_mh += planned

    rfo_overall_pct = round(grand_total_mh / grand_planned_mh * 100) if grand_planned_mh else 0

    # ── OT Approval Pipeline ────────────────────────────────────────
    ot_month_q = select(OtRequest).where(
        OtRequest.date >= first, OtRequest.date <= last
    )
    if user_scope_ids is not None:
        if user_scope_ids:
            ot_month_q = ot_month_q.where(OtRequest.user_id.in_(user_scope_ids))
        else:
            ot_month_q = ot_month_q.where(OtRequest.id == -1)
    ot_rows = (await db.execute(ot_month_q)).scalars().all()

    pipeline_pending = sum(1 for r in ot_rows if r.status == "PENDING")
    pipeline_endorsed = sum(1 for r in ot_rows if r.status == "ENDORSED")
    pipeline_approved = sum(1 for r in ot_rows if r.status == "APPROVED")
    pipeline_rejected = sum(1 for r in ot_rows if r.status == "REJECTED")

    month_total_hours = round(
        sum(r.requested_minutes for r in ot_rows if r.status in COUNTABLE_STATUSES) / 60, 1
    )

    # Avg turnaround
    approved_ids = [r.id for r in ot_rows if r.status == "APPROVED"]
    avg_turnaround = 0
    if approved_ids:
        approvals = (await db.execute(
            select(OtApproval).where(
                OtApproval.ot_request_id.in_(approved_ids),
                OtApproval.stage == "APPROVE",
                OtApproval.action == "APPROVE",
            )
        )).scalars().all()
        approve_map = {a.ot_request_id: a.acted_at for a in approvals}
        turnarounds = []
        for r in ot_rows:
            if r.id in approve_map and r.created_at and approve_map[r.id]:
                diff = (approve_map[r.id] - r.created_at).total_seconds() / 3600
                turnarounds.append(diff)
        if turnarounds:
            avg_turnaround = round(sum(turnarounds) / len(turnarounds), 1)

    # ── Recent Activity (last 5 audit logs) ─────────────────────────
    recent_logs = (await db.execute(
        select(AuditLog, User)
        .outerjoin(User, AuditLog.actor_id == User.id)
        .order_by(AuditLog.created_at.desc())
        .limit(5)
    )).all()

    recent_activity = [
        {
            "entity_type": log.entity_type,
            "action": log.action,
            "actor_name": user.name if user else "System",
            "created_at": log.created_at.strftime("%b %d, %H:%M") if log.created_at else "",
        }
        for log, user in recent_logs
    ]

    return templates.TemplateResponse("dashboard.html", _ctx(
        request, current_user,
        month_label=month_label,
        dashboard_scope_label=dashboard_scope_label,
        shop_options=shop_options,
        selected_shop_id=selected_shop_id,
        shop_select_disabled=shop_select_disabled,
        active_tasks=active_tasks,
        ot_pending=ot_pending,
        ot_endorsed=ot_endorsed,
        critical_issues=critical_issues,
        total_mh=total_mh,
        usage_list=usage_list,
        limit_hours=LIMIT_HOURS,
        avg_hours=avg_hours,
        rfo_list=rfo_list,
        rfo_count=len(rfo_list),
        grand_total_tasks=grand_total_tasks,
        grand_total_mh=round(grand_total_mh, 1),
        grand_planned_mh=round(grand_planned_mh, 1),
        rfo_overall_pct=rfo_overall_pct,
        pipeline_pending=pipeline_pending,
        pipeline_endorsed=pipeline_endorsed,
        pipeline_approved=pipeline_approved,
        pipeline_rejected=pipeline_rejected,
        month_total_hours=month_total_hours,
        avg_turnaround=avg_turnaround,
        recent_activity=recent_activity,
    ))
