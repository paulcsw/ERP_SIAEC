"""Task API — §8.4 (Branch 07: list, create, update, init-week)."""
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.api.deps import get_current_user, get_db
from app.models.reference import Aircraft, WorkPackage
from app.models.shop import Shop
from app.models.task import TaskItem, TaskSnapshot
from app.models.user import User
from app.schemas.common import APIError, PaginatedResponse, pagination_params
from app.schemas.task import (
    BatchUpdateRequest,
    BatchUpdateResponse,
    InitWeekRequest,
    InitWeekResponse,
    SnapshotDeleteResponse,
    SnapshotListItem,
    SnapshotRestoreResponse,
    SnapshotUpdate,
    SnapshotUpdateResponse,
    SnapshotVersionRequest,
    TaskCreate,
    TaskCreateResponse,
    TaskDeactivateResponse,
)
from app.services.audit_service import write_audit
from app.services.shop_access_service import enforce_shop_access
from app.services.task_service import (
    check_mh_decrease,
    is_sq_airline,
    validate_status,
    init_week,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


# ── Helpers ───────────────────────────────────────────────────────────

def _user_name(users_map: dict, uid: int | None) -> str | None:
    if uid is None:
        return None
    u = users_map.get(uid)
    return u.name if u else None


async def _build_users_map(db: AsyncSession, ids: set[int]) -> dict[int, User]:
    if not ids:
        return {}
    rows = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {u.id: u for u in rows}


def _snap_to_dict(snap: TaskSnapshot) -> dict:
    return {
        "snapshot_id": snap.id,
        "version": snap.version,
        "status": snap.status,
        "mh_incurred_hours": str(snap.mh_incurred_hours),
        "deadline_date": str(snap.deadline_date) if snap.deadline_date else None,
        "remarks": snap.remarks,
        "critical_issue": snap.critical_issue,
        "has_issue": snap.has_issue,
        "correction_reason": snap.correction_reason,
        "last_updated_at": str(snap.last_updated_at),
        "last_updated_by": snap.last_updated_by,
        "supervisor_updated_at": str(snap.supervisor_updated_at) if snap.supervisor_updated_at else None,
        "is_deleted": snap.is_deleted,
        "meeting_date": str(snap.meeting_date),
        "task_id": snap.task_id,
    }


# ── §8.4.2 GET /api/tasks/snapshots ──────────────────────────────────

@router.get("/snapshots", response_model=PaginatedResponse[SnapshotListItem])
async def list_snapshots(
    meeting_date: date = Query(...),
    shop_id: int = Query(...),
    include_deleted: bool = Query(False),
    work_package_id: int | None = Query(None),
    assigned_supervisor_id: int | None = Query(None),
    aircraft_id: int | None = Query(None),
    status: str | None = Query(None),
    has_issue: bool | None = Query(None),
    airline_category: str | None = Query(None),
    paging: dict = Depends(pagination_params),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await enforce_shop_access(db, current_user, shop_id, "VIEW")

    if status is not None:
        status = validate_status(status)

    # Base query: join snapshots with task_items
    q = (
        select(TaskSnapshot, TaskItem)
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .where(
            TaskSnapshot.meeting_date == meeting_date,
            TaskItem.shop_id == shop_id,
        )
    )
    cq = (
        select(func.count())
        .select_from(TaskSnapshot)
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .where(
            TaskSnapshot.meeting_date == meeting_date,
            TaskItem.shop_id == shop_id,
        )
    )

    if not include_deleted:
        q = q.where(TaskSnapshot.is_deleted == False)
        cq = cq.where(TaskSnapshot.is_deleted == False)

    if work_package_id is not None:
        q = q.where(TaskItem.work_package_id == work_package_id)
        cq = cq.where(TaskItem.work_package_id == work_package_id)

    if assigned_supervisor_id is not None:
        q = q.where(TaskItem.assigned_supervisor_id == assigned_supervisor_id)
        cq = cq.where(TaskItem.assigned_supervisor_id == assigned_supervisor_id)

    if aircraft_id is not None:
        q = q.where(TaskItem.aircraft_id == aircraft_id)
        cq = cq.where(TaskItem.aircraft_id == aircraft_id)

    if status is not None:
        q = q.where(TaskSnapshot.status == status)
        cq = cq.where(TaskSnapshot.status == status)

    if has_issue is not None:
        q = q.where(TaskSnapshot.has_issue == has_issue)
        cq = cq.where(TaskSnapshot.has_issue == has_issue)

    total = (await db.execute(cq)).scalar() or 0

    rows = (
        await db.execute(
            q.order_by(TaskSnapshot.id)
            .offset(paging["offset"])
            .limit(paging["per_page"])
        )
    ).all()

    # Collect IDs for batch resolution
    user_ids: set[int] = set()
    ac_ids: set[int] = set()
    wp_ids: set[int] = set()
    shop_ids: set[int] = set()

    for snap, task in rows:
        user_ids.add(snap.last_updated_by)
        if task.assigned_supervisor_id:
            user_ids.add(task.assigned_supervisor_id)
        if task.assigned_worker_id:
            user_ids.add(task.assigned_worker_id)
        ac_ids.add(task.aircraft_id)
        if task.work_package_id:
            wp_ids.add(task.work_package_id)
        shop_ids.add(task.shop_id)

    users_map = await _build_users_map(db, user_ids)

    ac_map: dict[int, Aircraft] = {}
    if ac_ids:
        acs = (await db.execute(select(Aircraft).where(Aircraft.id.in_(ac_ids)))).scalars().all()
        ac_map = {a.id: a for a in acs}

    wp_map: dict[int, WorkPackage] = {}
    if wp_ids:
        wps = (await db.execute(select(WorkPackage).where(WorkPackage.id.in_(wp_ids)))).scalars().all()
        wp_map = {w.id: w for w in wps}

    shop_map: dict[int, Shop] = {}
    if shop_ids:
        shops = (await db.execute(select(Shop).where(Shop.id.in_(shop_ids)))).scalars().all()
        shop_map = {s.id: s for s in shops}

    items = []
    for snap, task in rows:
        ac = ac_map.get(task.aircraft_id)
        wp = wp_map.get(task.work_package_id) if task.work_package_id else None
        shop = shop_map.get(task.shop_id)

        # §7.2.8 Airline filter
        if airline_category:
            cat = airline_category.upper()
            if cat == "SQ" and ac and not is_sq_airline(ac.airline):
                total -= 1
                continue
            if cat == "THIRD_PARTIES" and ac and is_sq_airline(ac.airline):
                total -= 1
                continue

        items.append({
            "snapshot_id": snap.id,
            "task_id": task.id,
            "meeting_date": snap.meeting_date,
            "aircraft_id": task.aircraft_id,
            "work_package_id": task.work_package_id,
            "rfo_no": wp.rfo_no if wp else None,
            "ac_reg": ac.ac_reg if ac else "",
            "shop_id": task.shop_id,
            "shop_name": shop.name if shop else "",
            "assigned_supervisor_id": task.assigned_supervisor_id,
            "assigned_supervisor_name": _user_name(users_map, task.assigned_supervisor_id),
            "assigned_worker_id": task.assigned_worker_id,
            "assigned_worker_name": _user_name(users_map, task.assigned_worker_id),
            "distributed_at": task.distributed_at,
            "planned_mh": task.planned_mh,
            "task_text": task.task_text,
            "status": snap.status,
            "mh_incurred_hours": snap.mh_incurred_hours,
            "remarks": snap.remarks,
            "critical_issue": snap.critical_issue,
            "has_issue": snap.has_issue,
            "deadline_date": snap.deadline_date,
            "correction_reason": snap.correction_reason,
            "is_deleted": snap.is_deleted,
            "version": snap.version,
            "supervisor_updated_at": snap.supervisor_updated_at,
            "last_updated_at": snap.last_updated_at,
            "last_updated_by": snap.last_updated_by,
            "is_active": task.is_active,
        })

    return {
        "items": items,
        "total": total,
        "page": paging["page"],
        "per_page": paging["per_page"],
    }


# ── §8.4.3 POST /api/tasks ───────────────────────────────────────────

@router.post("", response_model=TaskCreateResponse, status_code=201)
async def create_task(
    body: TaskCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await enforce_shop_access(db, current_user, body.shop_id, "EDIT")

    status = validate_status(body.status)

    # Validate FK: aircraft
    ac = (await db.execute(select(Aircraft).where(Aircraft.id == body.aircraft_id))).scalar_one_or_none()
    if not ac:
        raise APIError(422, "Aircraft not found", "VALIDATION_ERROR", field="aircraft_id")

    # Validate FK: shop
    shop = (await db.execute(select(Shop).where(Shop.id == body.shop_id))).scalar_one_or_none()
    if not shop:
        raise APIError(422, "Shop not found", "VALIDATION_ERROR", field="shop_id")

    # Validate FK: work_package (optional)
    rfo_no = None
    if body.work_package_id is not None:
        wp = (await db.execute(select(WorkPackage).where(WorkPackage.id == body.work_package_id))).scalar_one_or_none()
        if not wp:
            raise APIError(422, "Work package not found", "VALIDATION_ERROR", field="work_package_id")
        rfo_no = wp.rfo_no

    now = datetime.now(timezone.utc)

    # §8.4.3: assigned_supervisor_id → distributed_at = NOW()
    distributed_at = now if body.assigned_supervisor_id is not None else None

    task = TaskItem(
        aircraft_id=body.aircraft_id,
        shop_id=body.shop_id,
        work_package_id=body.work_package_id,
        assigned_supervisor_id=body.assigned_supervisor_id,
        distributed_at=distributed_at,
        planned_mh=body.planned_mh,
        task_text=body.task_text,
        created_by=current_user["user_id"],
        created_at=now,
    )
    db.add(task)
    await db.flush()

    snap = TaskSnapshot(
        task_id=task.id,
        meeting_date=body.meeting_date,
        status=status,
        mh_incurred_hours=body.mh_incurred_hours,
        remarks=body.remarks or None,
        critical_issue=body.critical_issue or None,
        has_issue=body.has_issue,
        deadline_date=body.deadline_date,
        version=1,
        last_updated_by=current_user["user_id"],
        last_updated_at=now,
        created_at=now,
    )
    db.add(snap)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="task_item",
        entity_id=task.id,
        action="CREATE",
        after={
            "task_id": task.id,
            "aircraft_id": task.aircraft_id,
            "shop_id": task.shop_id,
            "work_package_id": task.work_package_id,
            "assigned_supervisor_id": task.assigned_supervisor_id,
            "planned_mh": str(task.planned_mh) if task.planned_mh else None,
            "task_text": task.task_text,
        },
    )
    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="task_snapshot",
        entity_id=snap.id,
        action="CREATE",
        after=_snap_to_dict(snap),
    )
    await db.commit()

    return {
        "task_id": task.id,
        "snapshot_id": snap.id,
        "meeting_date": snap.meeting_date,
        "shop_id": task.shop_id,
        "aircraft_id": task.aircraft_id,
        "work_package_id": task.work_package_id,
        "rfo_no": rfo_no,
        "assigned_supervisor_id": task.assigned_supervisor_id,
        "assigned_worker_id": task.assigned_worker_id,
        "distributed_at": task.distributed_at,
        "planned_mh": task.planned_mh,
        "task_text": task.task_text,
        "status": snap.status,
        "mh_incurred_hours": snap.mh_incurred_hours,
        "deadline_date": snap.deadline_date,
        "remarks": snap.remarks,
        "critical_issue": snap.critical_issue,
        "has_issue": snap.has_issue,
        "correction_reason": snap.correction_reason,
        "version": snap.version,
        "supervisor_updated_at": snap.supervisor_updated_at,
        "last_updated_at": snap.last_updated_at,
        "last_updated_by": snap.last_updated_by,
    }


# ── §8.4.5 PATCH /api/tasks/snapshots/batch ───────────────────────────
# NOTE: Must be registered before /snapshots/{snapshot_id} to avoid
#       FastAPI matching "batch" as a path parameter.

@router.patch("/snapshots/batch", response_model=BatchUpdateResponse)
async def batch_update_snapshots(
    body: BatchUpdateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not body.updates:
        raise APIError(422, "updates list must not be empty", "VALIDATION_ERROR")

    errors: list[dict] = []
    results: list[dict] = []

    # Pre-load all snapshots + tasks in one pass
    snap_ids = [u.snapshot_id for u in body.updates]
    snap_rows = (
        await db.execute(select(TaskSnapshot).where(TaskSnapshot.id.in_(snap_ids)))
    ).scalars().all()
    snap_map = {s.id: s for s in snap_rows}

    task_ids = {s.task_id for s in snap_rows}
    task_rows = (
        await db.execute(select(TaskItem).where(TaskItem.id.in_(task_ids)))
    ).scalars().all()
    task_map = {t.id: t for t in task_rows}

    # Check shop access for all involved shops
    shop_ids_involved = {t.shop_id for t in task_rows}
    for sid in shop_ids_involved:
        await enforce_shop_access(db, current_user, sid, "EDIT")

    now = datetime.now(timezone.utc)

    for item in body.updates:
        snap = snap_map.get(item.snapshot_id)
        if not snap:
            errors.append({
                "snapshot_id": item.snapshot_id,
                "code": "NOT_FOUND",
                "detail": "Snapshot not found",
            })
            continue

        task = task_map.get(snap.task_id)
        if not task:
            errors.append({
                "snapshot_id": item.snapshot_id,
                "code": "NOT_FOUND",
                "detail": "Task not found",
            })
            continue

        # Version check
        if snap.version != item.version:
            errors.append({
                "snapshot_id": item.snapshot_id,
                "code": "CONFLICT_VERSION",
                "current_version": snap.version,
            })
            continue

        # Validate status
        if item.status is not None:
            try:
                validate_status(item.status)
            except APIError:
                errors.append({
                    "snapshot_id": item.snapshot_id,
                    "code": "VALIDATION_ERROR",
                    "detail": f"Invalid status: {item.status}",
                })
                continue

        # MH decrease check
        if item.mh_incurred_hours is not None:
            try:
                await check_mh_decrease(
                    db, snap, item.mh_incurred_hours, current_user,
                    task.shop_id, item.correction_reason,
                )
            except APIError as e:
                errors.append({
                    "snapshot_id": item.snapshot_id,
                    "code": e.code,
                    "detail": e.detail,
                })
                continue

    # All-or-nothing: if any errors, rollback entire batch
    if errors:
        await db.rollback()
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Batch update failed. All changes rolled back.",
                "code": "BATCH_VALIDATION_ERROR",
                "errors": errors,
            },
        )

    # Apply changes
    for item in body.updates:
        snap = snap_map[item.snapshot_id]
        before = _snap_to_dict(snap)

        if item.status is not None:
            snap.status = validate_status(item.status)
        if item.mh_incurred_hours is not None:
            snap.mh_incurred_hours = item.mh_incurred_hours
        if item.has_issue is not None:
            snap.has_issue = item.has_issue
        if item.remarks is not None:
            snap.remarks = item.remarks
        if item.critical_issue is not None:
            snap.critical_issue = item.critical_issue
        if item.correction_reason is not None:
            snap.correction_reason = item.correction_reason
        if "deadline_date" in item.model_fields_set:
            snap.deadline_date = item.deadline_date

        snap.version += 1
        snap.last_updated_at = now
        snap.last_updated_by = current_user["user_id"]
        snap.supervisor_updated_at = now

        await db.flush()

        await write_audit(
            db,
            actor_id=current_user["user_id"],
            entity_type="task_snapshot",
            entity_id=snap.id,
            action="UPDATE",
            before=before,
            after=_snap_to_dict(snap),
        )

        results.append({
            "snapshot_id": snap.id,
            "version": snap.version,
            "status": snap.status,
            "mh_incurred_hours": snap.mh_incurred_hours,
            "deadline_date": snap.deadline_date,
            "remarks": snap.remarks,
            "critical_issue": snap.critical_issue,
            "has_issue": snap.has_issue,
            "correction_reason": snap.correction_reason,
            "last_updated_at": snap.last_updated_at,
            "last_updated_by": snap.last_updated_by,
            "supervisor_updated_at": snap.supervisor_updated_at,
        })

    await db.commit()
    return {"items": results}


# ── §8.4.4 PATCH /api/tasks/snapshots/{snapshot_id} ──────────────────

@router.patch("/snapshots/{snapshot_id}", response_model=SnapshotUpdateResponse)
async def update_snapshot(
    snapshot_id: int,
    body: SnapshotUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    snap = (
        await db.execute(select(TaskSnapshot).where(TaskSnapshot.id == snapshot_id))
    ).scalar_one_or_none()
    if not snap:
        raise APIError(404, "Snapshot not found", "NOT_FOUND")

    task = (
        await db.execute(select(TaskItem).where(TaskItem.id == snap.task_id))
    ).scalar_one_or_none()
    if not task:
        raise APIError(404, "Task not found", "NOT_FOUND")

    await enforce_shop_access(db, current_user, task.shop_id, "EDIT")

    # Optimistic locking
    if snap.version != body.version:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Snapshot modified by another user. Reload and retry.",
                "code": "CONFLICT_VERSION",
                "current_version": snap.version,
            },
        )

    before = _snap_to_dict(snap)

    if body.status is not None:
        snap.status = validate_status(body.status)

    # MH decrease check (§7.2.7)
    if body.mh_incurred_hours is not None:
        await check_mh_decrease(
            db, snap, body.mh_incurred_hours, current_user,
            task.shop_id, body.correction_reason,
        )
        snap.mh_incurred_hours = body.mh_incurred_hours

    if body.has_issue is not None:
        snap.has_issue = body.has_issue

    # §7.2.4 deadline_date: explicit null = clear
    if "deadline_date" in body.model_fields_set:
        snap.deadline_date = body.deadline_date

    if body.remarks is not None:
        snap.remarks = body.remarks
    if body.critical_issue is not None:
        snap.critical_issue = body.critical_issue
    if body.correction_reason is not None:
        snap.correction_reason = body.correction_reason

    now = datetime.now(timezone.utc)
    snap.version += 1
    snap.last_updated_at = now
    snap.last_updated_by = current_user["user_id"]
    snap.supervisor_updated_at = now  # §8.4.4

    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="task_snapshot",
        entity_id=snap.id,
        action="UPDATE",
        before=before,
        after=_snap_to_dict(snap),
    )
    await db.commit()

    return {
        "snapshot_id": snap.id,
        "version": snap.version,
        "status": snap.status,
        "mh_incurred_hours": snap.mh_incurred_hours,
        "deadline_date": snap.deadline_date,
        "remarks": snap.remarks,
        "critical_issue": snap.critical_issue,
        "has_issue": snap.has_issue,
        "correction_reason": snap.correction_reason,
        "last_updated_at": snap.last_updated_at,
        "last_updated_by": snap.last_updated_by,
        "supervisor_updated_at": snap.supervisor_updated_at,
    }


# ── §8.4.7 PATCH /api/tasks/snapshots/{id}/delete ────────────────────

@router.patch("/snapshots/{snapshot_id}/delete", response_model=SnapshotDeleteResponse)
async def soft_delete_snapshot(
    snapshot_id: int,
    body: SnapshotVersionRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    snap = (
        await db.execute(select(TaskSnapshot).where(TaskSnapshot.id == snapshot_id))
    ).scalar_one_or_none()
    if not snap:
        raise APIError(404, "Snapshot not found", "NOT_FOUND")

    task = (
        await db.execute(select(TaskItem).where(TaskItem.id == snap.task_id))
    ).scalar_one_or_none()
    if not task:
        raise APIError(404, "Task not found", "NOT_FOUND")

    await enforce_shop_access(db, current_user, task.shop_id, "MANAGE")

    if snap.version != body.version:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Snapshot modified by another user. Reload and retry.",
                "code": "CONFLICT_VERSION",
                "current_version": snap.version,
            },
        )

    before = _snap_to_dict(snap)
    now = datetime.now(timezone.utc)

    snap.is_deleted = True
    snap.deleted_at = now
    snap.deleted_by = current_user["user_id"]
    snap.version += 1
    snap.last_updated_at = now
    snap.last_updated_by = current_user["user_id"]

    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="task_snapshot",
        entity_id=snap.id,
        action="DELETE",
        before=before,
        after=_snap_to_dict(snap),
    )
    await db.commit()

    return {
        "snapshot_id": snap.id,
        "is_deleted": True,
        "version": snap.version,
        "deleted_at": snap.deleted_at,
        "deleted_by": snap.deleted_by,
    }


# ── §8.4.8 PATCH /api/tasks/snapshots/{id}/restore ───────────────────

@router.patch("/snapshots/{snapshot_id}/restore", response_model=SnapshotRestoreResponse)
async def restore_snapshot(
    snapshot_id: int,
    body: SnapshotVersionRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    snap = (
        await db.execute(select(TaskSnapshot).where(TaskSnapshot.id == snapshot_id))
    ).scalar_one_or_none()
    if not snap:
        raise APIError(404, "Snapshot not found", "NOT_FOUND")

    task = (
        await db.execute(select(TaskItem).where(TaskItem.id == snap.task_id))
    ).scalar_one_or_none()
    if not task:
        raise APIError(404, "Task not found", "NOT_FOUND")

    await enforce_shop_access(db, current_user, task.shop_id, "MANAGE")

    if snap.version != body.version:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Snapshot modified by another user. Reload and retry.",
                "code": "CONFLICT_VERSION",
                "current_version": snap.version,
            },
        )

    before = _snap_to_dict(snap)
    now = datetime.now(timezone.utc)

    snap.is_deleted = False
    snap.deleted_at = None
    snap.deleted_by = None
    snap.version += 1
    snap.last_updated_at = now
    snap.last_updated_by = current_user["user_id"]

    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="task_snapshot",
        entity_id=snap.id,
        action="RESTORE",
        before=before,
        after=_snap_to_dict(snap),
    )
    await db.commit()

    return {
        "snapshot_id": snap.id,
        "is_deleted": False,
        "version": snap.version,
        "deleted_at": None,
        "deleted_by": None,
    }


# ── §8.4.6 PATCH /api/tasks/{id}/deactivate ──────────────────────────

@router.patch("/{task_id}/deactivate", response_model=TaskDeactivateResponse)
async def deactivate_task(
    task_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task = (
        await db.execute(select(TaskItem).where(TaskItem.id == task_id))
    ).scalar_one_or_none()
    if not task:
        raise APIError(404, "Task not found", "NOT_FOUND")

    await enforce_shop_access(db, current_user, task.shop_id, "MANAGE")

    now = datetime.now(timezone.utc)

    before = {
        "task_id": task.id,
        "is_active": task.is_active,
        "deactivated_at": str(task.deactivated_at) if task.deactivated_at else None,
        "deactivated_by": task.deactivated_by,
    }

    task.is_active = False
    task.deactivated_at = now
    task.deactivated_by = current_user["user_id"]

    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="task_item",
        entity_id=task.id,
        action="DEACTIVATE",
        before=before,
        after={
            "task_id": task.id,
            "is_active": False,
            "deactivated_at": str(now),
            "deactivated_by": current_user["user_id"],
        },
    )
    await db.commit()

    return {
        "task_id": task.id,
        "is_active": False,
        "deactivated_at": task.deactivated_at,
        "deactivated_by": task.deactivated_by,
    }


# ── §8.4.6 PATCH /api/tasks/{id}/reactivate ──────────────────────────

@router.patch("/{task_id}/reactivate", response_model=TaskDeactivateResponse)
async def reactivate_task(
    task_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task = (
        await db.execute(select(TaskItem).where(TaskItem.id == task_id))
    ).scalar_one_or_none()
    if not task:
        raise APIError(404, "Task not found", "NOT_FOUND")

    await enforce_shop_access(db, current_user, task.shop_id, "MANAGE")

    before = {
        "task_id": task.id,
        "is_active": task.is_active,
        "deactivated_at": str(task.deactivated_at) if task.deactivated_at else None,
        "deactivated_by": task.deactivated_by,
    }

    task.is_active = True
    task.deactivated_at = None
    task.deactivated_by = None

    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="task_item",
        entity_id=task.id,
        action="REACTIVATE",
        before=before,
        after={
            "task_id": task.id,
            "is_active": True,
            "deactivated_at": None,
            "deactivated_by": None,
        },
    )
    await db.commit()

    return {
        "task_id": task.id,
        "is_active": True,
        "deactivated_at": None,
        "deactivated_by": None,
    }


# ── §8.4.1 POST /api/tasks/init-week ─────────────────────────────────

@router.post("/init-week", response_model=InitWeekResponse)
async def init_week_endpoint(
    body: InitWeekRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await enforce_shop_access(db, current_user, body.shop_id, "MANAGE")

    result = await init_week(db, body.shop_id, body.meeting_date, current_user["user_id"])

    if result["created_count"] > 0:
        await write_audit(
            db,
            actor_id=current_user["user_id"],
            entity_type="task_snapshot",
            entity_id=0,
            action="INIT_WEEK",
            after=result,
        )

    await db.commit()
    return result
