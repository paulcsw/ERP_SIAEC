"""Shared test fixtures."""
import base64
import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import BigInteger, event as sa_event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.config import settings

# BigInteger → INTEGER for SQLite (enables autoincrement on PK columns)
@compiles(BigInteger, "sqlite")
def _compile_big_int_sqlite(type_, compiler, **kw):
    return "INTEGER"
from app.main import app
from app.middleware.rate_limit import reset_rate_limits
from app.models import Base
from app.models.user import Role, User

# ── Helpers ─────────────────────────────────────────────────────────

CSRF_HEADERS = {"X-CSRFToken": "test-csrf-token-abc123"}


def _make_session_cookie(data: dict) -> str:
    """Create a signed session cookie matching Starlette SessionMiddleware."""
    signer = TimestampSigner(str(settings.SECRET_KEY))
    payload = base64.b64encode(json.dumps(data).encode("utf-8"))
    return signer.sign(payload).decode("utf-8")


_ADMIN_SESSION = {
    "user_id": 1,
    "employee_no": "E001",
    "display_name": "Test Admin",
    "roles": ["ADMIN"],
    "team": "Sheet Metal",
    "csrf_token": "test-csrf-token-abc123",
}


# ── Sync fixtures (test_auth.py, no DB needed) ─────────────────────

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
    c.cookies.set("session", _make_session_cookie(_ADMIN_SESSION))
    return c


# ── Async DB engine (SQLite in-memory for tests) ───────────────────

_test_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@sa_event.listens_for(_test_engine.sync_engine, "connect")
def _register_sqlite_functions(dbapi_connection, connection_record):
    """Register MSSQL-compatible functions for SQLite."""
    raw = getattr(dbapi_connection, "driver_connection", dbapi_connection)
    raw = getattr(raw, "_conn", raw)
    raw.create_function(
        "GETUTCDATE", 0,
        lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )


_TestSessionFactory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _override_get_db():
    async with _TestSessionFactory() as session:
        yield session


# ── Async DB fixtures ──────────────────────────────────────────────

@pytest_asyncio.fixture
async def db():
    """Create tables, seed roles + admin, yield, then drop tables."""
    from app.api.deps import get_db as _deps_get_db

    app.dependency_overrides[_deps_get_db] = _override_get_db

    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed roles and admin user (user_id auto-assigned)
    async with _TestSessionFactory() as session:
        for rn in ("WORKER", "SUPERVISOR", "ADMIN"):
            session.add(Role(name=rn))
        await session.flush()
        admin_role = (
            await session.execute(select(Role).where(Role.name == "ADMIN"))
        ).scalar_one()
        admin = User(
            employee_no="E001",
            name="Test Admin",
            email="admin@test.com",
            team="Sheet Metal",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        admin.roles = [admin_role]
        session.add(admin)
        await session.commit()

    yield _TestSessionFactory

    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    app.dependency_overrides.pop(_deps_get_db, None)


@pytest_asyncio.fixture
async def async_client(db):
    """Authenticated ADMIN async client with DB backend."""
    reset_rate_limits()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.cookies.set("session", _make_session_cookie(_ADMIN_SESSION))
        yield c


@pytest_asyncio.fixture
async def async_anon_client(db):
    """Unauthenticated async client with DB backend."""
    reset_rate_limits()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
