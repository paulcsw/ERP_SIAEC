"""Shared test fixtures."""
import base64
import json

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.config import settings
from app.main import app
from app.middleware.rate_limit import reset_rate_limits


def _make_session_cookie(data: dict) -> str:
    """Create a signed session cookie matching Starlette SessionMiddleware."""
    signer = TimestampSigner(str(settings.SECRET_KEY))
    payload = base64.b64encode(json.dumps(data).encode("utf-8"))
    return signer.sign(payload).decode("utf-8")


@pytest.fixture()
def client():
    """Unauthenticated test client."""
    reset_rate_limits()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def auth_client():
    """Authenticated test client (ADMIN) with session + CSRF token."""
    reset_rate_limits()
    c = TestClient(app, raise_server_exceptions=False)
    session_data = {
        "user_id": 1,
        "employee_no": "E001",
        "display_name": "Test Admin",
        "roles": ["ADMIN"],
        "team": "Sheet Metal",
        "csrf_token": "test-csrf-token-abc123",
    }
    cookie_value = _make_session_cookie(session_data)
    c.cookies.set("session", cookie_value)
    return c
