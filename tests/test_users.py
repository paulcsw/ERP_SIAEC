"""Tests for Users CRUD (Branch 03, §8.8)."""
import pytest

from tests.conftest import CSRF_HEADERS


# ── GET /api/users ──────────────────────────────────────────────────

async def test_list_users(async_client):
    resp = await async_client.get("/api/users")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert body["page"] == 1
    # Seeded admin should be present
    names = [u["employee_no"] for u in body["items"]]
    assert "E001" in names


async def test_list_users_requires_admin(async_anon_client):
    resp = await async_anon_client.get("/api/users")
    assert resp.status_code == 401


# ── POST /api/users ─────────────────────────────────────────────────

async def test_create_user(async_client):
    resp = await async_client.post(
        "/api/users",
        json={"employee_no": "E099", "name": "New Worker", "roles": ["WORKER"]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["employee_no"] == "E099"
    assert body["name"] == "New Worker"
    assert body["roles"] == ["WORKER"]
    assert body["is_active"] is True


async def test_create_user_duplicate_employee_no(async_client):
    resp = await async_client.post(
        "/api/users",
        json={"employee_no": "E001", "name": "Dup"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert resp.json()["field"] == "employee_no"


async def test_create_user_unknown_role(async_client):
    resp = await async_client.post(
        "/api/users",
        json={"employee_no": "E100", "name": "X", "roles": ["INVALID"]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["field"] == "roles"


# ── PATCH /api/users/{id} ───────────────────────────────────────────

async def test_update_user(async_client):
    # Create a user first
    create = await async_client.post(
        "/api/users",
        json={"employee_no": "E050", "name": "Old Name", "roles": ["WORKER"]},
        headers=CSRF_HEADERS,
    )
    uid = create.json()["id"]

    resp = await async_client.patch(
        f"/api/users/{uid}",
        json={"name": "New Name", "team": "Airframe"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "New Name"
    assert body["team"] == "Airframe"


async def test_update_user_employee_no(async_client):
    create = await async_client.post(
        "/api/users",
        json={"employee_no": "E051", "name": "EmpNo Target", "roles": ["WORKER"]},
        headers=CSRF_HEADERS,
    )
    uid = create.json()["id"]

    resp = await async_client.patch(
        f"/api/users/{uid}",
        json={"employee_no": "E151"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["employee_no"] == "E151"


async def test_update_user_employee_no_duplicate(async_client):
    u1 = await async_client.post(
        "/api/users",
        json={"employee_no": "E152", "name": "U1", "roles": ["WORKER"]},
        headers=CSRF_HEADERS,
    )
    u2 = await async_client.post(
        "/api/users",
        json={"employee_no": "E153", "name": "U2", "roles": ["WORKER"]},
        headers=CSRF_HEADERS,
    )

    resp = await async_client.patch(
        f"/api/users/{u2.json()['id']}",
        json={"employee_no": u1.json()["employee_no"]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert resp.json()["field"] == "employee_no"


async def test_update_user_not_found(async_client):
    resp = await async_client.patch(
        "/api/users/9999",
        json={"name": "X"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


# ── PATCH deactivate / reactivate ──────────────────────────────────

async def test_deactivate_and_reactivate(async_client):
    create = await async_client.post(
        "/api/users",
        json={"employee_no": "E060", "name": "Deact Test"},
        headers=CSRF_HEADERS,
    )
    uid = create.json()["id"]

    # Deactivate
    resp = await async_client.patch(
        f"/api/users/{uid}/deactivate", headers=CSRF_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Reactivate
    resp = await async_client.patch(
        f"/api/users/{uid}/reactivate", headers=CSRF_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


# ── DELETE /api/users/{id} — hard delete ────────────────────────────

async def test_delete_user_no_references(async_client):
    """User with 0 references can be hard-deleted."""
    create = await async_client.post(
        "/api/users",
        json={"employee_no": "E070", "name": "Delete Me"},
        headers=CSRF_HEADERS,
    )
    uid = create.json()["id"]

    resp = await async_client.delete(
        f"/api/users/{uid}", headers=CSRF_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert resp.json()["user_id"] == uid

    # Verify gone
    resp2 = await async_client.get("/api/users")
    ids = [u["id"] for u in resp2.json()["items"]]
    assert uid not in ids


async def test_delete_user_not_found(async_client):
    resp = await async_client.delete(
        "/api/users/9999", headers=CSRF_HEADERS
    )
    assert resp.status_code == 404
