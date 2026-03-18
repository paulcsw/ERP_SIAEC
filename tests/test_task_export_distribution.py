"""Tests for Branch 09 — CSV export + Task Distribution endpoints."""
import csv
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

CSRF = {"X-CSRFToken": "test-csrf-token-abc123"}


# ── Helpers ──────────────────────────────────────────────────────────

async def _seed_shop_and_aircraft(db):
    """Seed a shop, aircraft, and optionally a work package."""
    from app.models.shop import Shop
    from app.models.reference import Aircraft, WorkPackage

    async with db() as session:
        shop = Shop(code="SM09", name="Sheet Metal 09", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-TST", airline="SQ")
        session.add(ac)
        await session.flush()

        wp = WorkPackage(aircraft_id=ac.id, rfo_no="1200000101", title="Test WP")
        session.add(wp)
        await session.commit()
        return shop.id, ac.id, wp.id


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


async def _grant_supervisor_access(db, shop_id, user_id=2, access="EDIT"):
    from app.models.user_shop_access import UserShopAccess

    async with db() as session:
        row = UserShopAccess(user_id=user_id, shop_id=shop_id, access=access, granted_by=1)
        session.add(row)
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════
# Commit 1: GET /api/tasks/export/csv
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_export_csv_empty(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    r = await async_client.get(
        f"/api/tasks/export/csv?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]

    # Parse CSV
    reader = csv.reader(io.StringIO(r.text))
    rows = list(reader)
    assert len(rows) == 1  # header only
    assert rows[0][0] == "task_id"


@pytest.mark.asyncio
async def test_export_csv_with_data(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    await _create_task(
        async_client, shop_id, ac_id,
        task_text="Export task", mh_incurred_hours=10.0,
        work_package_id=wp_id,
    )

    r = await async_client.get(
        f"/api/tasks/export/csv?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    assert r.status_code == 200

    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["task_text"] == "Export task"
    assert float(rows[0]["mh_incurred_hours"]) == 10.0
    assert rows[0]["rfo_no"] == "1200000101"
    assert rows[0]["status"] == "NOT_STARTED"


@pytest.mark.asyncio
async def test_export_csv_excludes_deleted(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    snap_id = cr.json()["snapshot_id"]

    # Soft delete the snapshot
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
        f"/api/tasks/export/csv?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    reader = csv.reader(io.StringIO(r.text))
    rows = list(reader)
    assert len(rows) == 1  # header only

    # include_deleted=true
    r2 = await async_client.get(
        f"/api/tasks/export/csv?meeting_date=2026-03-10&shop_id={shop_id}&include_deleted=true"
    )
    reader2 = csv.reader(io.StringIO(r2.text))
    rows2 = list(reader2)
    assert len(rows2) == 2  # header + 1 row


@pytest.mark.asyncio
async def test_export_csv_weekly_mh_delta(async_client, db):
    """weekly_mh_delta = current MH - previous week MH."""
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)

    # Create task in week 1 with MH=10
    cr = await _create_task(
        async_client, shop_id, ac_id,
        meeting_date="2026-03-03", mh_incurred_hours=10.0, status="IN_PROGRESS",
    )

    # Init week 2 (carries over to 2026-03-10)
    await async_client.post(
        "/api/tasks/init-week",
        json={"meeting_date": "2026-03-10", "shop_id": shop_id},
        headers=CSRF,
    )

    # Update carried-over snapshot to MH=25
    r_list = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    snap = r_list.json()["items"][0]
    await async_client.patch(
        f"/api/tasks/snapshots/{snap['snapshot_id']}",
        json={"version": snap["version"], "mh_incurred_hours": 25.0},
        headers=CSRF,
    )

    # Export CSV for week 2
    r = await async_client.get(
        f"/api/tasks/export/csv?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)
    assert len(rows) == 1
    # Delta should be 25 - 10 = 15
    assert float(rows[0]["weekly_mh_delta"]) == 15.0


@pytest.mark.asyncio
async def test_export_csv_delta_no_prev_week(async_client, db):
    """If no previous week snapshot, delta = current MH."""
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    await _create_task(
        async_client, shop_id, ac_id,
        meeting_date="2026-03-10", mh_incurred_hours=7.5,
    )

    r = await async_client.get(
        f"/api/tasks/export/csv?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)
    assert float(rows[0]["weekly_mh_delta"]) == 7.5


@pytest.mark.asyncio
async def test_export_csv_filename(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    r = await async_client.get(
        f"/api/tasks/export/csv?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    assert f"tasks_2026-03-10_{shop_id}.csv" in r.headers["content-disposition"]


# ═══════════════════════════════════════════════════════════════════════
# Commit 2: Task Distribution — Import
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_import_csv_preview(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)

    csv_content = "ac_reg,rfo_no,description,planned_mh\n9V-TST,1200000101,Install panel,15.0\nINVALID,,Bad row,0\n"
    files = {"file": ("tasks.csv", csv_content.encode(), "text/csv")}
    r = await async_client.post("/api/tasks/import", files=files, headers=CSRF)
    assert r.status_code == 200
    data = r.json()
    assert data["valid_count"] == 1
    assert data["error_count"] == 1
    assert len(data["preview"]) == 2
    assert data["preview"][0]["valid"] is True
    assert data["preview"][1]["valid"] is False
    assert "Aircraft not found" in data["preview"][1]["error"]


@pytest.mark.asyncio
async def test_import_csv_wp_not_found(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)

    csv_content = "ac_reg,rfo_no,description,planned_mh\n9V-TST,BADRFO,Test,5\n"
    files = {"file": ("tasks.csv", csv_content.encode(), "text/csv")}
    r = await async_client.post("/api/tasks/import", files=files, headers=CSRF)
    assert r.status_code == 200
    data = r.json()
    assert data["error_count"] == 1
    assert data["preview"][0]["error"] == "Work package not found"


@pytest.mark.asyncio
async def test_import_non_admin_forbidden(db, sup_client):
    """Non-ADMIN users cannot call import."""
    csv_content = "ac_reg,rfo_no,description,planned_mh\n9V-TST,,Test,0\n"
    files = {"file": ("tasks.csv", csv_content.encode(), "text/csv")}
    r = await sup_client.post("/api/tasks/import", files=files, headers=CSRF)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_import_confirm_creates_tasks(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)

    body = {
        "shop_id": shop_id,
        "meeting_date": "2026-03-10",
        "items": [
            {"ac_reg": "9V-TST", "rfo_no": "1200000101", "description": "Task A", "planned_mh": 10.0},
            {"ac_reg": "9V-TST", "description": "Task B", "planned_mh": 5.0},
        ],
    }
    r = await async_client.post("/api/tasks/import/confirm", json=body, headers=CSRF)
    assert r.status_code == 200
    assert r.json()["created_count"] == 2

    # Verify tasks exist
    r2 = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    assert r2.json()["total"] == 2


@pytest.mark.asyncio
async def test_import_confirm_with_supervisor_sets_distributed(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    await _grant_supervisor_access(db, shop_id)

    body = {
        "shop_id": shop_id,
        "meeting_date": "2026-03-10",
        "items": [
            {"ac_reg": "9V-TST", "description": "Assigned task", "assigned_supervisor_id": 2},
        ],
    }
    r = await async_client.post("/api/tasks/import/confirm", json=body, headers=CSRF)
    assert r.status_code == 200

    r2 = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}"
    )
    item = r2.json()["items"][0]
    assert item["assigned_supervisor_id"] == 2
    assert item["distributed_at"] is not None


@pytest.mark.asyncio
async def test_import_confirm_rejects_non_supervisor_assignee(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    body = {
        "shop_id": shop_id,
        "meeting_date": "2026-03-10",
        "items": [
            {"ac_reg": "9V-TST", "description": "Bad assignee", "assigned_supervisor_id": 3},
        ],
    }
    r = await async_client.post("/api/tasks/import/confirm", json=body, headers=CSRF)
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_import_confirm_invalid_aircraft(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)

    body = {
        "shop_id": shop_id,
        "meeting_date": "2026-03-10",
        "items": [
            {"ac_reg": "INVALID", "description": "Bad task"},
        ],
    }
    r = await async_client.post("/api/tasks/import/confirm", json=body, headers=CSRF)
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_import_confirm_audit(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)

    body = {
        "shop_id": shop_id,
        "meeting_date": "2026-03-10",
        "items": [
            {"ac_reg": "9V-TST", "description": "Audit test"},
        ],
    }
    await async_client.post("/api/tasks/import/confirm", json=body, headers=CSRF)

    from sqlalchemy import select
    from app.models.audit import AuditLog

    async with db() as session:
        logs = (await session.execute(
            select(AuditLog).where(AuditLog.action == "IMPORT_CREATE")
        )).scalars().all()
        assert len(logs) >= 1


# ═══════════════════════════════════════════════════════════════════════
# Commit 2: Task Distribution — Assign / Bulk-assign
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_assign_task(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    await _grant_supervisor_access(db, shop_id)
    cr = await _create_task(async_client, shop_id, ac_id)
    task_id = cr.json()["task_id"]

    r = await async_client.post(
        f"/api/tasks/{task_id}/assign",
        json={"assigned_supervisor_id": 2, "shop_id": shop_id},
        headers=CSRF,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["assigned_supervisor_id"] == 2
    assert data["distributed_at"] is not None


@pytest.mark.asyncio
async def test_assign_task_rejects_non_supervisor(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    task_id = cr.json()["task_id"]

    r = await async_client.post(
        f"/api/tasks/{task_id}/assign",
        json={"assigned_supervisor_id": 3, "shop_id": shop_id},
        headers=CSRF,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_assign_task_rejects_supervisor_without_shop_access(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    task_id = cr.json()["task_id"]

    r = await async_client.post(
        f"/api/tasks/{task_id}/assign",
        json={"assigned_supervisor_id": 2, "shop_id": shop_id},
        headers=CSRF,
    )
    assert r.status_code == 403
    assert r.json()["code"] == "SHOP_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_assign_task_shop_id_mismatch(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    await _grant_supervisor_access(db, shop_id)
    cr = await _create_task(async_client, shop_id, ac_id)
    task_id = cr.json()["task_id"]

    r = await async_client.post(
        f"/api/tasks/{task_id}/assign",
        json={"assigned_supervisor_id": 2, "shop_id": shop_id + 999},
        headers=CSRF,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_assign_task_not_found(async_client, db):
    await _seed_shop_and_aircraft(db)
    r = await async_client.post(
        "/api/tasks/99999/assign",
        json={"assigned_supervisor_id": 2, "shop_id": 1},
        headers=CSRF,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_assign_task_audit(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    await _grant_supervisor_access(db, shop_id)
    cr = await _create_task(async_client, shop_id, ac_id)
    task_id = cr.json()["task_id"]

    await async_client.post(
        f"/api/tasks/{task_id}/assign",
        json={"assigned_supervisor_id": 2, "shop_id": shop_id},
        headers=CSRF,
    )

    from sqlalchemy import select
    from app.models.audit import AuditLog

    async with db() as session:
        logs = (await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "task_item",
                AuditLog.entity_id == task_id,
                AuditLog.action == "ASSIGN",
            )
        )).scalars().all()
        assert len(logs) == 1


@pytest.mark.asyncio
async def test_bulk_assign(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    await _grant_supervisor_access(db, shop_id)
    cr1 = await _create_task(async_client, shop_id, ac_id, task_text="Task 1")
    cr2 = await _create_task(async_client, shop_id, ac_id, task_text="Task 2")

    r = await async_client.post(
        "/api/tasks/bulk-assign",
        json={
            "task_ids": [cr1.json()["task_id"], cr2.json()["task_id"]],
            "assigned_supervisor_id": 2,
        },
        headers=CSRF,
    )
    assert r.status_code == 200
    assert r.json()["assigned_count"] == 2

    # Verify both tasks are assigned
    r2 = await async_client.get(
        f"/api/tasks/snapshots?meeting_date=2026-03-10&shop_id={shop_id}&assigned_supervisor_id=2"
    )
    assert r2.json()["total"] == 2


@pytest.mark.asyncio
async def test_bulk_assign_rejects_supervisor_without_shop_access(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    cr1 = await _create_task(async_client, shop_id, ac_id, task_text="Task 1")
    cr2 = await _create_task(async_client, shop_id, ac_id, task_text="Task 2")

    r = await async_client.post(
        "/api/tasks/bulk-assign",
        json={
            "task_ids": [cr1.json()["task_id"], cr2.json()["task_id"]],
            "assigned_supervisor_id": 2,
        },
        headers=CSRF,
    )
    assert r.status_code == 403
    assert r.json()["code"] == "SHOP_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_bulk_assign_missing_task(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)

    r = await async_client.post(
        "/api/tasks/bulk-assign",
        json={
            "task_ids": [cr.json()["task_id"], 99999],
            "assigned_supervisor_id": 2,
        },
        headers=CSRF,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_bulk_assign_non_admin_forbidden(db, sup_client):
    r = await sup_client.post(
        "/api/tasks/bulk-assign",
        json={"task_ids": [1], "assigned_supervisor_id": 2},
        headers=CSRF,
    )
    assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════
# Commit 2: Task Distribution — Assign Worker
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_assign_worker(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    task_id = cr.json()["task_id"]

    # Give worker user_id=3 access to the shop
    from app.models.user_shop_access import UserShopAccess
    async with db() as session:
        session.add(UserShopAccess(user_id=3, shop_id=shop_id, access="VIEW", granted_by=1))
        await session.commit()

    r = await async_client.patch(
        f"/api/tasks/{task_id}/assign-worker",
        json={"assigned_worker_id": 3},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert r.json()["assigned_worker_id"] == 3


@pytest.mark.asyncio
async def test_assign_worker_cross_shop_denied(async_client, db):
    """Worker without shop access gets 403."""
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    task_id = cr.json()["task_id"]

    # worker_id=4 has NO access to this shop
    r = await async_client.patch(
        f"/api/tasks/{task_id}/assign-worker",
        json={"assigned_worker_id": 4},
        headers=CSRF,
    )
    assert r.status_code == 403
    assert r.json()["code"] == "SHOP_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_assign_worker_not_found(async_client, db):
    await _seed_shop_and_aircraft(db)
    r = await async_client.patch(
        "/api/tasks/99999/assign-worker",
        json={"assigned_worker_id": 3},
        headers=CSRF,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_assign_worker_audit(async_client, db):
    shop_id, ac_id, wp_id = await _seed_shop_and_aircraft(db)
    cr = await _create_task(async_client, shop_id, ac_id)
    task_id = cr.json()["task_id"]

    from app.models.user_shop_access import UserShopAccess
    async with db() as session:
        session.add(UserShopAccess(user_id=3, shop_id=shop_id, access="VIEW", granted_by=1))
        await session.commit()

    await async_client.patch(
        f"/api/tasks/{task_id}/assign-worker",
        json={"assigned_worker_id": 3},
        headers=CSRF,
    )

    from sqlalchemy import select
    from app.models.audit import AuditLog

    async with db() as session:
        logs = (await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "task_item",
                AuditLog.entity_id == task_id,
                AuditLog.action == "ASSIGN_WORKER",
            )
        )).scalars().all()
        assert len(logs) == 1
