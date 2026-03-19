"""Tests for authentication, CSRF, and rate limiting (Branch 02 DoD)."""
from app.middleware.rate_limit import MAX_REQUESTS, reset_rate_limits


async def test_unauthenticated_api_returns_401(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "AUTH_REQUIRED"
    assert "detail" in body


async def test_health_no_auth_required(client):
    """Health check must work without authentication."""
    # Health check talks to DB which is not available in tests,
    # but the route itself should not return 401.
    # If DB is unavailable it will be a 500, not 401.
    resp = await client.get("/health")
    assert resp.status_code != 401


async def test_me_returns_user_info(auth_client):
    resp = await auth_client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == 1
    assert body["employee_no"] == "E001"
    assert body["display_name"] == "Test Admin"
    assert body["roles"] == ["ADMIN"]
    assert body["team"] == "Sheet Metal"


async def test_csrf_missing_returns_403(auth_client):
    """POST without X-CSRFToken header returns 403 CSRF_INVALID."""
    resp = await auth_client.post("/logout")
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "CSRF_INVALID"


async def test_csrf_wrong_token_returns_403(auth_client):
    """POST with wrong CSRF token returns 403 CSRF_INVALID."""
    resp = await auth_client.post("/logout", headers={"X-CSRFToken": "wrong-token"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "CSRF_INVALID"


async def test_csrf_valid_passes(auth_client):
    """POST with correct X-CSRFToken should pass CSRF check."""
    resp = await auth_client.post(
        "/logout",
        headers={"X-CSRFToken": "test-csrf-token-abc123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302


async def test_csrf_skipped_for_get(auth_client):
    """GET requests should not require CSRF token."""
    resp = await auth_client.get("/api/auth/me")
    assert resp.status_code == 200


async def test_rate_limit_exceeded_returns_429(client):
    """After MAX_REQUESTS, next request should return 429."""
    reset_rate_limits()
    for _ in range(MAX_REQUESTS):
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401

    resp = await client.get("/api/auth/me")
    assert resp.status_code == 429
    body = resp.json()
    assert body["code"] == "RATE_LIMIT"
    assert "detail" in body
