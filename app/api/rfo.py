"""RFO Metrics API (§8.7.2, §8.11) — Branch 11 commit 2."""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.ot import OtRequest
from app.models.reference import Aircraft, WorkPackage
from app.models.task import TaskItem, TaskSnapshot
from app.models.user import User
from app.schemas.common import APIError

router = APIRouter(prefix="/api/rfo", tags=["rfo"])


def _require_sup_plus(current_user: dict):
    roles = current_user.get("roles", [])
    if "SUPERVISOR" not in roles and "ADMIN" not in roles:
        raise APIError(403, "SUPERVISOR+ required", "FORBIDDEN")


async def _get_wp(db: AsyncSession, wp_id: int) -> WorkPackage:
    wp = (await db.execute(select(WorkPackage).where(WorkPackage.id == wp_id))).scalar_one_or_none()
    if not wp:
        raise APIError(404, "Work package not found", "NOT_FOUND")
    return wp


# ── GET /api/rfo/{id}/summary — §8.7.2 ────────────────────────────────

@router.get("/{work_package_id}/summary")
async def rfo_summary(
    work_package_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    wp = await _get_wp(db, work_package_id)

    ac = (await db.execute(select(Aircraft).where(Aircraft.id == wp.aircraft_id))).scalar_one_or_none()

    # Task stats — latest snapshot per task
    tasks = (await db.execute(
        select(TaskItem).where(TaskItem.work_package_id == wp.id, TaskItem.is_active == True)
    )).scalars().all()

    task_ids = [t.id for t in tasks]
    by_status: dict[str, int] = {"NOT_STARTED": 0, "IN_PROGRESS": 0, "WAITING": 0, "COMPLETED": 0}
    total_mh = 0.0

    if task_ids:
        for ti in tasks:
            snap = (await db.execute(
                select(TaskSnapshot)
                .where(TaskSnapshot.task_id == ti.id, TaskSnapshot.is_deleted == False)
                .order_by(TaskSnapshot.meeting_date.desc())
                .limit(1)
            )).scalar_one_or_none()
            if snap:
                by_status[snap.status] = by_status.get(snap.status, 0) + 1
                total_mh += float(snap.mh_incurred_hours or 0)

    # OT stats
    ot_rows = (await db.execute(
        select(OtRequest).where(OtRequest.work_package_id == wp.id)
    )).scalars().all()

    ot_by_status: dict[str, int] = {}
    total_approved_minutes = 0
    for r in ot_rows:
        ot_by_status[r.status] = ot_by_status.get(r.status, 0) + 1
        if r.status == "APPROVED":
            total_approved_minutes += r.requested_minutes

    return {
        "work_package_id": wp.id,
        "rfo_no": wp.rfo_no,
        "title": wp.title,
        "aircraft": {
            "ac_reg": ac.ac_reg if ac else None,
            "airline": ac.airline if ac else None,
        },
        "tasks": {
            "total": len(tasks),
            "by_status": by_status,
            "total_mh": round(total_mh, 1),
        },
        "ot": {
            "total_requests": len(ot_rows),
            "total_approved_minutes": total_approved_minutes,
            "by_status": ot_by_status,
        },
    }


# ── GET /api/rfo/{id}/metrics — §8.11.1 ───────────────────────────────

@router.get("/{work_package_id}/metrics")
async def rfo_metrics(
    work_package_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    wp = await _get_wp(db, work_package_id)

    tasks = (await db.execute(
        select(TaskItem).where(TaskItem.work_package_id == wp.id, TaskItem.is_active == True)
    )).scalars().all()
    task_ids = [t.id for t in tasks]

    planned_mh = sum(float(t.planned_mh or 0) for t in tasks)
    actual_mh = 0.0
    waiting_mh = 0.0
    completed = 0
    blocker_count = 0
    unassigned_count = sum(1 for t in tasks if not t.assigned_worker_id)

    # Cycle time data
    cycle_times: list[float] = []

    for ti in tasks:
        snap = (await db.execute(
            select(TaskSnapshot)
            .where(TaskSnapshot.task_id == ti.id, TaskSnapshot.is_deleted == False)
            .order_by(TaskSnapshot.meeting_date.desc())
            .limit(1)
        )).scalar_one_or_none()
        if not snap:
            continue

        mh = float(snap.mh_incurred_hours or 0)
        actual_mh += mh

        if snap.status == "WAITING":
            waiting_mh += mh
            if snap.has_issue:
                blocker_count += 1
        elif snap.status == "COMPLETED":
            completed += 1

    # Cycle time: first IN_PROGRESS → last COMPLETED (weeks)
    if task_ids:
        for ti in tasks:
            first_ip = (await db.execute(
                select(func.min(TaskSnapshot.meeting_date)).where(
                    TaskSnapshot.task_id == ti.id,
                    TaskSnapshot.status == "IN_PROGRESS",
                    TaskSnapshot.is_deleted == False,
                )
            )).scalar()
            last_comp = (await db.execute(
                select(func.max(TaskSnapshot.meeting_date)).where(
                    TaskSnapshot.task_id == ti.id,
                    TaskSnapshot.status == "COMPLETED",
                    TaskSnapshot.is_deleted == False,
                )
            )).scalar()
            if first_ip and last_comp and last_comp >= first_ip:
                weeks = (last_comp - first_ip).days / 7.0
                cycle_times.append(weeks)

    # OT hours for this RFO
    ot_minutes = (await db.execute(
        select(func.coalesce(func.sum(OtRequest.requested_minutes), 0)).where(
            OtRequest.work_package_id == wp.id,
            OtRequest.status.in_(("APPROVED", "ENDORSED", "PENDING")),
        )
    )).scalar() or 0
    ot_hours = round(ot_minutes / 60, 1)

    total_tasks = len(tasks)
    productive_ratio = round((actual_mh - waiting_mh) / actual_mh * 100, 1) if actual_mh else 0
    ot_ratio = round(ot_hours / actual_mh * 100, 1) if actual_mh else 0
    ftc = round(completed / total_tasks * 100, 1) if total_tasks else 0
    avg_cycle = round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else 0

    return {
        "work_package_id": wp.id,
        "total_tasks": total_tasks,
        "planned_mh": round(planned_mh, 1),
        "actual_mh": round(actual_mh, 1),
        "mh_variance": round(actual_mh - planned_mh, 1),
        "ot_hours": ot_hours,
        "ot_ratio_pct": ot_ratio,
        "productive_ratio_pct": productive_ratio,
        "first_time_completion_pct": ftc,
        "avg_cycle_time_weeks": avg_cycle,
        "blocker_count": blocker_count,
        "unassigned_count": unassigned_count,
    }


# ── GET /api/rfo/{id}/blockers — §8.11 ────────────────────────────────

@router.get("/{work_package_id}/blockers")
async def rfo_blockers(
    work_package_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    wp = await _get_wp(db, work_package_id)

    tasks = (await db.execute(
        select(TaskItem).where(TaskItem.work_package_id == wp.id, TaskItem.is_active == True)
    )).scalars().all()

    blockers = []
    now = datetime.now(timezone.utc)

    for ti in tasks:
        snap = (await db.execute(
            select(TaskSnapshot)
            .where(TaskSnapshot.task_id == ti.id, TaskSnapshot.is_deleted == False)
            .order_by(TaskSnapshot.meeting_date.desc())
            .limit(1)
        )).scalar_one_or_none()
        if not snap:
            continue
        if snap.status == "WAITING" and snap.has_issue:
            # Days since issue was first marked
            days = (now.date() - snap.meeting_date).days if snap.meeting_date else 0
            blockers.append({
                "task_id": ti.id,
                "snapshot_id": snap.id,
                "task_text": ti.task_text,
                "critical_issue": snap.critical_issue,
                "remarks": snap.remarks,
                "meeting_date": snap.meeting_date.isoformat() if snap.meeting_date else None,
                "days_blocked": max(0, days),
                "mh_incurred_hours": float(snap.mh_incurred_hours or 0),
            })

    blockers.sort(key=lambda x: -x["days_blocked"])

    return {
        "work_package_id": wp.id,
        "count": len(blockers),
        "blockers": blockers,
    }


# ── GET /api/rfo/{id}/worker-allocation — §8.11 ──────────────────────

@router.get("/{work_package_id}/worker-allocation")
async def rfo_worker_allocation(
    work_package_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    wp = await _get_wp(db, work_package_id)

    tasks = (await db.execute(
        select(TaskItem).where(TaskItem.work_package_id == wp.id, TaskItem.is_active == True)
    )).scalars().all()

    # Group by assigned_worker_id
    worker_data: dict[int | None, dict] = {}
    for ti in tasks:
        snap = (await db.execute(
            select(TaskSnapshot)
            .where(TaskSnapshot.task_id == ti.id, TaskSnapshot.is_deleted == False)
            .order_by(TaskSnapshot.meeting_date.desc())
            .limit(1)
        )).scalar_one_or_none()
        mh = float(snap.mh_incurred_hours or 0) if snap else 0

        wid = ti.assigned_worker_id
        if wid not in worker_data:
            worker_data[wid] = {"task_count": 0, "mh_total": 0.0}
        worker_data[wid]["task_count"] += 1
        worker_data[wid]["mh_total"] += mh

    # Resolve user names
    user_ids = [wid for wid in worker_data if wid is not None]
    users_map: dict[int, User] = {}
    if user_ids:
        us = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        users_map = {u.id: u for u in us}

    workers = []
    for wid, data in worker_data.items():
        u = users_map.get(wid) if wid else None
        workers.append({
            "worker_id": wid,
            "name": u.name if u else "Unassigned",
            "employee_no": u.employee_no if u else None,
            "task_count": data["task_count"],
            "mh_total": round(data["mh_total"], 1),
        })

    workers.sort(key=lambda x: -x["mh_total"])

    return {
        "work_package_id": wp.id,
        "workers": workers,
    }


# ── GET /api/rfo/{id}/burndown — §8.11 ────────────────────────────────

@router.get("/{work_package_id}/burndown")
async def rfo_burndown(
    work_package_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    wp = await _get_wp(db, work_package_id)

    tasks = (await db.execute(
        select(TaskItem).where(TaskItem.work_package_id == wp.id, TaskItem.is_active == True)
    )).scalars().all()
    task_ids = [t.id for t in tasks]
    planned_mh = sum(float(t.planned_mh or 0) for t in tasks)

    weeks: list[dict] = []
    if task_ids:
        # Get all snapshots grouped by meeting_date
        snaps = (await db.execute(
            select(TaskSnapshot).where(
                TaskSnapshot.task_id.in_(task_ids),
                TaskSnapshot.is_deleted == False,
            ).order_by(TaskSnapshot.meeting_date)
        )).scalars().all()

        by_week: dict[date, float] = {}
        for s in snaps:
            md = s.meeting_date
            by_week[md] = by_week.get(md, 0) + float(s.mh_incurred_hours or 0)

        for md in sorted(by_week.keys()):
            cumulative = by_week[md]
            weeks.append({
                "week": md.isoformat(),
                "cumulative_mh": round(cumulative, 1),
                "remaining_mh": round(max(0, planned_mh - cumulative), 1),
            })

    return {
        "work_package_id": wp.id,
        "rfo_no": wp.rfo_no,
        "planned_mh": round(planned_mh, 1),
        "weeks": weeks,
    }
