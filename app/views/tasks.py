"""Task Manager + Data Entry + Detail + Mobile SSR views."""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db, require_role
from app.models.audit import AuditLog
from app.models.ot import OtRequest
from app.models.reference import Aircraft, WorkPackage
from app.models.shop import Shop
from app.models.system_config import SystemConfig
from app.models.task import TaskItem, TaskSnapshot
from app.models.user import User
from app.models.user_shop_access import UserShopAccess
from app.views import templates

router = APIRouter(tags=["task-views"])


# Helpers

def _ctx(request, user, **kw):
    """Build base template context with sidebar page highlight."""
    return {
        "request": request,
        "current_user": {
            "user_id": user["user_id"],
            "display_name": user.get("display_name", ""),
            "roles": user.get("roles", []),
            "team": user.get("team"),
            "employee_no": user.get("employee_no", ""),
        },
        "active_tab": kw.pop("active_tab", "tasks"),
        **kw,
    }


def _format_relative(dt: datetime | None) -> str:
    """Return compact relative time string (e.g. 2h ago, 1d ago)."""
    if not dt:
        return "-"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    total_sec = max(0, int(delta.total_seconds()))
    if total_sec < 60:
        return "just now"
    minutes = total_sec // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = total_sec // 3600
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 7:
        return f"{days}d ago"
    return dt.strftime("%b %d")


async def _compute_mob_badges(db: AsyncSession, user: dict) -> dict:
    """Compute mobile tab badge counts."""
    # Tasks badge: NEW tasks (distributed_at NOT NULL, supervisor_updated_at IS NULL)
    new_q = (
        select(func.count())
        .select_from(TaskSnapshot)
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .where(
            TaskItem.is_active == True,
            TaskSnapshot.is_deleted == False,
            TaskItem.distributed_at.isnot(None),
            TaskSnapshot.supervisor_updated_at.is_(None),
        )
    )
    new_count = (await db.execute(new_q)).scalar() or 0

    # OT badge: pending approval count
    roles = user.get("roles", [])
    ot_count = 0
    if "SUPERVISOR" in roles and "ADMIN" not in roles:
        ot_q = select(func.count()).select_from(OtRequest).where(
            OtRequest.status == "PENDING",
            OtRequest.user_id != user["user_id"],
        )
        ot_count = (await db.execute(ot_q)).scalar() or 0
    elif "ADMIN" in roles:
        ot_q = select(func.count()).select_from(OtRequest).where(
            OtRequest.status == "ENDORSED",
            OtRequest.user_id != user["user_id"],
        )
        ot_count = (await db.execute(ot_q)).scalar() or 0

    return {"mob_badge_tasks": new_count, "mob_badge_ot": ot_count}


async def _get_config_map(db: AsyncSession) -> dict[str, str]:
    """Fetch all system_config rows into a dict."""
    rows = (await db.execute(select(SystemConfig))).scalars().all()
    return {r.key: r.value for r in rows}


async def _get_shops(db: AsyncSession) -> list:
    return (await db.execute(select(Shop).order_by(Shop.code))).scalars().all()


async def _get_supervisors(db: AsyncSession) -> list:
    """Get users who have SUPERVISOR role."""
    from app.models.user import Role, user_roles
    q = (
        select(User)
        .join(user_roles, User.id == user_roles.c.user_id)
        .join(Role, user_roles.c.role_id == Role.id)
        .where(Role.name == "SUPERVISOR", User.is_active == True)
        .order_by(User.name)
    )
    return (await db.execute(q)).scalars().all()


async def _get_workers(db: AsyncSession) -> list:
    """Get active users (all can be workers)."""
    return (
        await db.execute(select(User).where(User.is_active == True).order_by(User.name))
    ).scalars().all()


async def _get_aircraft(db: AsyncSession) -> list:
    return (
        await db.execute(select(Aircraft).where(Aircraft.status == "ACTIVE").order_by(Aircraft.ac_reg))
    ).scalars().all()


async def _get_work_packages(db: AsyncSession) -> list:
    return (
        await db.execute(select(WorkPackage).where(WorkPackage.status == "ACTIVE").order_by(WorkPackage.rfo_no))
    ).scalars().all()


def _snap_to_dict(snap: TaskSnapshot, task: TaskItem, ac_reg: str | None = None,
                  rfo_no: str | None = None, worker_name: str | None = None,
                  shop_code: str | None = None) -> dict:
    """Convert snapshot + task item to a flat dict for templates."""
    today = date.today()
    is_overdue = bool(snap.deadline_date and snap.deadline_date < today and snap.status != "COMPLETED")
    updated_src = snap.supervisor_updated_at or snap.last_updated_at
    return {
        "task_id": task.id,
        "snapshot_id": snap.id,
        "task_text": task.task_text,
        "ac_reg": ac_reg or "",
        "rfo_no": rfo_no or "",
        "shop_code": shop_code or "",
        "status": snap.status,
        "mh_incurred_hours": float(snap.mh_incurred_hours),
        "deadline_date": snap.deadline_date.isoformat() if snap.deadline_date else None,
        "is_overdue": is_overdue,
        "has_issue": snap.has_issue,
        "remarks": snap.remarks,
        "critical_issue": snap.critical_issue,
        "correction_reason": snap.correction_reason,
        "version": snap.version,
        "assigned_worker": worker_name or "",
        "assigned_worker_id": task.assigned_worker_id,
        "meeting_date": snap.meeting_date.isoformat() if snap.meeting_date else None,
        "supervisor_updated_at": snap.supervisor_updated_at,
        "distributed_at": task.distributed_at,
        "deadline_display": snap.deadline_date.strftime("%b %d") if snap.deadline_date else "-",
        "distributed_display": task.distributed_at.strftime("%b %d, %Y") if task.distributed_at else "-",
        "updated_display": _format_relative(updated_src),
        "updated_stamp": updated_src.strftime("%b %d %H:%M") if updated_src else "",
    }


# GET /tasks - Task Manager

@router.get("/tasks")
async def task_manager_page(
    request: Request,
    meeting_date: str | None = Query(None),
    shop_id: int | None = Query(None),
    airline: str | None = Query(None),
    supervisor_id: int | None = Query(None),
    status: str | None = Query(None),
    rfo: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    current_user: dict = Depends(require_role("SUPERVISOR", "ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    roles = current_user.get("roles", [])
    is_admin = "ADMIN" in roles

    allowed_shop_ids: set[int] | None = None
    manageable_shop_ids: set[int] = set()
    if not is_admin:
        access_rows = (await db.execute(
            select(UserShopAccess.shop_id, UserShopAccess.access)
            .where(UserShopAccess.user_id == current_user["user_id"])
        )).all()
        allowed_shop_ids = {row.shop_id for row in access_rows}
        manageable_shop_ids = {row.shop_id for row in access_rows if row.access == "MANAGE"}

    can_init_week = is_admin or bool(manageable_shop_ids)

    shops = await _get_shops(db)
    if allowed_shop_ids is not None:
        shops = [s for s in shops if s.id in allowed_shop_ids]
    supervisors = await _get_supervisors(db)
    aircraft_list = await _get_aircraft(db)
    work_packages = await _get_work_packages(db)

    def _apply_shop_scope(base_q):
        if allowed_shop_ids is None:
            return base_q
        if not allowed_shop_ids:
            return base_q.where(TaskItem.id == -1)
        return base_q.where(TaskItem.shop_id.in_(allowed_shop_ids))

    # Build snapshot query with eager-loaded relationships
    q = _apply_shop_scope(
        (
        select(TaskSnapshot, TaskItem, Aircraft, WorkPackage, User, Shop)
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .outerjoin(Aircraft, TaskItem.aircraft_id == Aircraft.id)
        .outerjoin(WorkPackage, TaskItem.work_package_id == WorkPackage.id)
        .outerjoin(User, TaskItem.assigned_worker_id == User.id)
        .outerjoin(Shop, TaskItem.shop_id == Shop.id)
        .where(TaskSnapshot.is_deleted == False, TaskItem.is_active == True)
        )
    )

    # Filters
    if meeting_date:
        q = q.where(TaskSnapshot.meeting_date == meeting_date)
    if shop_id:
        q = q.where(TaskItem.shop_id == shop_id)
    if airline:
        if airline == "SQ":
            q = q.where(Aircraft.airline == "SQ")
        elif airline == "3RD":
            q = q.where(Aircraft.airline != "SQ")
    if supervisor_id:
        q = q.where(TaskItem.assigned_supervisor_id == supervisor_id)
    if status:
        q = q.where(TaskSnapshot.status == status)
    if rfo:
        q = q.where(WorkPackage.rfo_no == rfo)
    if search:
        q = q.where(TaskItem.task_text.ilike(f"%{search}%"))

    # Shared filter conditions (applied to count + stats + main query)
    def _apply_filters(base_q):
        base_q = _apply_shop_scope(base_q)
        if meeting_date:
            base_q = base_q.where(TaskSnapshot.meeting_date == meeting_date)
        if shop_id:
            base_q = base_q.where(TaskItem.shop_id == shop_id)
        if airline:
            if airline == "SQ":
                base_q = base_q.where(Aircraft.airline == "SQ")
            elif airline == "3RD":
                base_q = base_q.where(Aircraft.airline != "SQ")
        if supervisor_id:
            base_q = base_q.where(TaskItem.assigned_supervisor_id == supervisor_id)
        if status:
            base_q = base_q.where(TaskSnapshot.status == status)
        if rfo:
            base_q = base_q.where(WorkPackage.rfo_no == rfo)
        if search:
            base_q = base_q.where(TaskItem.task_text.ilike(f"%{search}%"))
        return base_q

    # Base joins for count/stats queries
    def _base_count():
        return (
            select(func.count())
            .select_from(TaskSnapshot)
            .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
            .outerjoin(Aircraft, TaskItem.aircraft_id == Aircraft.id)
            .outerjoin(WorkPackage, TaskItem.work_package_id == WorkPackage.id)
            .where(TaskSnapshot.is_deleted == False, TaskItem.is_active == True)
        )

    total = (await db.execute(_apply_filters(_base_count()))).scalar() or 0
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Summary stats (global for filtered set, not just current page)
    stats_q = (
        select(
            TaskSnapshot.status,
            func.count(),
            func.coalesce(func.sum(TaskSnapshot.mh_incurred_hours), 0),
        )
        .select_from(TaskSnapshot)
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .outerjoin(Aircraft, TaskItem.aircraft_id == Aircraft.id)
        .outerjoin(WorkPackage, TaskItem.work_package_id == WorkPackage.id)
        .where(TaskSnapshot.is_deleted == False, TaskItem.is_active == True)
        .group_by(TaskSnapshot.status)
    )
    stats_rows = (await db.execute(_apply_filters(stats_q))).all()
    status_counts = {"NOT_STARTED": 0, "IN_PROGRESS": 0, "WAITING": 0, "COMPLETED": 0}
    summary_mh = 0.0
    for s, cnt, mh in stats_rows:
        status_counts[s] = cnt
        summary_mh += float(mh)

    overdue_count = (await db.execute(
        _apply_filters(_base_count()).where(
            TaskSnapshot.status != "COMPLETED",
            TaskSnapshot.deadline_date < date.today(),
        )
    )).scalar() or 0

    unassigned_count = (await db.execute(
        _apply_filters(_base_count()).where(
            TaskItem.assigned_supervisor_id.is_(None),
        )
    )).scalar() or 0

    # Paginated results
    offset = (page - 1) * per_page
    rows = (
        await db.execute(
            q.order_by(TaskSnapshot.meeting_date.desc(), TaskItem.id)
            .offset(offset).limit(per_page)
        )
    ).all()

    # Batch-fetch supervisor names
    supervisor_ids = {task.assigned_supervisor_id for _, task, _, _, _, _ in rows if task.assigned_supervisor_id}
    sup_map: dict[int, str] = {}
    if supervisor_ids:
        sup_rows = (await db.execute(select(User).where(User.id.in_(supervisor_ids)))).scalars().all()
        sup_map = {u.id: u.name for u in sup_rows}

    snapshots = []
    rfo_groups: dict[str, dict] = {}
    for snap, task, ac, wp, worker, shop in rows:
        d = _snap_to_dict(
            snap, task,
            ac_reg=ac.ac_reg if ac else None,
            rfo_no=wp.rfo_no if wp else None,
            worker_name=worker.name if worker else None,
            shop_code=shop.code if shop else None,
        )
        d["assigned_supervisor"] = sup_map.get(task.assigned_supervisor_id)
        d["wp_title"] = wp.title if wp else None
        snapshots.append(d)

        rfo_key = wp.rfo_no if wp and wp.rfo_no else "No RFO"
        if rfo_key not in rfo_groups:
            rfo_groups[rfo_key] = {
                "rfo_no": rfo_key,
                "ac_reg": ac.ac_reg if ac else "",
                "title": wp.title if wp else "",
                "tasks": [],
            }
        rfo_groups[rfo_key]["tasks"].append(d)

    # Compute simple MH delta (current - previous snapshot for the same task in this result set)
    prev_mh_by_task: dict[int, float] = {}
    for d in sorted(snapshots, key=lambda x: (x["task_id"], x["meeting_date"] or "")):
        prev = prev_mh_by_task.get(d["task_id"])
        if prev is None:
            d["mh_delta"] = None
        else:
            delta = round(float(d["mh_incurred_hours"]) - float(prev), 1)
            d["mh_delta"] = delta if delta > 0 else None
        prev_mh_by_task[d["task_id"]] = float(d["mh_incurred_hours"])

    for d in snapshots:
        d["mh_delta_display"] = f"+{d['mh_delta']:.1f}" if d.get("mh_delta") else "-"
    # Enrich RFO groups with computed metadata
    rfo_list = []
    for rfo_key, grp in rfo_groups.items():
        tasks_in_grp = grp["tasks"]
        grp_mh = sum(t["mh_incurred_hours"] for t in tasks_in_grp)
        assigned = sum(1 for t in tasks_in_grp if t.get("assigned_supervisor"))
        updated = sum(1 for t in tasks_in_grp if t.get("supervisor_updated_at"))
        grp_status = {"NOT_STARTED": 0, "IN_PROGRESS": 0, "WAITING": 0, "COMPLETED": 0}
        for t in tasks_in_grp:
            grp_status[t["status"]] = grp_status.get(t["status"], 0) + 1
        n = len(tasks_in_grp) or 1
        rfo_list.append({
            **grp,
            "total_mh": round(grp_mh, 1),
            "assigned_count": assigned,
            "updated_count": updated,
            "status_counts": grp_status,
            "pct_not_started": round(grp_status["NOT_STARTED"] / n * 100),
            "pct_in_progress": round(grp_status["IN_PROGRESS"] / n * 100),
            "pct_waiting": round(grp_status["WAITING"] / n * 100),
            "pct_completed": round(grp_status["COMPLETED"] / n * 100),
        })

    # All RFOs for filter dropdown
    all_rfos = (await db.execute(
        select(WorkPackage.rfo_no, WorkPackage.title)
        .where(WorkPackage.status == "ACTIVE", WorkPackage.rfo_no.isnot(None))
        .order_by(WorkPackage.rfo_no)
    )).all()

    return templates.TemplateResponse("tasks/manager.html", _ctx(
        request, current_user,
        page="tasks",
        can_manage_tasks=is_admin,
        can_init_week=can_init_week,
        meeting_date=meeting_date or "",
        shop_id=shop_id,
        airline_filter=airline or "",
        supervisor_id=supervisor_id,
        status_filter=status or "",
        rfo_filter=rfo or "",
        search_filter=search or "",
        shops=shops,
        supervisors=supervisors,
        aircraft_list=aircraft_list,
        work_packages=work_packages,
        all_rfos=all_rfos,
        snapshots=snapshots,
        rfo_list=rfo_list,
        status_counts=status_counts,
        summary_mh=round(summary_mh, 1),
        overdue_count=overdue_count,
        unassigned_count=unassigned_count,
        total=total,
        per_page=per_page,
        page_num=page,
        total_pages=total_pages,
    ))


# GET /tasks/entry - Data Entry


# -- GET /tasks/partials/detail (Task Manager right-panel detail) -----------------

@router.get("/tasks/partials/detail")
async def task_manager_detail_partial(
    request: Request,
    snapshot_id: int = Query(..., ge=1),
    current_user: dict = Depends(require_role("SUPERVISOR", "ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    roles = current_user.get("roles", [])
    is_admin = "ADMIN" in roles

    allowed_shop_ids: set[int] | None = None
    if not is_admin:
        access_rows = (await db.execute(
            select(UserShopAccess.shop_id)
            .where(UserShopAccess.user_id == current_user["user_id"])
        )).all()
        allowed_shop_ids = {row.shop_id for row in access_rows}

    q = (
        select(TaskSnapshot, TaskItem, Aircraft, WorkPackage, Shop)
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .outerjoin(Aircraft, TaskItem.aircraft_id == Aircraft.id)
        .outerjoin(WorkPackage, TaskItem.work_package_id == WorkPackage.id)
        .outerjoin(Shop, TaskItem.shop_id == Shop.id)
        .where(
            TaskSnapshot.id == snapshot_id,
            TaskSnapshot.is_deleted == False,
            TaskItem.is_active == True,
        )
    )
    if allowed_shop_ids is not None:
        if not allowed_shop_ids:
            q = q.where(TaskItem.id == -1)
        else:
            q = q.where(TaskItem.shop_id.in_(allowed_shop_ids))

    row = (await db.execute(q)).first()
    if not row:
        return templates.TemplateResponse(
            "tasks/partials/_manager_detail_body.html",
            {"request": request, "detail": None},
        )

    snap, task, aircraft, wp, shop = row

    supervisor_name = None
    worker_name = None
    if task.assigned_supervisor_id:
        sup = (await db.execute(select(User).where(User.id == task.assigned_supervisor_id))).scalar_one_or_none()
        supervisor_name = sup.name if sup else None
    if task.assigned_worker_id:
        worker = (await db.execute(select(User).where(User.id == task.assigned_worker_id))).scalar_one_or_none()
        worker_name = worker.name if worker else None

    all_snapshots = (await db.execute(
        select(TaskSnapshot)
        .where(
            TaskSnapshot.task_id == task.id,
            TaskSnapshot.is_deleted == False,
        )
        .order_by(TaskSnapshot.meeting_date.asc(), TaskSnapshot.id.asc())
    )).scalars().all()

    prev_mh = None
    for idx, hist in enumerate(all_snapshots):
        if hist.id == snap.id and idx > 0:
            prev_mh = all_snapshots[idx - 1].mh_incurred_hours
            break
    mh_delta = None
    if prev_mh is not None:
        delta = round(float(snap.mh_incurred_hours) - float(prev_mh), 1)
        if delta > 0:
            mh_delta = f"+{delta:.1f}"

    updated_src = snap.supervisor_updated_at or snap.last_updated_at
    assigned_name = worker_name or supervisor_name or "Unassigned"
    initials = "".join([p[0] for p in assigned_name.split()][:2]).upper() if assigned_name != "Unassigned" else "NA"

    status_map = {
        "NOT_STARTED": ("Not Started", "#eef1f6", "#7c8694"),
        "IN_PROGRESS": ("In Progress", "#fdf5e0", "#a07d12"),
        "WAITING": ("Waiting", "#fdf0f0", "#b93a3a"),
        "COMPLETED": ("Completed", "#ecf7f0", "#2d7a4f"),
    }
    status_label, status_bg, status_color = status_map.get(
        snap.status, ("Not Started", "#eef1f6", "#7c8694")
    )

    history_window = all_snapshots[-4:] if all_snapshots else [snap]
    max_mh = max((float(h.mh_incurred_hours or 0) for h in history_window), default=0.0)
    scale = max_mh if max_mh > 0 else 1.0
    mh_history = []
    for idx, h in enumerate(history_window, start=1):
        mh_value = float(h.mh_incurred_hours or 0)
        pct = mh_value / scale if scale else 0
        bar_height = int(round(10 + (pct * 40))) if mh_value > 0 else 6
        mh_history.append({
            "label": f"W{idx}",
            "bar_height": bar_height,
            "is_current": h.id == snap.id,
        })

    snap_ids = [s.id for s in all_snapshots]
    audit_filters = [
        (AuditLog.entity_type == "task_item") & (AuditLog.entity_id == task.id),
    ]
    if snap_ids:
        audit_filters.append(
            (AuditLog.entity_type == "task_snapshot") & (AuditLog.entity_id.in_(snap_ids))
        )
    audit_rows = (await db.execute(
        select(AuditLog, User)
        .outerjoin(User, AuditLog.actor_id == User.id)
        .where(or_(*audit_filters))
        .order_by(AuditLog.created_at.desc())
        .limit(8)
    )).all()
    audit_logs = []
    for log, actor in audit_rows:
        action_upper = (log.action or "").upper()
        if "DELETE" in action_upper:
            dot_class = "bg-st-red"
        elif "CREATE" in action_upper:
            dot_class = "bg-navy-400"
        elif (
            "UPDATE" in action_upper
            or "PATCH" in action_upper
            or "ASSIGN" in action_upper
            or "IMPORT" in action_upper
        ):
            dot_class = "bg-st-yellow"
        else:
            dot_class = "bg-st-grey"
        audit_logs.append({
            "action_label": (log.action or "UPDATED").replace("_", " ").title(),
            "actor_name": actor.name if actor else None,
            "created_at": log.created_at.strftime("%b %d %H:%M") if log.created_at else "-",
            "dot_class": dot_class,
        })

    detail = {
        "task_id": task.id,
        "title": task.task_text,
        "ac_reg": aircraft.ac_reg if aircraft else "-",
        "rfo_no": wp.rfo_no if wp and wp.rfo_no else "-",
        "shop_code": shop.code if shop else "-",
        "status_label": status_label,
        "status_bg": status_bg,
        "status_color": status_color,
        "mh_incurred_hours": float(snap.mh_incurred_hours),
        "mh_delta": mh_delta,
        "deadline_display": snap.deadline_date.strftime("%b %d") if snap.deadline_date else "-",
        "assigned_name": assigned_name,
        "assigned_initials": initials,
        "remarks": snap.remarks,
        "critical_issue": snap.critical_issue,
        "distributed_display": task.distributed_at.strftime("%b %d, %Y") if task.distributed_at else "-",
        "updated_display": _format_relative(updated_src),
        "updated_stamp": updated_src.strftime("%b %d %H:%M") if updated_src else "-",
        "open_detail_url": f"/tasks/{task.id}",
        "mh_delta_display": f"{mh_delta} MH" if mh_delta else "-",
        "mh_history": mh_history,
        "audit_logs": audit_logs,
    }

    return templates.TemplateResponse(
        "tasks/partials/_manager_detail_body.html",
        {"request": request, "detail": detail},
    )

@router.get("/tasks/entry")
async def task_entry_page(
    request: Request,
    ac: str | None = Query(None),
    status: str | None = Query(None),
    quick: str | None = Query(None),
    edit: int | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    configs = await _get_config_map(db)
    threshold_hours = int(configs.get("needs_update_threshold_hours", "72"))
    threshold_dt = datetime.now(timezone.utc) - timedelta(hours=threshold_hours)

    workers = await _get_workers(db)

    # Build aircraft groups with task counts and badge indicators
    ac_q = (
        select(
            Aircraft.ac_reg,
            func.count(TaskSnapshot.id).label("task_count"),
        )
        .join(TaskItem, TaskItem.aircraft_id == Aircraft.id)
        .join(TaskSnapshot, TaskSnapshot.task_id == TaskItem.id)
        .where(TaskItem.is_active == True, TaskSnapshot.is_deleted == False)
    )
    if status:
        ac_q = ac_q.where(TaskSnapshot.status == status)

    ac_q = ac_q.group_by(Aircraft.ac_reg).order_by(Aircraft.ac_reg)
    ac_rows = (await db.execute(ac_q)).all()

    ac_groups = []
    for row in ac_rows:
        # Check for NEW badge: distributed_at IS NOT NULL AND supervisor_updated_at IS NULL
        new_q = (
            select(func.count())
            .select_from(TaskSnapshot)
            .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
            .join(Aircraft, TaskItem.aircraft_id == Aircraft.id)
            .where(
                Aircraft.ac_reg == row.ac_reg,
                TaskItem.is_active == True,
                TaskSnapshot.is_deleted == False,
                TaskItem.distributed_at.isnot(None),
                TaskSnapshot.supervisor_updated_at.is_(None),
            )
        )
        new_count = (await db.execute(new_q)).scalar() or 0

        # Check for NEEDS UPDATE badge: supervisor_updated_at older than threshold
        needs_q = (
            select(func.count())
            .select_from(TaskSnapshot)
            .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
            .join(Aircraft, TaskItem.aircraft_id == Aircraft.id)
            .where(
                Aircraft.ac_reg == row.ac_reg,
                TaskItem.is_active == True,
                TaskSnapshot.is_deleted == False,
                TaskSnapshot.supervisor_updated_at.isnot(None),
                TaskSnapshot.supervisor_updated_at < threshold_dt,
            )
        )
        needs_count = (await db.execute(needs_q)).scalar() or 0

        ac_groups.append({
            "ac_reg": row.ac_reg,
            "task_count": row.task_count,
            "has_new": new_count > 0,
            "needs_update": needs_count > 0,
        })

    # If quick filter, narrow down
    if quick == "issue":
        ac_regs_with_issue = set()
        issue_q = (
            select(Aircraft.ac_reg)
            .join(TaskItem, TaskItem.aircraft_id == Aircraft.id)
            .join(TaskSnapshot, TaskSnapshot.task_id == TaskItem.id)
            .where(TaskItem.is_active == True, TaskSnapshot.is_deleted == False, TaskSnapshot.has_issue == True)
            .distinct()
        )
        ac_regs_with_issue = set(r[0] for r in (await db.execute(issue_q)).all())
        ac_groups = [g for g in ac_groups if g["ac_reg"] in ac_regs_with_issue]
    elif quick == "new":
        ac_groups = [g for g in ac_groups if g["has_new"]]

    # Load tasks for selected aircraft
    tasks = []
    editing_task = None
    selected_ac_id = None
    selected_rfo = None
    selected_shop_id = None
    selected_meeting_date = None
    selected_work_package_id = None

    if ac:
        task_q = (
            select(TaskSnapshot, TaskItem, Aircraft, WorkPackage, User)
            .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
            .join(Aircraft, TaskItem.aircraft_id == Aircraft.id)
            .outerjoin(WorkPackage, TaskItem.work_package_id == WorkPackage.id)
            .outerjoin(User, TaskItem.assigned_worker_id == User.id)
            .where(
                Aircraft.ac_reg == ac,
                TaskItem.is_active == True,
                TaskSnapshot.is_deleted == False,
            )
        )
        if status:
            task_q = task_q.where(TaskSnapshot.status == status)

        task_q = task_q.order_by(TaskItem.id)
        task_rows = (await db.execute(task_q)).all()

        for snap, task, aircraft, wp, worker in task_rows:
            is_new = bool(task.distributed_at and snap.supervisor_updated_at is None)
            needs_update = bool(
                snap.supervisor_updated_at
                and snap.supervisor_updated_at < threshold_dt
            )

            d = {
                "task_id": task.id,
                "snapshot_id": snap.id,
                "task_text": task.task_text,
                "status": snap.status,
                "mh_incurred_hours": float(snap.mh_incurred_hours),
                "deadline_date": snap.deadline_date.isoformat() if snap.deadline_date else None,
                "has_issue": snap.has_issue,
                "remarks": snap.remarks,
                "critical_issue": snap.critical_issue,
                "version": snap.version,
                "assigned_worker": worker.name if worker else None,
                "assigned_worker_id": task.assigned_worker_id,
                "is_new": is_new,
                "needs_update": needs_update,
            }
            tasks.append(d)

            if selected_ac_id is None:
                selected_ac_id = aircraft.id
            if selected_shop_id is None:
                selected_shop_id = task.shop_id
            if selected_meeting_date is None and snap.meeting_date:
                selected_meeting_date = snap.meeting_date.isoformat()
            if selected_work_package_id is None:
                selected_work_package_id = task.work_package_id
            if wp and wp.rfo_no:
                selected_rfo = wp.rfo_no

            # Load editing task details
            if edit and task.id == edit:
                editing_task = d

    # Mobile badge counts
    badges = await _compute_mob_badges(db, current_user)

    return templates.TemplateResponse("tasks/entry.html", _ctx(
        request, current_user,
        page="tasks_entry",
        status_filter=status or "",
        quick_filter=quick or "",
        selected_ac=ac or "",
        selected_ac_id=selected_ac_id,
        selected_shop_id=selected_shop_id,
        selected_meeting_date=selected_meeting_date or "",
        selected_work_package_id=selected_work_package_id,
        selected_rfo=selected_rfo,
        ac_groups=ac_groups,
        tasks=tasks,
        editing_task=editing_task,
        editing_task_id=edit,
        workers=workers,
        shop_context=current_user.get("team", "All Shops"),
        **badges,
    ))


# GET /tasks/{task_id} - Task Detail

@router.get("/tasks/{task_id}")
async def task_detail_page(
    request: Request,
    task_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Load task item with relationships
    task_q = (
        select(TaskItem, Aircraft, WorkPackage, Shop)
        .outerjoin(Aircraft, TaskItem.aircraft_id == Aircraft.id)
        .outerjoin(WorkPackage, TaskItem.work_package_id == WorkPackage.id)
        .outerjoin(Shop, TaskItem.shop_id == Shop.id)
        .where(TaskItem.id == task_id)
    )
    result = (await db.execute(task_q)).first()

    if not result:
        return templates.TemplateResponse("tasks/detail.html", _ctx(
            request, current_user, page="tasks", task=None, snapshots=[], audit_logs=[],
        ), status_code=404)

    task_item, aircraft, wp, shop = result

    # Load supervisor and worker names
    supervisor = None
    worker = None
    if task_item.assigned_supervisor_id:
        supervisor = (await db.execute(
            select(User).where(User.id == task_item.assigned_supervisor_id)
        )).scalar_one_or_none()
    if task_item.assigned_worker_id:
        worker = (await db.execute(
            select(User).where(User.id == task_item.assigned_worker_id)
        )).scalar_one_or_none()

    # Latest snapshot for current status
    latest_snap = (await db.execute(
        select(TaskSnapshot)
        .where(TaskSnapshot.task_id == task_id, TaskSnapshot.is_deleted == False)
        .order_by(TaskSnapshot.meeting_date.desc())
        .limit(1)
    )).scalar_one_or_none()

    task_data = {
        "id": task_item.id,
        "task_text": task_item.task_text,
        "ac_reg": aircraft.ac_reg if aircraft else None,
        "rfo_no": wp.rfo_no if wp else None,
        "shop_code": shop.code if shop else None,
        "status": latest_snap.status if latest_snap else "NOT_STARTED",
        "has_issue": latest_snap.has_issue if latest_snap else False,
        "supervisor_name": supervisor.name if supervisor else None,
        "worker_name": worker.name if worker else None,
        "distributed_at": (
            task_item.distributed_at.strftime("%Y-%m-%d %H:%M") if task_item.distributed_at else None
        ),
        "planned_mh": float(task_item.planned_mh) if task_item.planned_mh else None,
        "created_at": task_item.created_at.strftime("%Y-%m-%d %H:%M") if task_item.created_at else None,
    }

    # Load all snapshots for timeline
    snap_rows = (await db.execute(
        select(TaskSnapshot)
        .where(TaskSnapshot.task_id == task_id, TaskSnapshot.is_deleted == False)
        .order_by(TaskSnapshot.meeting_date.desc())
    )).scalars().all()

    snapshots = [
        {
            "meeting_date": s.meeting_date.isoformat() if s.meeting_date else None,
            "status": s.status,
            "mh_incurred_hours": float(s.mh_incurred_hours),
            "deadline_date": s.deadline_date.isoformat() if s.deadline_date else None,
            "has_issue": s.has_issue,
            "remarks": s.remarks,
            "critical_issue": s.critical_issue,
            "correction_reason": s.correction_reason,
            "version": s.version,
        }
        for s in snap_rows
    ]

    # Load audit logs (recent 10)
    # task_item audits use entity_id=task.id; task_snapshot audits use entity_id=snap.id
    snap_ids = [s.id for s in snap_rows]
    audit_q = (
        select(AuditLog, User)
        .outerjoin(User, AuditLog.actor_id == User.id)
        .where(or_(
            (AuditLog.entity_type == "task_item") & (AuditLog.entity_id == task_id),
            (AuditLog.entity_type == "task_snapshot") & (AuditLog.entity_id.in_(snap_ids)) if snap_ids else False,
        ))
        .order_by(AuditLog.created_at.desc())
        .limit(10)
    )
    audit_rows = (await db.execute(audit_q)).all()
    audit_logs = [
        {
            "action": log.action,
            "actor_name": user.name if user else None,
            "created_at": log.created_at.strftime("%Y-%m-%d %H:%M") if log.created_at else None,
        }
        for log, user in audit_rows
    ]

    return templates.TemplateResponse("tasks/detail.html", _ctx(
        request, current_user,
        page="tasks",
        task=task_data,
        snapshots=snapshots,
        audit_logs=audit_logs,
    ))


# GET /admin/settings - Settings

@router.get("/admin/settings")
async def settings_page(
    request: Request,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    configs = await _get_config_map(db)
    shops = await _get_shops(db)

    return templates.TemplateResponse("admin/settings.html", _ctx(
        request, current_user,
        page="settings",
        configs=configs,
        shops=shops,
    ))


# Mobile HTMX: M1 Aircraft List

@router.get("/tasks/entry/mobile/m1")
async def mobile_m1(
    request: Request,
    status: str | None = Query(None),
    quick: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    configs = await _get_config_map(db)
    threshold_hours = int(configs.get("needs_update_threshold_hours", "72"))
    threshold_dt = datetime.now(timezone.utc) - timedelta(hours=threshold_hours)

    ac_q = (
        select(Aircraft.ac_reg, func.count(TaskSnapshot.id).label("task_count"))
        .join(TaskItem, TaskItem.aircraft_id == Aircraft.id)
        .join(TaskSnapshot, TaskSnapshot.task_id == TaskItem.id)
        .where(TaskItem.is_active == True, TaskSnapshot.is_deleted == False)
    )
    if status:
        ac_q = ac_q.where(TaskSnapshot.status == status)
    ac_q = ac_q.group_by(Aircraft.ac_reg).order_by(Aircraft.ac_reg)
    ac_rows = (await db.execute(ac_q)).all()

    ac_groups = []
    total_new = 0
    for row in ac_rows:
        new_count = (await db.execute(
            select(func.count()).select_from(TaskSnapshot)
            .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
            .join(Aircraft, TaskItem.aircraft_id == Aircraft.id)
            .where(Aircraft.ac_reg == row.ac_reg, TaskItem.is_active == True,
                   TaskSnapshot.is_deleted == False,
                   TaskItem.distributed_at.isnot(None),
                   TaskSnapshot.supervisor_updated_at.is_(None))
        )).scalar() or 0
        needs_count = (await db.execute(
            select(func.count()).select_from(TaskSnapshot)
            .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
            .join(Aircraft, TaskItem.aircraft_id == Aircraft.id)
            .where(Aircraft.ac_reg == row.ac_reg, TaskItem.is_active == True,
                   TaskSnapshot.is_deleted == False,
                   TaskSnapshot.supervisor_updated_at.isnot(None),
                   TaskSnapshot.supervisor_updated_at < threshold_dt)
        )).scalar() or 0
        total_new += new_count
        ac_groups.append({
            "ac_reg": row.ac_reg, "task_count": row.task_count,
            "has_new": new_count > 0, "new_count": new_count,
            "needs_update_count": needs_count,
        })

    if quick == "issue":
        issue_regs = set(r[0] for r in (await db.execute(
            select(Aircraft.ac_reg).join(TaskItem, TaskItem.aircraft_id == Aircraft.id)
            .join(TaskSnapshot, TaskSnapshot.task_id == TaskItem.id)
            .where(TaskItem.is_active == True, TaskSnapshot.is_deleted == False, TaskSnapshot.has_issue == True)
            .distinct()
        )).all())
        ac_groups = [g for g in ac_groups if g["ac_reg"] in issue_regs]
    elif quick == "new":
        ac_groups = [g for g in ac_groups if g["has_new"]]

    return templates.TemplateResponse("tasks/partials/_m1_aircraft.html", {
        "request": request,
        "ac_groups": ac_groups,
        "status_filter": status or "",
        "quick_filter": quick or "",
        "shop_context": current_user.get("team", "All Shops"),
        "total_new": total_new,
    })


# Mobile HTMX: M2 Task List

@router.get("/tasks/entry/mobile/m2")
async def mobile_m2(
    request: Request,
    ac: str = Query(...),
    status: str | None = Query(None),
    quick: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    configs = await _get_config_map(db)
    threshold_hours = int(configs.get("needs_update_threshold_hours", "72"))
    threshold_dt = datetime.now(timezone.utc) - timedelta(hours=threshold_hours)
    workers = await _get_workers(db)

    task_q = (
        select(TaskSnapshot, TaskItem, Aircraft, WorkPackage, User)
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .join(Aircraft, TaskItem.aircraft_id == Aircraft.id)
        .outerjoin(WorkPackage, TaskItem.work_package_id == WorkPackage.id)
        .outerjoin(User, TaskItem.assigned_worker_id == User.id)
        .where(Aircraft.ac_reg == ac, TaskItem.is_active == True, TaskSnapshot.is_deleted == False)
    )
    if status:
        task_q = task_q.where(TaskSnapshot.status == status)
    task_q = task_q.order_by(TaskItem.id)
    task_rows = (await db.execute(task_q)).all()

    tasks = []
    selected_ac_id = None
    selected_shop_id = None
    selected_work_package_id = None
    selected_meeting_date = None
    rfo_no = None
    total_mh = 0.0
    for snap, task, aircraft, wp, worker in task_rows:
        is_new = bool(task.distributed_at and snap.supervisor_updated_at is None)
        needs_update = bool(snap.supervisor_updated_at and snap.supervisor_updated_at < threshold_dt)

        if selected_ac_id is None:
            selected_ac_id = aircraft.id
        if selected_shop_id is None:
            selected_shop_id = task.shop_id
        if selected_work_package_id is None:
            selected_work_package_id = task.work_package_id
        if selected_meeting_date is None:
            selected_meeting_date = snap.meeting_date
        if wp and wp.rfo_no:
            rfo_no = wp.rfo_no

        if quick == "issue" and not snap.has_issue:
            continue
        if quick == "new" and not is_new:
            continue

        last_upd = ""
        if snap.supervisor_updated_at:
            delta = datetime.now(timezone.utc) - snap.supervisor_updated_at.replace(tzinfo=timezone.utc) if snap.supervisor_updated_at.tzinfo is None else datetime.now(timezone.utc) - snap.supervisor_updated_at
            hrs = int(delta.total_seconds() / 3600)
            last_upd = f"{hrs}h ago" if hrs < 24 else f"{hrs // 24}d ago"
        tasks.append({
            "task_id": task.id, "snapshot_id": snap.id, "task_text": task.task_text,
            "status": snap.status, "mh_incurred_hours": float(snap.mh_incurred_hours),
            "deadline_date": snap.deadline_date.isoformat() if snap.deadline_date else None,
            "has_issue": snap.has_issue, "remarks": snap.remarks,
            "critical_issue": snap.critical_issue, "version": snap.version,
            "assigned_worker": worker.name if worker else None,
            "assigned_worker_id": task.assigned_worker_id,
            "is_new": is_new, "needs_update": needs_update,
            "last_updated_display": last_upd,
        })
        total_mh += float(snap.mh_incurred_hours)

    return templates.TemplateResponse("tasks/partials/_m2_tasks.html", {
        "request": request,
        "ac_reg": ac,
        "rfo_no": rfo_no,
        "tasks_summary": f"{len(tasks)} tasks - {total_mh:.1f} MH",
        "tasks": tasks,
        "status_filter": status or "",
        "quick_filter": quick or "",
        "selected_ac_id": selected_ac_id,
        "workers": workers,
        "meeting_date": selected_meeting_date.isoformat() if selected_meeting_date else date.today().isoformat(),
        "shop_id": selected_shop_id or "",
        "work_package_id": selected_work_package_id or "",
    })


# Mobile HTMX: M3 Quick Update

@router.get("/tasks/entry/mobile/m3")
async def mobile_m3(
    request: Request,
    snapshot_id: int = Query(...),
    ac: str = Query(""),
    status: str | None = Query(None),
    quick: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workers = await _get_workers(db)

    snap = (await db.execute(select(TaskSnapshot).where(TaskSnapshot.id == snapshot_id))).scalar_one_or_none()
    if not snap:
        return templates.TemplateResponse("tasks/partials/_m2_tasks.html", {
            "request": request, "ac_reg": ac, "tasks": [],
            "status_filter": status or "",
            "quick_filter": quick or "",
            "workers": workers,
        })

    task_item = (await db.execute(select(TaskItem).where(TaskItem.id == snap.task_id))).scalar_one()
    worker = None
    if task_item.assigned_worker_id:
        worker = (await db.execute(select(User).where(User.id == task_item.assigned_worker_id))).scalar_one_or_none()

    # Determine next snapshot for Save & Next
    next_snap = (await db.execute(
        select(TaskSnapshot)
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .join(Aircraft, TaskItem.aircraft_id == Aircraft.id)
        .where(
            Aircraft.ac_reg == ac,
            TaskItem.is_active == True,
            TaskSnapshot.is_deleted == False,
            TaskSnapshot.id > snapshot_id,
        )
        .order_by(TaskSnapshot.id)
        .limit(1)
    )).scalar_one_or_none()

    last_sync = ""
    if snap.last_updated_at:
        last_sync = snap.last_updated_at.strftime("%H:%M")

    task_data = {
        "task_id": task_item.id, "snapshot_id": snap.id,
        "task_text": task_item.task_text, "status": snap.status,
        "mh_incurred_hours": float(snap.mh_incurred_hours),
        "deadline_date": snap.deadline_date.isoformat() if snap.deadline_date else None,
        "has_issue": snap.has_issue, "remarks": snap.remarks,
        "critical_issue": snap.critical_issue, "version": snap.version,
        "assigned_worker": worker.name if worker else None,
        "assigned_worker_id": task_item.assigned_worker_id,
        "last_sync": last_sync,
    }

    return templates.TemplateResponse("tasks/partials/_m3_update.html", {
        "request": request,
        "task": task_data,
        "workers": workers,
        "ac_reg": ac,
        "status_filter": status or "",
        "quick_filter": quick or "",
        "next_snapshot_id": next_snap.id if next_snap else None,
        "selected_ac_id": task_item.aircraft_id,
        "meeting_date": snap.meeting_date.isoformat() if snap.meeting_date else "",
        "shop_id": task_item.shop_id or "",
        "work_package_id": task_item.work_package_id or "",
    })


# Mobile HTMX: M4 Add Task Modal

@router.get("/tasks/entry/mobile/m4")
async def mobile_m4(
    request: Request,
    ac_id: int | None = Query(None),
    meeting_date: str | None = Query(None),
    shop_id: int | None = Query(None),
    work_package_id: int | None = Query(None),
    status: str | None = Query(None),
    quick: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workers = await _get_workers(db)
    return templates.TemplateResponse("tasks/partials/_m4_add_task.html", {
        "request": request,
        "workers": workers,
        "selected_ac_id": ac_id,
        "meeting_date": meeting_date or "",
        "shop_id": shop_id or "",
        "work_package_id": work_package_id or "",
        "status_filter": status or "",
        "quick_filter": quick or "",
    })


# Mobile HTMX: M5 Task Detail (read-only)

@router.get("/tasks/entry/mobile/m5")
async def mobile_m5(
    request: Request,
    snapshot_id: int = Query(...),
    ac: str = Query(""),
    status: str | None = Query(None),
    quick: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    snap = (await db.execute(select(TaskSnapshot).where(TaskSnapshot.id == snapshot_id))).scalar_one_or_none()
    if not snap:
        return templates.TemplateResponse("tasks/partials/_m5_detail.html", {
            "request": request, "task": {}, "snapshots": [], "audit_logs": [], "ac_reg": ac,
            "status_filter": status or "",
            "quick_filter": quick or "",
        })

    task_item = (await db.execute(
        select(TaskItem, Aircraft, WorkPackage, Shop)
        .outerjoin(Aircraft, TaskItem.aircraft_id == Aircraft.id)
        .outerjoin(WorkPackage, TaskItem.work_package_id == WorkPackage.id)
        .outerjoin(Shop, TaskItem.shop_id == Shop.id)
        .where(TaskItem.id == snap.task_id)
    )).first()
    ti, aircraft, wp, shop = task_item

    supervisor = worker = None
    if ti.assigned_supervisor_id:
        supervisor = (await db.execute(select(User).where(User.id == ti.assigned_supervisor_id))).scalar_one_or_none()
    if ti.assigned_worker_id:
        worker = (await db.execute(select(User).where(User.id == ti.assigned_worker_id))).scalar_one_or_none()

    task_data = {
        "task_id": ti.id, "snapshot_id": snap.id,
        "task_text": ti.task_text, "status": snap.status,
        "mh_incurred_hours": float(snap.mh_incurred_hours),
        "deadline_date": snap.deadline_date.isoformat() if snap.deadline_date else None,
        "has_issue": snap.has_issue, "remarks": snap.remarks,
        "critical_issue": snap.critical_issue,
        "ac_reg": aircraft.ac_reg if aircraft else None,
        "rfo_no": wp.rfo_no if wp else None,
        "shop_code": shop.code if shop else None,
        "supervisor_name": supervisor.name if supervisor else None,
        "worker_name": worker.name if worker else None,
        "distributed_at": ti.distributed_at.strftime("%b %d, %Y") if ti.distributed_at else None,
        "last_updated_at": snap.last_updated_at.strftime("%b %d, %H:%M") if snap.last_updated_at else None,
    }

    # All snapshots for timeline
    snap_rows = (await db.execute(
        select(TaskSnapshot).where(TaskSnapshot.task_id == snap.task_id, TaskSnapshot.is_deleted == False)
        .order_by(TaskSnapshot.meeting_date.desc())
    )).scalars().all()
    snapshots = [{
        "meeting_date": s.meeting_date.isoformat() if s.meeting_date else None,
        "status": s.status,
        "mh_incurred_hours": float(s.mh_incurred_hours),
        "remarks": s.remarks,
    } for s in snap_rows]

    # Audit logs (10)
    # task_item audits use entity_id=task.id; task_snapshot audits use entity_id=snap.id
    snap_ids_m5 = [s.id for s in snap_rows]
    from sqlalchemy import or_
    audit_rows = (await db.execute(
        select(AuditLog, User).outerjoin(User, AuditLog.actor_id == User.id)
        .where(or_(
            (AuditLog.entity_type == "task_item") & (AuditLog.entity_id == snap.task_id),
            (AuditLog.entity_type == "task_snapshot") & (AuditLog.entity_id.in_(snap_ids_m5)) if snap_ids_m5 else False,
        ))
        .order_by(AuditLog.created_at.desc()).limit(10)
    )).all()
    audit_logs = [{
        "action": log.action, "actor_name": user.name if user else None,
        "created_at": log.created_at.strftime("%b %d, %H:%M") if log.created_at else None,
    } for log, user in audit_rows]

    return templates.TemplateResponse("tasks/partials/_m5_detail.html", {
        "request": request,
        "task": task_data,
        "snapshots": snapshots,
        "audit_logs": audit_logs,
        "ac_reg": ac,
        "status_filter": status or "",
        "quick_filter": quick or "",
    })
