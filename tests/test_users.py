"""Tests for Users CRUD."""
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import CSRF_HEADERS, _make_session_cookie


@asynccontextmanager
async def _session_client(*, user_id: int, employee_no: str, display_name: str, roles: list[str], team: str = "Sheet Metal"):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.cookies.set(
            "session",
            _make_session_cookie(
                {
                    "user_id": user_id,
                    "employee_no": employee_no,
                    "display_name": display_name,
                    "roles": roles,
                    "team": team,
                    "csrf_token": "test-csrf-token-abc123",
                }
            ),
        )
        yield c


async def test_list_users(async_client):
    resp = await async_client.get("/api/users")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert body["page"] == 1
    names = [u["employee_no"] for u in body["items"]]
    assert "E001" in names


async def test_list_users_requires_admin(async_anon_client):
    resp = await async_anon_client.get("/api/users")
    assert resp.status_code == 401


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


async def test_create_user_requires_at_least_one_role(async_client):
    resp = await async_client.post(
        "/api/users",
        json={"employee_no": "E101", "name": "No Role", "roles": []},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert resp.json()["field"] == "roles"


async def test_update_user(async_client):
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


async def test_update_user_multi_role_and_clear_email(async_client):
    create = await async_client.post(
        "/api/users",
        json={
            "employee_no": "E154",
            "name": "Combo User",
            "email": "combo@test.com",
            "roles": ["WORKER"],
        },
        headers=CSRF_HEADERS,
    )
    uid = create.json()["id"]

    resp = await async_client.patch(
        f"/api/users/{uid}",
        json={"roles": ["SUPERVISOR", "ADMIN"], "email": None},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["roles"] == ["ADMIN", "SUPERVISOR"]
    assert body["email"] is None


async def test_update_user_requires_at_least_one_role(async_client):
    create = await async_client.post(
        "/api/users",
        json={"employee_no": "E155", "name": "Role Target", "roles": ["WORKER"]},
        headers=CSRF_HEADERS,
    )
    uid = create.json()["id"]

    resp = await async_client.patch(
        f"/api/users/{uid}",
        json={"roles": []},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert resp.json()["field"] == "roles"


async def test_admin_cannot_deactivate_self_via_patch(async_client):
    resp = await async_client.patch(
        "/api/users/1",
        json={"is_active": False},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "SELF_LOCKOUT_FORBIDDEN"


async def test_admin_cannot_remove_own_admin_role(async_client):
    resp = await async_client.patch(
        "/api/users/1",
        json={"roles": ["SUPERVISOR"]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "SELF_LOCKOUT_FORBIDDEN"


async def test_update_user_not_found(async_client):
    resp = await async_client.patch(
        "/api/users/9999",
        json={"name": "X"},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_deactivate_and_reactivate(async_client):
    create = await async_client.post(
        "/api/users",
        json={"employee_no": "E060", "name": "Deact Test", "roles": ["WORKER"]},
        headers=CSRF_HEADERS,
    )
    uid = create.json()["id"]

    resp = await async_client.patch(
        f"/api/users/{uid}/deactivate", headers=CSRF_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    resp = await async_client.patch(
        f"/api/users/{uid}/reactivate", headers=CSRF_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


async def test_admin_cannot_deactivate_self_via_endpoint(async_client):
    resp = await async_client.patch("/api/users/1/deactivate", headers=CSRF_HEADERS)
    assert resp.status_code == 422
    assert resp.json()["code"] == "SELF_LOCKOUT_FORBIDDEN"


async def test_last_active_admin_cannot_be_deactivated(async_client):
    second_admin = await async_client.post(
        "/api/users",
        json={"employee_no": "E061", "name": "Admin Two", "roles": ["ADMIN"]},
        headers=CSRF_HEADERS,
    )
    second_admin_id = second_admin.json()["id"]

    async with _session_client(
        user_id=second_admin_id,
        employee_no="E061",
        display_name="Admin Two",
        roles=["ADMIN"],
    ) as second_admin_client:
        first_resp = await second_admin_client.patch("/api/users/1/deactivate", headers=CSRF_HEADERS)
        assert first_resp.status_code == 200

    resp = await async_client.patch(f"/api/users/{second_admin_id}/deactivate", headers=CSRF_HEADERS)
    assert resp.status_code == 422
    assert resp.json()["code"] == "LAST_ACTIVE_ADMIN"


async def test_last_active_admin_role_cannot_be_removed(async_client):
    second_admin = await async_client.post(
        "/api/users",
        json={"employee_no": "E062", "name": "Admin Three", "roles": ["ADMIN"]},
        headers=CSRF_HEADERS,
    )
    second_admin_id = second_admin.json()["id"]

    async with _session_client(
        user_id=second_admin_id,
        employee_no="E062",
        display_name="Admin Three",
        roles=["ADMIN"],
    ) as second_admin_client:
        first_resp = await second_admin_client.patch(
            "/api/users/1",
            json={"roles": ["WORKER"]},
            headers=CSRF_HEADERS,
        )
        assert first_resp.status_code == 200

    resp = await async_client.patch(
        f"/api/users/{second_admin_id}",
        json={"roles": ["SUPERVISOR"]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "LAST_ACTIVE_ADMIN"


async def test_delete_user_no_references(async_client):
    create = await async_client.post(
        "/api/users",
        json={"employee_no": "E070", "name": "Delete Me", "roles": ["WORKER"]},
        headers=CSRF_HEADERS,
    )
    uid = create.json()["id"]

    resp = await async_client.delete(
        f"/api/users/{uid}", headers=CSRF_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert resp.json()["user_id"] == uid

    resp2 = await async_client.get("/api/users")
    ids = [u["id"] for u in resp2.json()["items"]]
    assert uid not in ids


async def test_delete_user_not_found(async_client):
    resp = await async_client.delete(
        "/api/users/9999", headers=CSRF_HEADERS
    )
    assert resp.status_code == 404
