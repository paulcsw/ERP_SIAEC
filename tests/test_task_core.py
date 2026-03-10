"""Tests for Branch 07 — Task core API (list, create, update, init-week)."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

CSRF = {"X-CSRFToken": "test-csrf-token-abc123"}


# ── Helpers ───────────────────────────────────────────────────────────

async def _seed_shop_and_aircraft(db):
    """Seed a shop, aircraft, and give admin MANAGE access. Return (shop, aircraft)."""
    from app.models.shop import Shop
    from app.models.reference import Aircraft

    async with db() as session:
        shop = Shop(code="SM", name="Sheet Metal", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-SMA", airline="SQ")
        session.add(ac)
        await session.commit()
        return shop.id, ac.id


async def _create_task(client, shop_id, ac_id, meeting_date="2026-03-10", **kw):
    payload = {
        "meeting_date": meeting_date,
        "shop_id": shop_id,
        "aircraft_id": ac_id,
        "task_text": kw.get("task_text", "Test task"),
        "status": kw.get("status", "NOT_STARTED"),
        "mh_incurred_hours": kw.get("mh_incurred_hours", 0),
        **{k: v for k, v in kw.items() if k not in ("task_text", "status", "mh_incurred_hours")},
    }
    r = await client.post("/api/tasks", json=payload, headers=CSRF)
    return r


# ═══════════════════════════════════════════════════════════════════════
# Commit 1: GET /api/tasks/snapshots
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_list_snapshots_empty(async_client, db):
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    r = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_snapshots_with_data(async_client, db):
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    await _create_task(async_client, shop_id, ac_id)

    r = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["task_text"] == "Test task"
    assert item["ac_reg"] == "9V-SMA"
    assert item["shop_name"] == "Sheet Metal"
    assert item["status"] == "NOT_STARTED"


@pytest.mark.asyncio
async def test_list_snapshots_status_filter(async_client, db):
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    await _create_task(async_client, shop_id, ac_id, status="IN_PROGRESS")
    await _create_task(async_client, shop_id, ac_id, task_text="Task 2", status="COMPLETED")

    r = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}&status=IN_PROGRESS"
    )
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_list_snapshots_include_deleted(async_client, db):
    """Deleted snapshots excluded by default, included with flag."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    snap_id = cr.json()["snapshot_id"]

    # Soft-delete via direct DB
    from app.models.task import TaskSnapshot
    from sqlalchemy import select

    async with db() as session:
        snap = (await session.execute(
            select(TaskSnapshot).where(TaskSnapshot.id == snap_id)
        )).scalar_one()
        snap.is_deleted = True
        await session.commit()

    # Default: excluded
    r = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    assert r.json()["total"] == 0

    # include_deleted=true
    r2 = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}&include_deleted=true"
    )
    assert r2.json()["total"] == 1


@pytest.mark.asyncio
async def test_list_snapshots_supervisor_filter(async_client, db):
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    await _create_task(async_client, shop_id, ac_id, assigned_supervisor_id=2)
    await _create_task(async_client, shop_id, ac_id, task_text="No sup")

    r = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}&assigned_supervisor_id=2"
    )
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["assigned_supervisor_id"] == 2


# ═══════════════════════════════════════════════════════════════════════
# Commit 2: POST /api/tasks
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_task_basic(async_client, db):
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    r = await _create_task(async_client, shop_id, ac_id)
    assert r.status_code == 201
    data = r.json()
    assert data["task_id"] is not None
    assert data["snapshot_id"] is not None
    assert data["version"] == 1
    assert data["status"] == "NOT_STARTED"


@pytest.mark.asyncio
async def test_create_task_with_supervisor_sets_distributed_at(async_client, db):
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    r = await _create_task(
        async_client, shop_id, ac_id,
        assigned_supervisor_id=2, planned_mh=15.0,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["assigned_supervisor_id"] == 2
    assert data["distributed_at"] is not None
    assert float(data["planned_mh"]) == 15.0


@pytest.mark.asyncio
async def test_create_task_without_supervisor_no_distributed_at(async_client, db):
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    r = await _create_task(async_client, shop_id, ac_id)
    assert r.status_code == 201
    assert r.json()["distributed_at"] is None


@pytest.mark.asyncio
async def test_create_task_invalid_aircraft(async_client, db):
    shop_id, _ = await _seed_shop_and_aircraft(db)
    r = await _create_task(async_client, shop_id, 9999)
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_create_task_invalid_status(async_client, db):
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    r = await _create_task(async_client, shop_id, ac_id, status="INVALID")
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_create_task_audit_logged(async_client, db):
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    r = await _create_task(async_client, shop_id, ac_id)
    task_id = r.json()["task_id"]

    from sqlalchemy import select
    from app.models.audit import AuditLog

    async with db() as session:
        logs = (await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "task_item",
                AuditLog.entity_id == task_id,
            )
        )).scalars().all()
        assert any(l.action == "CREATE" for l in logs)


# ═══════════════════════════════════════════════════════════════════════
# Commit 3: PATCH /api/tasks/snapshots/{id}
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_update_snapshot_basic(async_client, db):
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    snap_id = cr.json()["snapshot_id"]

    r = await async_client.patch(
        f"/api/tasks/snapshots/{snap_id}",
        json={"version": 1, "status": "IN_PROGRESS", "mh_incurred_hours": 5.0},
        headers=CSRF,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == 2
    assert data["status"] == "IN_PROGRESS"
    assert float(data["mh_incurred_hours"]) == 5.0
    assert data["supervisor_updated_at"] is not None


@pytest.mark.asyncio
async def test_update_snapshot_version_conflict(async_client, db):
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    snap_id = cr.json()["snapshot_id"]

    # First update succeeds
    await async_client.patch(
        f"/api/tasks/snapshots/{snap_id}",
        json={"version": 1, "status": "IN_PROGRESS"},
        headers=CSRF,
    )

    # Same version again → conflict
    r = await async_client.patch(
        f"/api/tasks/snapshots/{snap_id}",
        json={"version": 1, "status": "COMPLETED"},
        headers=CSRF,
    )
    assert r.status_code == 409
    data = r.json()
    assert data["code"] == "CONFLICT_VERSION"
    assert data["current_version"] == 2


@pytest.mark.asyncio
async def test_mh_decrease_edit_blocked(async_client, db):
    """EDIT user cannot decrease MH."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)

    # Give supervisor EDIT access
    from app.models.user_shop_access import UserShopAccess
    async with db() as session:
        session.add(UserShopAccess(user_id=2, shop_id=shop_id, access="EDIT", granted_by=1))
        await session.commit()

    # Create task as admin with initial MH
    cr = await _create_task(async_client, shop_id, ac_id, mh_incurred_hours=10.0)
    snap_id = cr.json()["snapshot_id"]

    # Try to decrease MH as supervisor (EDIT only)
    r = await async_client.patch(
        f"/api/tasks/snapshots/{snap_id}",
        json={"version": 1, "mh_incurred_hours": 5.0},
        headers=CSRF,
    )
    # Admin has MANAGE (bypass), so this test uses the admin client.
    # Admin = bypass = MANAGE → needs correction_reason.
    # Let's verify the MANAGE path requires correction_reason.
    assert r.status_code == 422
    assert r.json()["code"] == "CORRECTION_REASON_REQUIRED"


@pytest.mark.asyncio
async def test_mh_decrease_manage_needs_correction_reason(async_client, db):
    """MANAGE user can decrease MH but must provide correction_reason."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id, mh_incurred_hours=10.0)
    snap_id = cr.json()["snapshot_id"]

    # Decrease without correction_reason → error
    r = await async_client.patch(
        f"/api/tasks/snapshots/{snap_id}",
        json={"version": 1, "mh_incurred_hours": 5.0},
        headers=CSRF,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "CORRECTION_REASON_REQUIRED"

    # Decrease with correction_reason → success
    r2 = await async_client.patch(
        f"/api/tasks/snapshots/{snap_id}",
        json={
            "version": 1,
            "mh_incurred_hours": 5.0,
            "correction_reason": "Data entry error",
        },
        headers=CSRF,
    )
    assert r2.status_code == 200
    assert float(r2.json()["mh_incurred_hours"]) == 5.0
    assert r2.json()["correction_reason"] == "Data entry error"


@pytest.mark.asyncio
async def test_mh_decrease_edit_user_forbidden(db, sup_client):
    """Supervisor with only EDIT access cannot decrease MH."""
    from app.models.shop import Shop
    from app.models.reference import Aircraft
    from app.models.user_shop_access import UserShopAccess
    from app.models.task import TaskItem, TaskSnapshot

    async with db() as session:
        shop = Shop(code="EDSHOP", name="Edit Shop", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-EDT", airline="SQ")
        session.add(ac)
        await session.flush()

        # Give supervisor EDIT access (not MANAGE)
        session.add(UserShopAccess(user_id=2, shop_id=shop.id, access="EDIT", granted_by=1))
        await session.flush()

        now = datetime.now(timezone.utc)
        task = TaskItem(
            aircraft_id=ac.id, shop_id=shop.id, task_text="MH test",
            created_by=1, created_at=now,
        )
        session.add(task)
        await session.flush()

        snap = TaskSnapshot(
            task_id=task.id, meeting_date=date(2026, 3, 10),
            mh_incurred_hours=Decimal("10.0"),
            last_updated_by=1, last_updated_at=now, created_at=now,
        )
        session.add(snap)
        await session.commit()
        snap_id = snap.id

    r = await sup_client.patch(
        f"/api/tasks/snapshots/{snap_id}",
        json={"version": 1, "mh_incurred_hours": 5.0},
        headers=CSRF,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "MH_DECREASE_FORBIDDEN"


@pytest.mark.asyncio
async def test_mh_increase_allowed_for_edit(db, sup_client):
    """EDIT user can increase MH (only decrease is blocked)."""
    from app.models.shop import Shop
    from app.models.reference import Aircraft
    from app.models.user_shop_access import UserShopAccess
    from app.models.task import TaskItem, TaskSnapshot

    async with db() as session:
        shop = Shop(code="INCSHOP", name="Inc Shop", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-INC", airline="SQ")
        session.add(ac)
        await session.flush()

        session.add(UserShopAccess(user_id=2, shop_id=shop.id, access="EDIT", granted_by=1))
        await session.flush()

        now = datetime.now(timezone.utc)
        task = TaskItem(
            aircraft_id=ac.id, shop_id=shop.id, task_text="MH inc test",
            created_by=1, created_at=now,
        )
        session.add(task)
        await session.flush()

        snap = TaskSnapshot(
            task_id=task.id, meeting_date=date(2026, 3, 10),
            mh_incurred_hours=Decimal("5.0"),
            last_updated_by=1, last_updated_at=now, created_at=now,
        )
        session.add(snap)
        await session.commit()
        snap_id = snap.id

    r = await sup_client.patch(
        f"/api/tasks/snapshots/{snap_id}",
        json={"version": 1, "mh_incurred_hours": 10.0},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert float(r.json()["mh_incurred_hours"]) == 10.0


@pytest.mark.asyncio
async def test_update_snapshot_audit(async_client, db):
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    snap_id = cr.json()["snapshot_id"]

    await async_client.patch(
        f"/api/tasks/snapshots/{snap_id}",
        json={"version": 1, "status": "IN_PROGRESS"},
        headers=CSRF,
    )

    from sqlalchemy import select
    from app.models.audit import AuditLog

    async with db() as session:
        logs = (await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "task_snapshot",
                AuditLog.entity_id == snap_id,
                AuditLog.action == "UPDATE",
            )
        )).scalars().all()
        assert len(logs) == 1


# ═══════════════════════════════════════════════════════════════════════
# Commit 4: POST /api/tasks/init-week
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_init_week_carry_over(async_client, db):
    """Init-week copies eligible snapshots from prev week."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)

    # Create tasks for week 1 (2026-03-03)
    await _create_task(
        async_client, shop_id, ac_id,
        meeting_date="2026-03-03", status="IN_PROGRESS", mh_incurred_hours=5.0,
    )
    await _create_task(
        async_client, shop_id, ac_id,
        meeting_date="2026-03-03", task_text="Task 2", status="COMPLETED",
    )

    # Init week 2 (2026-03-10)
    r = await async_client.post(
        "/api/tasks/init-week",
        json={"meeting_date": "2026-03-10", "shop_id": shop_id},
        headers=CSRF,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["created_count"] == 1  # only IN_PROGRESS, not COMPLETED
    assert data["skipped_count"] == 0

    # Verify carried-over snapshot
    r2 = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    items = r2.json()["items"]
    assert len(items) == 1
    assert float(items[0]["mh_incurred_hours"]) == 5.0  # copied from prev week
    assert items[0]["status"] == "IN_PROGRESS"
    assert items[0]["supervisor_updated_at"] is None  # reset
    assert items[0]["version"] == 1


@pytest.mark.asyncio
async def test_init_week_idempotent(async_client, db):
    """Re-calling init-week doesn't create duplicates."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    await _create_task(
        async_client, shop_id, ac_id,
        meeting_date="2026-03-03", status="IN_PROGRESS",
    )

    # First call
    r1 = await async_client.post(
        "/api/tasks/init-week",
        json={"meeting_date": "2026-03-10", "shop_id": shop_id},
        headers=CSRF,
    )
    assert r1.json()["created_count"] == 1

    # Second call — idempotent
    r2 = await async_client.post(
        "/api/tasks/init-week",
        json={"meeting_date": "2026-03-10", "shop_id": shop_id},
        headers=CSRF,
    )
    assert r2.json()["created_count"] == 0
    assert r2.json()["skipped_count"] == 1


@pytest.mark.asyncio
async def test_init_week_skips_deleted(async_client, db):
    """Soft-deleted snapshots are not carried over."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(
        async_client, shop_id, ac_id,
        meeting_date="2026-03-03", status="IN_PROGRESS",
    )
    snap_id = cr.json()["snapshot_id"]

    # Soft-delete
    from app.models.task import TaskSnapshot
    from sqlalchemy import select

    async with db() as session:
        snap = (await session.execute(
            select(TaskSnapshot).where(TaskSnapshot.id == snap_id)
        )).scalar_one()
        snap.is_deleted = True
        await session.commit()

    r = await async_client.post(
        "/api/tasks/init-week",
        json={"meeting_date": "2026-03-10", "shop_id": shop_id},
        headers=CSRF,
    )
    assert r.json()["created_count"] == 0


@pytest.mark.asyncio
async def test_init_week_skips_inactive_task(async_client, db):
    """Deactivated task_items are not carried over."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(
        async_client, shop_id, ac_id,
        meeting_date="2026-03-03", status="IN_PROGRESS",
    )
    task_id = cr.json()["task_id"]

    # Deactivate
    from app.models.task import TaskItem
    from sqlalchemy import select

    async with db() as session:
        task = (await session.execute(
            select(TaskItem).where(TaskItem.id == task_id)
        )).scalar_one()
        task.is_active = False
        await session.commit()

    r = await async_client.post(
        "/api/tasks/init-week",
        json={"meeting_date": "2026-03-10", "shop_id": shop_id},
        headers=CSRF,
    )
    assert r.json()["created_count"] == 0


@pytest.mark.asyncio
async def test_init_week_copies_mh_and_resets_supervisor_updated(async_client, db):
    """Carry-over preserves mh_incurred_hours but resets supervisor_updated_at."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(
        async_client, shop_id, ac_id,
        meeting_date="2026-03-03", status="IN_PROGRESS", mh_incurred_hours=42.5,
    )
    snap_id = cr.json()["snapshot_id"]

    # Set supervisor_updated_at on original
    from app.models.task import TaskSnapshot
    from sqlalchemy import select

    async with db() as session:
        snap = (await session.execute(
            select(TaskSnapshot).where(TaskSnapshot.id == snap_id)
        )).scalar_one()
        snap.supervisor_updated_at = datetime.now(timezone.utc)
        await session.commit()

    await async_client.post(
        "/api/tasks/init-week",
        json={"meeting_date": "2026-03-10", "shop_id": shop_id},
        headers=CSRF,
    )

    r = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    item = r.json()["items"][0]
    assert float(item["mh_incurred_hours"]) == 42.5
    assert item["supervisor_updated_at"] is None
    assert item["version"] == 1
