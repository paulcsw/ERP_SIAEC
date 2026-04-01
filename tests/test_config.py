"""Tests for Config API (Branch 03, §8.9)."""
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import Settings
from app.models.audit import AuditLog
from app.models.system_config import SystemConfig
from app.services.week_advancement_service import (
    calculate_due_week_start,
    maybe_auto_advance_working_week,
)
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
        session.add(SystemConfig(
            key="meeting_auto_advance", value="manual",
            updated_at=datetime.now(timezone.utc),
        ))
        session.add(SystemConfig(
            key="snapshot_advance_day", value="monday",
            updated_at=datetime.now(timezone.utc),
        ))
        session.add(SystemConfig(
            key="snapshot_advance_time", value="00:00",
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
    assert "meeting_auto_advance" in keys
    assert "snapshot_advance_day" in keys
    assert "snapshot_advance_time" in keys


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


async def test_update_auto_week_configs(async_client, db):
    await _seed_config(db)

    resp = await async_client.patch(
        "/api/config",
        json={
            "configs": [
                {"key": "meeting_auto_advance", "value": "scheduled"},
                {"key": "snapshot_advance_day", "value": "friday"},
                {"key": "snapshot_advance_time", "value": "18:30"},
            ]
        },
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 3

    get_resp = await async_client.get("/api/config")
    configs = {c["key"]: c["value"] for c in get_resp.json()["configs"]}
    assert configs["meeting_auto_advance"] == "scheduled"
    assert configs["snapshot_advance_day"] == "friday"
    assert configs["snapshot_advance_time"] == "18:30"


async def test_update_auto_week_configs_bootstraps_missing_rows(async_client, db):
    async with db() as session:
        session.add(SystemConfig(
            key="meeting_current_date", value="2026-02-26",
            updated_at=datetime.now(timezone.utc),
        ))
        await session.commit()

    resp = await async_client.patch(
        "/api/config",
        json={
            "configs": [
                {"key": "meeting_auto_advance", "value": "scheduled"},
                {"key": "snapshot_advance_day", "value": "friday"},
                {"key": "snapshot_advance_time", "value": "18:30"},
            ]
        },
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 3

    get_resp = await async_client.get("/api/config")
    configs = {c["key"]: c["value"] for c in get_resp.json()["configs"]}
    assert configs["meeting_auto_advance"] == "scheduled"
    assert configs["snapshot_advance_day"] == "friday"
    assert configs["snapshot_advance_time"] == "18:30"


async def test_update_unknown_key(async_client, db):
    await _seed_config(db)

    resp = await async_client.patch(
        "/api/config",
        json={"configs": [{"key": "nonexistent_key", "value": "x"}]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_update_invalid_threshold_rejected(async_client, db):
    await _seed_config(db)

    resp = await async_client.patch(
        "/api/config",
        json={"configs": [{"key": "needs_update_threshold_hours", "value": "abc"}]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert resp.json()["field"] == "needs_update_threshold_hours"


async def test_update_out_of_range_threshold_rejected(async_client, db):
    await _seed_config(db)

    resp = await async_client.patch(
        "/api/config",
        json={"configs": [{"key": "needs_update_threshold_hours", "value": "721"}]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["field"] == "needs_update_threshold_hours"


async def test_update_invalid_meeting_date_rejected(async_client, db):
    await _seed_config(db)

    resp = await async_client.patch(
        "/api/config",
        json={"configs": [{"key": "meeting_current_date", "value": "2026-02-30"}]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["field"] == "meeting_current_date"


async def test_update_invalid_boolean_rejected(async_client, db):
    await _seed_config(db)

    resp = await async_client.patch(
        "/api/config",
        json={"configs": [{"key": "teams_enabled", "value": "yes"}]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["field"] == "teams_enabled"


async def test_update_invalid_auto_mode_rejected(async_client, db):
    await _seed_config(db)

    resp = await async_client.patch(
        "/api/config",
        json={"configs": [{"key": "meeting_auto_advance", "value": "every_monday"}]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["field"] == "meeting_auto_advance"


async def test_update_invalid_auto_day_rejected(async_client, db):
    await _seed_config(db)

    resp = await async_client.patch(
        "/api/config",
        json={"configs": [{"key": "snapshot_advance_day", "value": "weekday"}]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["field"] == "snapshot_advance_day"


async def test_update_invalid_auto_time_rejected(async_client, db):
    await _seed_config(db)

    resp = await async_client.patch(
        "/api/config",
        json={"configs": [{"key": "snapshot_advance_time", "value": "25:00"}]},
        headers=CSRF_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["field"] == "snapshot_advance_time"


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


def test_settings_accepts_release_as_non_debug():
    cfg = Settings(_env_file=None, DEBUG="release")
    assert cfg.DEBUG is False


def test_settings_accepts_development_as_debug():
    cfg = Settings(_env_file=None, DEBUG="development")
    assert cfg.DEBUG is True


def test_calculate_due_week_start_for_friday_evening_advances_to_next_monday():
    now = datetime(2026, 4, 10, 11, 0, tzinfo=timezone.utc)  # 19:00 Asia/Singapore
    assert calculate_due_week_start(
        now,
        advance_day="friday",
        advance_time="18:00",
    ).isoformat() == "2026-04-13"


def test_calculate_due_week_start_for_monday_noon_before_trigger_keeps_previous_week():
    now = datetime(2026, 4, 13, 3, 0, tzinfo=timezone.utc)  # 11:00 Asia/Singapore
    assert calculate_due_week_start(
        now,
        advance_day="monday",
        advance_time="12:00",
    ).isoformat() == "2026-04-06"


async def test_auto_week_advancement_noop_when_manual(db):
    await _seed_config(db)

    async with db() as session:
        result = await maybe_auto_advance_working_week(
            session,
            now=datetime(2026, 4, 10, 11, 0, tzinfo=timezone.utc),
        )

    assert result["updated"] is False
    assert result["reason"] == "manual_mode"


async def test_auto_week_advancement_jumps_to_latest_due_week_and_audits(db):
    await _seed_config(db)

    async with db() as session:
        configs = (
            await session.execute(
                select(SystemConfig).where(
                    SystemConfig.key.in_(
                        [
                            "meeting_current_date",
                            "meeting_auto_advance",
                            "snapshot_advance_day",
                            "snapshot_advance_time",
                        ]
                    )
                )
            )
        ).scalars().all()
        config_map = {row.key: row for row in configs}
        config_map["meeting_current_date"].value = "2026-03-30"
        config_map["meeting_auto_advance"].value = "scheduled"
        config_map["snapshot_advance_day"].value = "friday"
        config_map["snapshot_advance_time"].value = "18:00"
        await session.commit()

    async with db() as session:
        result = await maybe_auto_advance_working_week(
            session,
            now=datetime(2026, 4, 10, 11, 0, tzinfo=timezone.utc),
        )

    assert result["updated"] is True
    assert result["current"] == "2026-03-30"
    assert result["target"] == "2026-04-13"

    async with db() as session:
        meeting = (
            await session.execute(
                select(SystemConfig).where(SystemConfig.key == "meeting_current_date")
            )
        ).scalar_one()
        assert meeting.value == "2026-04-13"
        assert meeting.updated_by is None

        logs = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "system_config",
                    AuditLog.action == "AUTO_ADVANCE",
                )
            )
        ).scalars().all()
        assert len(logs) == 1
        assert logs[0].actor_id is None


async def test_auto_week_advancement_second_run_is_noop(db):
    await _seed_config(db)

    async with db() as session:
        configs = (
            await session.execute(
                select(SystemConfig).where(
                    SystemConfig.key.in_(
                        [
                            "meeting_current_date",
                            "meeting_auto_advance",
                            "snapshot_advance_day",
                            "snapshot_advance_time",
                        ]
                    )
                )
            )
        ).scalars().all()
        config_map = {row.key: row for row in configs}
        config_map["meeting_current_date"].value = "2026-03-30"
        config_map["meeting_auto_advance"].value = "scheduled"
        config_map["snapshot_advance_day"].value = "friday"
        config_map["snapshot_advance_time"].value = "18:00"
        await session.commit()

    async with db() as session:
        first = await maybe_auto_advance_working_week(
            session,
            now=datetime(2026, 4, 10, 11, 0, tzinfo=timezone.utc),
        )

    async with db() as session:
        second = await maybe_auto_advance_working_week(
            session,
            now=datetime(2026, 4, 10, 11, 0, tzinfo=timezone.utc),
        )

    assert first["updated"] is True
    assert second["updated"] is False
    assert second["reason"] == "already_current"
