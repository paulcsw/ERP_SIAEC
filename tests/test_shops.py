"""Tests for Branch 06 — Shop CRUD, Shop Access, check_shop_access (§6.3, §8.5)."""
import pytest

CSRF = {"X-CSRFToken": "test-csrf-token-abc123"}


# ═══════════════════════════════════════════════════════════════════════
# Shop CRUD (commit 1)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_shop(async_client):
    r = await async_client.post(
        "/api/shops", json={"code": "SHEET_METAL", "name": "Sheet Metal"}, headers=CSRF,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["code"] == "SHEET_METAL"
    assert data["name"] == "Sheet Metal"
    assert data["id"] is not None


@pytest.mark.asyncio
async def test_create_shop_trims_code_and_name(async_client):
    r = await async_client.post(
        "/api/shops", json={"code": "  TRIM  ", "name": "  Trim Shop  "}, headers=CSRF,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["code"] == "TRIM"
    assert data["name"] == "Trim Shop"


@pytest.mark.asyncio
async def test_create_shop_rejects_blank_code(async_client):
    r = await async_client.post(
        "/api/shops", json={"code": "   ", "name": "Valid Name"}, headers=CSRF,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert r.json()["field"] == "code"


@pytest.mark.asyncio
async def test_create_shop_rejects_blank_name(async_client):
    r = await async_client.post(
        "/api/shops", json={"code": "BLANK", "name": "   "}, headers=CSRF,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert r.json()["field"] == "name"


@pytest.mark.asyncio
async def test_create_shop_duplicate_code(async_client):
    await async_client.post(
        "/api/shops", json={"code": "DUP", "name": "First"}, headers=CSRF,
    )
    r = await async_client.post(
        "/api/shops", json={"code": "DUP", "name": "Second"}, headers=CSRF,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_list_shops(async_client):
    await async_client.post(
        "/api/shops", json={"code": "S1", "name": "Shop 1"}, headers=CSRF,
    )
    await async_client.post(
        "/api/shops", json={"code": "S2", "name": "Shop 2"}, headers=CSRF,
    )
    r = await async_client.get("/api/shops")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 2
    assert len(data["items"]) >= 2


@pytest.mark.asyncio
async def test_update_shop(async_client):
    r = await async_client.post(
        "/api/shops", json={"code": "UPD", "name": "Before"}, headers=CSRF,
    )
    shop_id = r.json()["id"]

    r2 = await async_client.patch(
        f"/api/shops/{shop_id}", json={"name": "After"}, headers=CSRF,
    )
    assert r2.status_code == 200
    assert r2.json()["name"] == "After"
    assert r2.json()["code"] == "UPD"


@pytest.mark.asyncio
async def test_update_shop_trims_name(async_client):
    r = await async_client.post(
        "/api/shops", json={"code": "TRIMUPD", "name": "Before"}, headers=CSRF,
    )
    shop_id = r.json()["id"]

    r2 = await async_client.patch(
        f"/api/shops/{shop_id}", json={"name": "  After Trim  "}, headers=CSRF,
    )
    assert r2.status_code == 200
    assert r2.json()["name"] == "After Trim"


@pytest.mark.asyncio
async def test_update_shop_rejects_blank_name(async_client):
    r = await async_client.post(
        "/api/shops", json={"code": "NONBLANK", "name": "Before"}, headers=CSRF,
    )
    shop_id = r.json()["id"]

    r2 = await async_client.patch(
        f"/api/shops/{shop_id}", json={"name": "   "}, headers=CSRF,
    )
    assert r2.status_code == 422
    assert r2.json()["code"] == "VALIDATION_ERROR"
    assert r2.json()["field"] == "name"


@pytest.mark.asyncio
async def test_update_shop_code_is_immutable(async_client):
    r = await async_client.post(
        "/api/shops", json={"code": "IMMUT", "name": "Before"}, headers=CSRF,
    )
    shop_id = r.json()["id"]

    r2 = await async_client.patch(
        f"/api/shops/{shop_id}", json={"code": "RENAMED"}, headers=CSRF,
    )
    assert r2.status_code == 422
    assert r2.json()["code"] == "VALIDATION_ERROR"
    assert r2.json()["field"] == "code"


@pytest.mark.asyncio
async def test_update_shop_code_is_immutable_even_if_same_value(async_client):
    r = await async_client.post(
        "/api/shops", json={"code": "IMMUT2", "name": "Before"}, headers=CSRF,
    )
    shop_id = r.json()["id"]

    r2 = await async_client.patch(
        f"/api/shops/{shop_id}", json={"code": "IMMUT2"}, headers=CSRF,
    )
    assert r2.status_code == 422
    assert r2.json()["code"] == "VALIDATION_ERROR"
    assert r2.json()["field"] == "code"


@pytest.mark.asyncio
async def test_update_shop_not_found(async_client):
    r = await async_client.patch(
        "/api/shops/9999", json={"name": "Nope"}, headers=CSRF,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_shop_crud_audit_logged(async_client, db):
    """Create + update should produce audit_logs entries."""
    r = await async_client.post(
        "/api/shops", json={"code": "AUD", "name": "Audit Shop"}, headers=CSRF,
    )
    shop_id = r.json()["id"]
    await async_client.patch(
        f"/api/shops/{shop_id}", json={"name": "Updated"}, headers=CSRF,
    )

    from sqlalchemy import select
    from app.models.audit import AuditLog

    async with db() as session:
        logs = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "shop",
                    AuditLog.entity_id == shop_id,
                )
            )
        ).scalars().all()
        actions = [log.action for log in logs]
        assert "CREATE" in actions
        assert "UPDATE" in actions


@pytest.mark.asyncio
async def test_shop_crud_forbidden_for_worker(worker_client):
    r = await worker_client.post(
        "/api/shops", json={"code": "X", "name": "X"}, headers=CSRF,
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_shop_crud_forbidden_for_supervisor(sup_client):
    r = await sup_client.post(
        "/api/shops", json={"code": "X", "name": "X"}, headers=CSRF,
    )
    assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════
# Shop Access (commit 2)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_shop_access(async_client):
    shop = (await async_client.post(
        "/api/shops", json={"code": "ACC1", "name": "Access Shop"}, headers=CSRF,
    )).json()

    r = await async_client.post(
        "/api/shop-access",
        json={"user_id": 2, "shop_id": shop["id"], "access": "EDIT"},
        headers=CSRF,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["user_id"] == 2
    assert data["shop_id"] == shop["id"]
    assert data["access"] == "EDIT"
    assert data["granted_by"] == 1  # admin


@pytest.mark.asyncio
async def test_create_shop_access_invalid_level(async_client):
    shop = (await async_client.post(
        "/api/shops", json={"code": "ACC2", "name": "Access Shop 2"}, headers=CSRF,
    )).json()

    r = await async_client.post(
        "/api/shop-access",
        json={"user_id": 2, "shop_id": shop["id"], "access": "SUPERADMIN"},
        headers=CSRF,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_create_shop_access_rejects_admin_target(async_client):
    shop = (await async_client.post(
        "/api/shops", json={"code": "ACC2A", "name": "Admin Reject Shop"}, headers=CSRF,
    )).json()

    r = await async_client.post(
        "/api/shop-access",
        json={"user_id": 1, "shop_id": shop["id"], "access": "VIEW"},
        headers=CSRF,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert r.json()["field"] == "user_id"


@pytest.mark.asyncio
async def test_create_shop_access_rejects_inactive_target(async_client, db):
    from sqlalchemy import select

    from app.models.user import User

    shop = (await async_client.post(
        "/api/shops", json={"code": "ACC2B", "name": "Inactive Reject Shop"}, headers=CSRF,
    )).json()

    async with db() as session:
        user = (await session.execute(select(User).where(User.id == 4))).scalar_one()
        user.is_active = False
        await session.commit()

    r = await async_client.post(
        "/api/shop-access",
        json={"user_id": 4, "shop_id": shop["id"], "access": "VIEW"},
        headers=CSRF,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert r.json()["field"] == "user_id"


@pytest.mark.asyncio
async def test_create_shop_access_duplicate(async_client):
    shop = (await async_client.post(
        "/api/shops", json={"code": "ACC3", "name": "Access Shop 3"}, headers=CSRF,
    )).json()

    await async_client.post(
        "/api/shop-access",
        json={"user_id": 2, "shop_id": shop["id"], "access": "VIEW"},
        headers=CSRF,
    )
    r = await async_client.post(
        "/api/shop-access",
        json={"user_id": 2, "shop_id": shop["id"], "access": "EDIT"},
        headers=CSRF,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_shop_access(async_client):
    shop = (await async_client.post(
        "/api/shops", json={"code": "ACC4", "name": "List Shop"}, headers=CSRF,
    )).json()
    await async_client.post(
        "/api/shop-access",
        json={"user_id": 2, "shop_id": shop["id"], "access": "VIEW"},
        headers=CSRF,
    )

    r = await async_client.get("/api/shop-access")
    assert r.status_code == 200
    assert r.json()["total"] >= 1


@pytest.mark.asyncio
async def test_update_shop_access(async_client):
    shop = (await async_client.post(
        "/api/shops", json={"code": "ACC5", "name": "Update Shop"}, headers=CSRF,
    )).json()
    access = (await async_client.post(
        "/api/shop-access",
        json={"user_id": 2, "shop_id": shop["id"], "access": "VIEW"},
        headers=CSRF,
    )).json()

    r = await async_client.patch(
        f"/api/shop-access/{access['id']}",
        json={"access": "MANAGE"},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert r.json()["access"] == "MANAGE"


@pytest.mark.asyncio
async def test_update_shop_access_rejects_admin_target_row(async_client, db):
    from app.models.shop import Shop
    from app.models.user_shop_access import UserShopAccess

    async with db() as session:
        shop = Shop(code="ACC5A", name="Legacy Admin Shop", created_by=1)
        session.add(shop)
        await session.flush()
        row = UserShopAccess(user_id=1, shop_id=shop.id, access="VIEW", granted_by=1)
        session.add(row)
        await session.commit()
        access_id = row.id

    r = await async_client.patch(
        f"/api/shop-access/{access_id}",
        json={"access": "MANAGE"},
        headers=CSRF,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert r.json()["field"] == "user_id"


@pytest.mark.asyncio
async def test_update_shop_access_rejects_inactive_target_row(async_client, db):
    from sqlalchemy import select

    from app.models.shop import Shop
    from app.models.user import User
    from app.models.user_shop_access import UserShopAccess

    async with db() as session:
        user = (await session.execute(select(User).where(User.id == 4))).scalar_one()
        user.is_active = False
        shop = Shop(code="ACC5B", name="Legacy Inactive Shop", created_by=1)
        session.add(shop)
        await session.flush()
        row = UserShopAccess(user_id=4, shop_id=shop.id, access="VIEW", granted_by=1)
        session.add(row)
        await session.commit()
        access_id = row.id

    r = await async_client.patch(
        f"/api/shop-access/{access_id}",
        json={"access": "MANAGE"},
        headers=CSRF,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert r.json()["field"] == "user_id"


@pytest.mark.asyncio
async def test_delete_shop_access(async_client):
    shop = (await async_client.post(
        "/api/shops", json={"code": "ACC6", "name": "Delete Shop"}, headers=CSRF,
    )).json()
    access = (await async_client.post(
        "/api/shop-access",
        json={"user_id": 3, "shop_id": shop["id"], "access": "VIEW"},
        headers=CSRF,
    )).json()

    r = await async_client.delete(
        f"/api/shop-access/{access['id']}", headers=CSRF,
    )
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    # Verify deleted
    r2 = await async_client.delete(
        f"/api/shop-access/{access['id']}", headers=CSRF,
    )
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_delete_shop_access_allows_legacy_admin_and_inactive_rows(async_client, db):
    from sqlalchemy import select

    from app.models.shop import Shop
    from app.models.user import User
    from app.models.user_shop_access import UserShopAccess

    async with db() as session:
        inactive_user = (await session.execute(select(User).where(User.id == 4))).scalar_one()
        inactive_user.is_active = False
        admin_shop = Shop(code="ACC6A", name="Legacy Admin Delete", created_by=1)
        inactive_shop = Shop(code="ACC6B", name="Legacy Inactive Delete", created_by=1)
        session.add_all([admin_shop, inactive_shop])
        await session.flush()
        admin_row = UserShopAccess(user_id=1, shop_id=admin_shop.id, access="VIEW", granted_by=1)
        inactive_row = UserShopAccess(user_id=4, shop_id=inactive_shop.id, access="VIEW", granted_by=1)
        session.add_all([admin_row, inactive_row])
        await session.commit()
        admin_access_id = admin_row.id
        inactive_access_id = inactive_row.id

    admin_delete = await async_client.delete(f"/api/shop-access/{admin_access_id}", headers=CSRF)
    inactive_delete = await async_client.delete(f"/api/shop-access/{inactive_access_id}", headers=CSRF)
    assert admin_delete.status_code == 200
    assert inactive_delete.status_code == 200


@pytest.mark.asyncio
async def test_shop_access_audit_logged(async_client, db):
    shop = (await async_client.post(
        "/api/shops", json={"code": "ACC7", "name": "Audit Access"}, headers=CSRF,
    )).json()
    access = (await async_client.post(
        "/api/shop-access",
        json={"user_id": 2, "shop_id": shop["id"], "access": "VIEW"},
        headers=CSRF,
    )).json()

    await async_client.patch(
        f"/api/shop-access/{access['id']}",
        json={"access": "EDIT"},
        headers=CSRF,
    )
    await async_client.delete(
        f"/api/shop-access/{access['id']}", headers=CSRF,
    )

    from sqlalchemy import select
    from app.models.audit import AuditLog

    async with db() as session:
        logs = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "user_shop_access",
                    AuditLog.entity_id == access["id"],
                )
            )
        ).scalars().all()
        actions = sorted(log.action for log in logs)
        assert actions == ["CREATE", "DELETE", "UPDATE"]


@pytest.mark.asyncio
async def test_shop_access_forbidden_for_worker(worker_client):
    r = await worker_client.get("/api/shop-access")
    assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════
# check_shop_access / require_shop_access (commit 3)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_admin_bypass_no_access_row(db):
    """ADMIN can access any shop without user_shop_access row."""
    from app.services.shop_access_service import check_shop_access
    from app.models.shop import Shop

    async with db() as session:
        shop = Shop(code="BYPASS", name="Bypass Shop", created_by=1)
        session.add(shop)
        await session.commit()

        admin_user = {"user_id": 1, "roles": ["ADMIN"]}
        result = await check_shop_access(session, admin_user, shop.id, "MANAGE")
        assert result is True


@pytest.mark.asyncio
async def test_view_user_has_view_access(db):
    """User with VIEW access can pass VIEW check."""
    from app.services.shop_access_service import check_shop_access
    from app.models.shop import Shop
    from app.models.user_shop_access import UserShopAccess

    async with db() as session:
        shop = Shop(code="VIEW_SHOP", name="View Shop", created_by=1)
        session.add(shop)
        await session.flush()

        access = UserShopAccess(
            user_id=3, shop_id=shop.id, access="VIEW", granted_by=1,
        )
        session.add(access)
        await session.commit()

        worker = {"user_id": 3, "roles": ["WORKER"]}
        assert await check_shop_access(session, worker, shop.id, "VIEW") is True


@pytest.mark.asyncio
async def test_view_user_cannot_edit(db):
    """User with VIEW access fails EDIT check."""
    from app.services.shop_access_service import check_shop_access
    from app.models.shop import Shop
    from app.models.user_shop_access import UserShopAccess

    async with db() as session:
        shop = Shop(code="NOEDIT", name="No Edit Shop", created_by=1)
        session.add(shop)
        await session.flush()

        access = UserShopAccess(
            user_id=3, shop_id=shop.id, access="VIEW", granted_by=1,
        )
        session.add(access)
        await session.commit()

        worker = {"user_id": 3, "roles": ["WORKER"]}
        assert await check_shop_access(session, worker, shop.id, "EDIT") is False


@pytest.mark.asyncio
async def test_edit_user_can_view_and_edit(db):
    """User with EDIT access passes both VIEW and EDIT checks."""
    from app.services.shop_access_service import check_shop_access
    from app.models.shop import Shop
    from app.models.user_shop_access import UserShopAccess

    async with db() as session:
        shop = Shop(code="EDIT_SHOP", name="Edit Shop", created_by=1)
        session.add(shop)
        await session.flush()

        access = UserShopAccess(
            user_id=3, shop_id=shop.id, access="EDIT", granted_by=1,
        )
        session.add(access)
        await session.commit()

        worker = {"user_id": 3, "roles": ["WORKER"]}
        assert await check_shop_access(session, worker, shop.id, "VIEW") is True
        assert await check_shop_access(session, worker, shop.id, "EDIT") is True
        assert await check_shop_access(session, worker, shop.id, "MANAGE") is False


@pytest.mark.asyncio
async def test_manage_user_has_full_access(db):
    """User with MANAGE access passes all checks."""
    from app.services.shop_access_service import check_shop_access
    from app.models.shop import Shop
    from app.models.user_shop_access import UserShopAccess

    async with db() as session:
        shop = Shop(code="MANAGE_SHOP", name="Manage Shop", created_by=1)
        session.add(shop)
        await session.flush()

        access = UserShopAccess(
            user_id=2, shop_id=shop.id, access="MANAGE", granted_by=1,
        )
        session.add(access)
        await session.commit()

        sup = {"user_id": 2, "roles": ["SUPERVISOR"]}
        assert await check_shop_access(session, sup, shop.id, "VIEW") is True
        assert await check_shop_access(session, sup, shop.id, "EDIT") is True
        assert await check_shop_access(session, sup, shop.id, "MANAGE") is True


@pytest.mark.asyncio
async def test_no_access_row_denied(db):
    """User with no user_shop_access row is denied."""
    from app.services.shop_access_service import check_shop_access
    from app.models.shop import Shop

    async with db() as session:
        shop = Shop(code="NOACC", name="No Access Shop", created_by=1)
        session.add(shop)
        await session.commit()

        worker = {"user_id": 3, "roles": ["WORKER"]}
        assert await check_shop_access(session, worker, shop.id, "VIEW") is False


@pytest.mark.asyncio
async def test_enforce_shop_access_raises_403(db):
    """enforce_shop_access raises 403 SHOP_ACCESS_DENIED."""
    from app.services.shop_access_service import enforce_shop_access
    from app.models.shop import Shop
    from app.schemas.common import APIError

    async with db() as session:
        shop = Shop(code="DENY", name="Denied Shop", created_by=1)
        session.add(shop)
        await session.commit()

        worker = {"user_id": 3, "roles": ["WORKER"]}
        with pytest.raises(APIError) as exc_info:
            await enforce_shop_access(session, worker, shop.id, "VIEW")
        assert exc_info.value.code == "SHOP_ACCESS_DENIED"
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_enforce_shop_access_admin_pass(db):
    """enforce_shop_access passes for ADMIN without access row."""
    from app.services.shop_access_service import enforce_shop_access
    from app.models.shop import Shop

    async with db() as session:
        shop = Shop(code="ADMPASS", name="Admin Pass", created_by=1)
        session.add(shop)
        await session.commit()

        admin = {"user_id": 1, "roles": ["ADMIN"]}
        # Should not raise
        await enforce_shop_access(session, admin, shop.id, "MANAGE")
