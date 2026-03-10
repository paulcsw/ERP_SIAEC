"""Tests for Branch 08 — Task lifecycle: batch update, soft delete/restore, deactivate/reactivate."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

CSRF = {"X-CSRFToken": "test-csrf-token-abc123"}


# ── Helpers ───────────────────────────────────────────────────────────

async def _seed_shop_and_aircraft(db):
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
# Commit 1: PATCH /api/tasks/snapshots/batch
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_batch_update_multiple_snapshots(async_client, db):
    """Batch update succeeds for multiple snapshots."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    r1 = await _create_task(async_client, shop_id, ac_id, task_text="Task A")
    r2 = await _create_task(async_client, shop_id, ac_id, task_text="Task B")
    sid1 = r1.json()["snapshot_id"]
    sid2 = r2.json()["snapshot_id"]

    r = await async_client.patch(
        "/api/tasks/snapshots/batch",
        json={
            "updates": [
                {"snapshot_id": sid1, "version": 1, "status": "IN_PROGRESS", "mh_incurred_hours": 5.0},
                {"snapshot_id": sid2, "version": 1, "status": "COMPLETED", "mh_incurred_hours": 8.0},
            ]
        },
        headers=CSRF,
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 2
    assert data["items"][0]["version"] == 2
    assert data["items"][0]["status"] == "IN_PROGRESS"
    assert float(data["items"][0]["mh_incurred_hours"]) == 5.0
    assert data["items"][1]["version"] == 2
    assert data["items"][1]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_batch_update_version_conflict_rollback(async_client, db):
    """Version conflict on one item rolls back the entire batch."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    r1 = await _create_task(async_client, shop_id, ac_id, task_text="Task A")
    r2 = await _create_task(async_client, shop_id, ac_id, task_text="Task B")
    sid1 = r1.json()["snapshot_id"]
    sid2 = r2.json()["snapshot_id"]

    # Update sid2 to bump version to 2
    await async_client.patch(
        f"/api/tasks/snapshots/{sid2}",
        json={"version": 1, "status": "IN_PROGRESS"},
        headers=CSRF,
    )

    # Batch: sid1 OK, sid2 has stale version (1 instead of 2)
    r = await async_client.patch(
        "/api/tasks/snapshots/batch",
        json={
            "updates": [
                {"snapshot_id": sid1, "version": 1, "status": "COMPLETED"},
                {"snapshot_id": sid2, "version": 1, "status": "COMPLETED"},
            ]
        },
        headers=CSRF,
    )
    assert r.status_code == 422
    data = r.json()
    assert data["code"] == "BATCH_VALIDATION_ERROR"
    assert any(e["snapshot_id"] == sid2 and e["code"] == "CONFLICT_VERSION" for e in data["errors"])

    # Verify sid1 was NOT updated (rollback)
    r_check = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    items = {i["snapshot_id"]: i for i in r_check.json()["items"]}
    assert items[sid1]["version"] == 1  # unchanged
    assert items[sid1]["status"] == "NOT_STARTED"  # unchanged


@pytest.mark.asyncio
async def test_batch_update_validation_error_rollback(async_client, db):
    """Field validation error on one item rolls back the entire batch."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    r1 = await _create_task(async_client, shop_id, ac_id, task_text="Task A")
    r2 = await _create_task(async_client, shop_id, ac_id, task_text="Task B")
    sid1 = r1.json()["snapshot_id"]
    sid2 = r2.json()["snapshot_id"]

    r = await async_client.patch(
        "/api/tasks/snapshots/batch",
        json={
            "updates": [
                {"snapshot_id": sid1, "version": 1, "status": "IN_PROGRESS"},
                {"snapshot_id": sid2, "version": 1, "status": "INVALID_STATUS"},
            ]
        },
        headers=CSRF,
    )
    assert r.status_code == 422
    data = r.json()
    assert data["code"] == "BATCH_VALIDATION_ERROR"
    assert any(e["snapshot_id"] == sid2 and e["code"] == "VALIDATION_ERROR" for e in data["errors"])

    # Verify sid1 was NOT updated (rollback)
    r_check = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    items = {i["snapshot_id"]: i for i in r_check.json()["items"]}
    assert items[sid1]["version"] == 1
    assert items[sid1]["status"] == "NOT_STARTED"


@pytest.mark.asyncio
async def test_batch_update_audit_per_snapshot(async_client, db):
    """Batch creates one audit log per snapshot."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    r1 = await _create_task(async_client, shop_id, ac_id, task_text="Task A")
    r2 = await _create_task(async_client, shop_id, ac_id, task_text="Task B")
    sid1 = r1.json()["snapshot_id"]
    sid2 = r2.json()["snapshot_id"]

    await async_client.patch(
        "/api/tasks/snapshots/batch",
        json={
            "updates": [
                {"snapshot_id": sid1, "version": 1, "status": "IN_PROGRESS"},
                {"snapshot_id": sid2, "version": 1, "status": "COMPLETED"},
            ]
        },
        headers=CSRF,
    )

    from sqlalchemy import select
    from app.models.audit import AuditLog

    async with db() as session:
        logs = (await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "task_snapshot",
                AuditLog.action == "UPDATE",
                AuditLog.entity_id.in_([sid1, sid2]),
            )
        )).scalars().all()
        assert len(logs) == 2


# ═══════════════════════════════════════════════════════════════════════
# Commit 2: Soft delete / Restore
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_soft_delete_snapshot(async_client, db):
    """Soft-deleted snapshot hidden from default list."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    snap_id = cr.json()["snapshot_id"]

    r = await async_client.patch(
        f"/api/tasks/snapshots/{snap_id}/delete",
        json={"version": 1},
        headers=CSRF,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["is_deleted"] is True
    assert data["version"] == 2
    assert data["deleted_at"] is not None
    assert data["deleted_by"] is not None

    # Default list excludes deleted
    r_list = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    assert r_list.json()["total"] == 0

    # include_deleted shows it
    r_list2 = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}&include_deleted=true"
    )
    assert r_list2.json()["total"] == 1


@pytest.mark.asyncio
async def test_restore_snapshot(async_client, db):
    """Restored snapshot reappears in default list."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    snap_id = cr.json()["snapshot_id"]

    # Delete
    await async_client.patch(
        f"/api/tasks/snapshots/{snap_id}/delete",
        json={"version": 1},
        headers=CSRF,
    )

    # Restore
    r = await async_client.patch(
        f"/api/tasks/snapshots/{snap_id}/restore",
        json={"version": 2},
        headers=CSRF,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["is_deleted"] is False
    assert data["version"] == 3
    assert data["deleted_at"] is None
    assert data["deleted_by"] is None

    # Default list shows it again
    r_list = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    assert r_list.json()["total"] == 1


@pytest.mark.asyncio
async def test_soft_delete_audit(async_client, db):
    """Soft delete writes audit log."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    snap_id = cr.json()["snapshot_id"]

    await async_client.patch(
        f"/api/tasks/snapshots/{snap_id}/delete",
        json={"version": 1},
        headers=CSRF,
    )

    from sqlalchemy import select
    from app.models.audit import AuditLog

    async with db() as session:
        logs = (await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "task_snapshot",
                AuditLog.entity_id == snap_id,
                AuditLog.action == "DELETE",
            )
        )).scalars().all()
        assert len(logs) == 1


@pytest.mark.asyncio
async def test_soft_delete_requires_manage(db, sup_client):
    """EDIT-only user cannot soft delete (needs MANAGE)."""
    from app.models.shop import Shop
    from app.models.reference import Aircraft
    from app.models.user_shop_access import UserShopAccess
    from app.models.task import TaskItem, TaskSnapshot

    async with db() as session:
        shop = Shop(code="DELSHOP", name="Del Shop", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-DEL", airline="SQ")
        session.add(ac)
        await session.flush()

        # Give supervisor EDIT only (not MANAGE)
        session.add(UserShopAccess(user_id=2, shop_id=shop.id, access="EDIT", granted_by=1))
        await session.flush()

        now = datetime.now(timezone.utc)
        task = TaskItem(
            aircraft_id=ac.id, shop_id=shop.id, task_text="Del test",
            created_by=1, created_at=now,
        )
        session.add(task)
        await session.flush()

        snap = TaskSnapshot(
            task_id=task.id, meeting_date=date(2026, 3, 10),
            last_updated_by=1, last_updated_at=now, created_at=now,
        )
        session.add(snap)
        await session.commit()
        snap_id = snap.id

    r = await sup_client.patch(
        f"/api/tasks/snapshots/{snap_id}/delete",
        json={"version": 1},
        headers=CSRF,
    )
    assert r.status_code == 403
    assert r.json()["code"] == "SHOP_ACCESS_DENIED"


# ═══════════════════════════════════════════════════════════════════════
# Commit 3: Deactivate / Reactivate
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_deactivate_task(async_client, db):
    """Deactivating a task sets is_active=false."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    task_id = cr.json()["task_id"]

    r = await async_client.patch(
        f"/api/tasks/{task_id}/deactivate",
        headers=CSRF,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["is_active"] is False
    assert data["deactivated_at"] is not None
    assert data["deactivated_by"] is not None


@pytest.mark.asyncio
async def test_reactivate_task(async_client, db):
    """Reactivating a task sets is_active=true."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    task_id = cr.json()["task_id"]

    # Deactivate first
    await async_client.patch(f"/api/tasks/{task_id}/deactivate", headers=CSRF)

    # Reactivate
    r = await async_client.patch(
        f"/api/tasks/{task_id}/reactivate",
        headers=CSRF,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["is_active"] is True
    assert data["deactivated_at"] is None
    assert data["deactivated_by"] is None


@pytest.mark.asyncio
async def test_deactivate_excludes_from_init_week(async_client, db):
    """Deactivated tasks are excluded from init-week carry-over."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(
        async_client, shop_id, ac_id,
        meeting_date="2026-03-03", status="IN_PROGRESS",
    )
    task_id = cr.json()["task_id"]

    # Deactivate
    await async_client.patch(f"/api/tasks/{task_id}/deactivate", headers=CSRF)

    # Init week
    r = await async_client.post(
        "/api/tasks/init-week",
        json={"meeting_date": "2026-03-10", "shop_id": shop_id},
        headers=CSRF,
    )
    assert r.json()["created_count"] == 0


@pytest.mark.asyncio
async def test_deactivate_audit(async_client, db):
    """Deactivate writes audit log."""
    shop_id, ac_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    task_id = cr.json()["task_id"]

    await async_client.patch(f"/api/tasks/{task_id}/deactivate", headers=CSRF)

    from sqlalchemy import select
    from app.models.audit import AuditLog

    async with db() as session:
        logs = (await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "task_item",
                AuditLog.entity_id == task_id,
                AuditLog.action == "DEACTIVATE",
            )
        )).scalars().all()
        assert len(logs) == 1


@pytest.mark.asyncio
async def test_deactivate_requires_manage(db, sup_client):
    """EDIT-only user cannot deactivate (needs MANAGE)."""
    from app.models.shop import Shop
    from app.models.reference import Aircraft
    from app.models.user_shop_access import UserShopAccess
    from app.models.task import TaskItem

    async with db() as session:
        shop = Shop(code="DEACTSHOP", name="Deact Shop", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-DCA", airline="SQ")
        session.add(ac)
        await session.flush()

        session.add(UserShopAccess(user_id=2, shop_id=shop.id, access="EDIT", granted_by=1))
        await session.flush()

        now = datetime.now(timezone.utc)
        task = TaskItem(
            aircraft_id=ac.id, shop_id=shop.id, task_text="Deact test",
            created_by=1, created_at=now,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    r = await sup_client.patch(
        f"/api/tasks/{task_id}/deactivate",
        headers=CSRF,
    )
    assert r.status_code == 403
    assert r.json()["code"] == "SHOP_ACCESS_DENIED"
