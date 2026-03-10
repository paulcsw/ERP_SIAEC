"""Smoke tests for migration 002 — task schema tables, indexes, FKs, CHECKs.

Uses SQLite in-memory (via conftest.py db fixture) so the tables are created
from SQLAlchemy models (which must match the raw DDL in 002).
"""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import inspect, select, text

from app.models import Base
from app.models.shop import Shop
from app.models.user_shop_access import UserShopAccess
from app.models.task import TaskItem, TaskSnapshot


# ── Table existence ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shops_table_exists(db):
    """shops table is created with correct columns."""
    async with db() as session:
        conn = await session.connection()

        def _inspect(sync_conn):
            insp = inspect(sync_conn)
            cols = {c["name"] for c in insp.get_columns("shops")}
            return cols

        cols = await conn.run_sync(_inspect)
        expected = {"id", "code", "name", "created_at", "updated_at", "created_by"}
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"


@pytest.mark.asyncio
async def test_user_shop_access_table_exists(db):
    """user_shop_access table is created with correct columns."""
    async with db() as session:
        conn = await session.connection()

        def _inspect(sync_conn):
            insp = inspect(sync_conn)
            cols = {c["name"] for c in insp.get_columns("user_shop_access")}
            return cols

        cols = await conn.run_sync(_inspect)
        expected = {"id", "user_id", "shop_id", "access", "granted_at", "granted_by"}
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"


@pytest.mark.asyncio
async def test_task_items_table_exists(db):
    """task_items table is created with all §5.3.3 columns."""
    async with db() as session:
        conn = await session.connection()

        def _inspect(sync_conn):
            insp = inspect(sync_conn)
            cols = {c["name"] for c in insp.get_columns("task_items")}
            return cols

        cols = await conn.run_sync(_inspect)
        expected = {
            "id", "aircraft_id", "shop_id", "work_package_id",
            "assigned_supervisor_id", "assigned_worker_id",
            "distributed_at", "planned_mh", "task_text",
            "is_active", "deactivated_at", "deactivated_by",
            "created_at", "created_by",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"


@pytest.mark.asyncio
async def test_task_snapshots_table_exists(db):
    """task_snapshots table is created with all §5.3.4 columns including supervisor_updated_at."""
    async with db() as session:
        conn = await session.connection()

        def _inspect(sync_conn):
            insp = inspect(sync_conn)
            cols = {c["name"] for c in insp.get_columns("task_snapshots")}
            return cols

        cols = await conn.run_sync(_inspect)
        expected = {
            "id", "task_id", "meeting_date", "status",
            "mh_incurred_hours", "remarks", "critical_issue",
            "has_issue", "deadline_date", "correction_reason",
            "is_deleted", "deleted_at", "deleted_by",
            "version", "supervisor_updated_at",
            "last_updated_at", "last_updated_by", "created_at",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"


# ── CRUD smoke ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shop_crud(db):
    """Can create and query a shop."""
    async with db() as session:
        shop = Shop(code="SHEET_METAL", name="Sheet Metal", created_by=1)
        session.add(shop)
        await session.commit()

        result = (await session.execute(select(Shop).where(Shop.code == "SHEET_METAL"))).scalar_one()
        assert result.name == "Sheet Metal"
        assert result.id is not None


@pytest.mark.asyncio
async def test_user_shop_access_crud(db):
    """Can create user_shop_access with valid access level."""
    async with db() as session:
        shop = Shop(code="FABRIC", name="Fabric", created_by=1)
        session.add(shop)
        await session.flush()

        access = UserShopAccess(
            user_id=2, shop_id=shop.id, access="EDIT", granted_by=1
        )
        session.add(access)
        await session.commit()

        result = (await session.execute(
            select(UserShopAccess).where(UserShopAccess.user_id == 2)
        )).scalar_one()
        assert result.access == "EDIT"
        assert result.shop_id == shop.id


@pytest.mark.asyncio
async def test_user_shop_access_unique_constraint(db):
    """Duplicate (user_id, shop_id) raises IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    async with db() as session:
        shop = Shop(code="PAINT", name="Painting", created_by=1)
        session.add(shop)
        await session.flush()

        a1 = UserShopAccess(user_id=2, shop_id=shop.id, access="VIEW", granted_by=1)
        session.add(a1)
        await session.flush()

        a2 = UserShopAccess(user_id=2, shop_id=shop.id, access="EDIT", granted_by=1)
        session.add(a2)
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_task_item_crud(db):
    """Can create task_item with distribution fields."""
    from app.models.reference import Aircraft

    async with db() as session:
        shop = Shop(code="SHEET_METAL", name="Sheet Metal", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-SMA", airline="SQ")
        session.add(ac)
        await session.flush()

        now = datetime.now(timezone.utc)
        task = TaskItem(
            aircraft_id=ac.id,
            shop_id=shop.id,
            work_package_id=None,
            assigned_supervisor_id=2,
            assigned_worker_id=3,
            distributed_at=now,
            planned_mh=Decimal("10.50"),
            task_text="Replace panel",
            created_by=1,
        )
        session.add(task)
        await session.commit()

        result = (await session.execute(
            select(TaskItem).where(TaskItem.id == task.id)
        )).scalar_one()
        assert result.assigned_supervisor_id == 2
        assert result.assigned_worker_id == 3
        assert result.distributed_at is not None
        assert result.planned_mh == Decimal("10.50")
        assert result.is_active is True


@pytest.mark.asyncio
async def test_task_snapshot_crud(db):
    """Can create snapshot with version, supervisor_updated_at, and all fields."""
    from app.models.reference import Aircraft

    async with db() as session:
        shop = Shop(code="FIBERGLASS", name="Fiberglass", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-SMB", airline="SQ")
        session.add(ac)
        await session.flush()

        task = TaskItem(
            aircraft_id=ac.id, shop_id=shop.id, task_text="Repair crack",
            created_by=1,
        )
        session.add(task)
        await session.flush()

        now = datetime.now(timezone.utc)
        snap = TaskSnapshot(
            task_id=task.id,
            meeting_date=date(2026, 3, 10),
            status="IN_PROGRESS",
            mh_incurred_hours=Decimal("3.50"),
            remarks="On track",
            has_issue=False,
            version=1,
            supervisor_updated_at=now,
            last_updated_by=2,
        )
        session.add(snap)
        await session.commit()

        result = (await session.execute(
            select(TaskSnapshot).where(TaskSnapshot.task_id == task.id)
        )).scalar_one()
        assert result.status == "IN_PROGRESS"
        assert result.mh_incurred_hours == Decimal("3.50")
        assert result.version == 1
        assert result.supervisor_updated_at is not None
        assert result.is_deleted is False


@pytest.mark.asyncio
async def test_task_snapshot_unique_task_meeting(db):
    """Duplicate (task_id, meeting_date) raises IntegrityError."""
    from sqlalchemy.exc import IntegrityError
    from app.models.reference import Aircraft

    async with db() as session:
        shop = Shop(code="SM2", name="Sheet Metal 2", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-SMC", airline="SQ")
        session.add(ac)
        await session.flush()

        task = TaskItem(
            aircraft_id=ac.id, shop_id=shop.id, task_text="Task A", created_by=1,
        )
        session.add(task)
        await session.flush()

        md = date(2026, 3, 10)
        s1 = TaskSnapshot(
            task_id=task.id, meeting_date=md, last_updated_by=1,
        )
        session.add(s1)
        await session.flush()

        s2 = TaskSnapshot(
            task_id=task.id, meeting_date=md, last_updated_by=1,
        )
        session.add(s2)
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_task_snapshot_cascade_delete(db):
    """Deleting task_item cascades to task_snapshots."""
    from app.models.reference import Aircraft

    async with db() as session:
        shop = Shop(code="SM3", name="Sheet Metal 3", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-SMD", airline="SQ")
        session.add(ac)
        await session.flush()

        task = TaskItem(
            aircraft_id=ac.id, shop_id=shop.id, task_text="Task B", created_by=1,
        )
        session.add(task)
        await session.flush()

        snap = TaskSnapshot(
            task_id=task.id, meeting_date=date(2026, 3, 10), last_updated_by=1,
        )
        session.add(snap)
        await session.commit()

        task_id = task.id
        await session.delete(task)
        await session.commit()

    # Use a fresh session to verify cascade
    async with db() as session2:
        remaining = (await session2.execute(
            select(TaskSnapshot).where(TaskSnapshot.task_id == task_id)
        )).scalars().all()
        assert len(remaining) == 0


@pytest.mark.asyncio
async def test_task_item_relationships(db):
    """TaskItem relationships resolve correctly."""
    from app.models.reference import Aircraft

    async with db() as session:
        shop = Shop(code="SM4", name="Sheet Metal 4", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-SME", airline="3P")
        session.add(ac)
        await session.flush()

        task = TaskItem(
            aircraft_id=ac.id, shop_id=shop.id, task_text="Task C",
            assigned_supervisor_id=2, created_by=1,
        )
        session.add(task)
        await session.commit()

        result = (await session.execute(
            select(TaskItem).where(TaskItem.id == task.id)
        )).scalar_one()
        # Lazy-load relationships
        ac_loaded = await session.get(Aircraft, result.aircraft_id)
        assert ac_loaded.ac_reg == "9V-SME"


@pytest.mark.asyncio
async def test_task_item_deactivate_fields(db):
    """Deactivation fields (is_active, deactivated_at, deactivated_by) work."""
    from app.models.reference import Aircraft

    async with db() as session:
        shop = Shop(code="SM5", name="Sheet Metal 5", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-SMF", airline="SQ")
        session.add(ac)
        await session.flush()

        now = datetime.now(timezone.utc)
        task = TaskItem(
            aircraft_id=ac.id, shop_id=shop.id, task_text="Task D",
            is_active=False, deactivated_at=now, deactivated_by=1,
            created_by=1,
        )
        session.add(task)
        await session.commit()

        result = (await session.execute(
            select(TaskItem).where(TaskItem.is_active == False)
        )).scalar_one()
        assert result.deactivated_by == 1
        assert result.deactivated_at is not None


@pytest.mark.asyncio
async def test_snapshot_soft_delete_fields(db):
    """Soft delete fields (is_deleted, deleted_at, deleted_by) work."""
    from app.models.reference import Aircraft

    async with db() as session:
        shop = Shop(code="SM6", name="Sheet Metal 6", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-SMG", airline="SQ")
        session.add(ac)
        await session.flush()

        task = TaskItem(
            aircraft_id=ac.id, shop_id=shop.id, task_text="Task E", created_by=1,
        )
        session.add(task)
        await session.flush()

        now = datetime.now(timezone.utc)
        snap = TaskSnapshot(
            task_id=task.id, meeting_date=date(2026, 3, 10),
            is_deleted=True, deleted_at=now, deleted_by=1,
            last_updated_by=1,
        )
        session.add(snap)
        await session.commit()

        result = (await session.execute(
            select(TaskSnapshot).where(TaskSnapshot.is_deleted == True)
        )).scalar_one()
        assert result.deleted_by == 1


@pytest.mark.asyncio
async def test_snapshot_version_default(db):
    """Snapshot version defaults to 1."""
    from app.models.reference import Aircraft

    async with db() as session:
        shop = Shop(code="SM7", name="Sheet Metal 7", created_by=1)
        session.add(shop)
        await session.flush()

        ac = Aircraft(ac_reg="9V-SMH", airline="SQ")
        session.add(ac)
        await session.flush()

        task = TaskItem(
            aircraft_id=ac.id, shop_id=shop.id, task_text="Task F", created_by=1,
        )
        session.add(task)
        await session.flush()

        snap = TaskSnapshot(
            task_id=task.id, meeting_date=date(2026, 3, 10), last_updated_by=1,
        )
        session.add(snap)
        await session.commit()

        result = (await session.execute(
            select(TaskSnapshot).where(TaskSnapshot.id == snap.id)
        )).scalar_one()
        assert result.version == 1
        assert result.status == "NOT_STARTED"
        assert result.mh_incurred_hours == Decimal("0")
        assert result.has_issue is False
