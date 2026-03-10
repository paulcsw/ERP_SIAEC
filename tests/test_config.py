"""Tests for Config API (Branch 03, §8.9)."""
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.system_config import SystemConfig
from tests.conftest import CSRF_HEADERS


async def _seed_config(db):
    """Insert a few config rows for testing."""
    async with db() as session:
        session.add(SystemConfig(
            key="meeting_current_date", value="2026-02-26",
            updated_at=datetime.now(timezone.utc),
        ))
        session.add(SystemConfig(
            key="teams_enabled", value="true",
            updated_at=datetime.now(timezone.utc),
        ))
        session.add(SystemConfig(
            key="needs_update_threshold_hours", value="48",
            updated_at=datetime.now(timezone.utc),
        ))
        await session.commit()


# ── GET /api/config ─────────────────────────────────────────────────

async def test_list_configs(async_client, db):
    await _seed_config(db)

    resp = await async_client.get("/api/config")
    assert resp.status_code == 200
    keys = [c["key"] for c in resp.json()["configs"]]
    assert "meeting_current_date" in keys
    assert "teams_enabled" in keys


# ── PATCH /api/config ───────────────────────────────────────────────

async def test_update_configs(async_client, db):
    await _seed_config(db)

    resp = await async_client.patch(
        "/api/config",
        json={
            "configs": [
                {"key": "teams_enabled", "value": "false"},
                {"key": "needs_update_threshold_hours", "value": "72"},
            ]
        },
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 2

    # Verify updated
    get_resp = await async_client.get("/api/config")
    configs = {c["key"]: c["value"] for c in get_resp.json()["configs"]}
    assert configs["teams_enabled"] == "false"
    assert configs["needs_update_threshold_hours"] == "72"


async def test_update_unknown_key(async_client, db):
    await _seed_config(db)

    resp = await async_client.patch(
        "/api/config",
        json={"configs": [{"key": "nonexistent_key", "value": "x"}]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


# ── GET /api/config/{key} ──────────────────────────────────────────

async def test_get_single_config(async_client, db):
    await _seed_config(db)

    resp = await async_client.get("/api/config/meeting_current_date")
    assert resp.status_code == 200
    assert resp.json()["key"] == "meeting_current_date"
    assert resp.json()["value"] == "2026-02-26"


async def test_get_config_unknown_key(async_client, db):
    resp = await async_client.get("/api/config/unknown_key")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"
