"""Automatic working-week advancement helpers and scheduler loop."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session_factory
from app.models.system_config import SystemConfig
from app.services.audit_service import write_audit

logger = logging.getLogger(__name__)

AUTO_ADVANCE_MANUAL = "manual"
AUTO_ADVANCE_SCHEDULED = "scheduled"
AUTO_ADVANCE_DAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
AUTO_ADVANCE_TIMEZONE = ZoneInfo("Asia/Singapore")
AUTO_ADVANCE_POLL_SECONDS = 60

_DAY_INDEX = {day: idx for idx, day in enumerate(AUTO_ADVANCE_DAYS)}


def normalize_auto_advance_mode(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized == AUTO_ADVANCE_SCHEDULED:
        return AUTO_ADVANCE_SCHEDULED
    return AUTO_ADVANCE_MANUAL


def normalize_auto_advance_day(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in _DAY_INDEX else "monday"


def normalize_auto_advance_time(value: str | None) -> str:
    normalized = (value or "").strip()
    try:
        parsed = time.fromisoformat(normalized)
    except ValueError:
        return "00:00"
    return parsed.strftime("%H:%M")


def _parse_meeting_date(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _to_local_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(AUTO_ADVANCE_TIMEZONE)


def calculate_due_week_start(
    now: datetime | None,
    *,
    advance_day: str,
    advance_time: str,
) -> date:
    now_local = _to_local_now(now)
    normalized_day = normalize_auto_advance_day(advance_day)
    normalized_time = normalize_auto_advance_time(advance_time)
    trigger_time = time.fromisoformat(normalized_time)

    current_week_monday = now_local.date() - timedelta(days=now_local.weekday())
    candidate_date = current_week_monday + timedelta(days=_DAY_INDEX[normalized_day])
    candidate_trigger = datetime.combine(
        candidate_date,
        trigger_time,
        tzinfo=AUTO_ADVANCE_TIMEZONE,
    )
    if candidate_trigger > now_local:
        candidate_trigger -= timedelta(days=7)

    trigger_week_monday = candidate_trigger.date() - timedelta(days=candidate_trigger.weekday())
    if normalized_day == "monday":
        return trigger_week_monday
    return trigger_week_monday + timedelta(days=7)


async def maybe_auto_advance_working_week(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict:
    rows = (
        await db.execute(
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
    config_map = {row.key: row for row in rows}

    meeting_row = config_map.get("meeting_current_date")
    if not meeting_row:
        return {"updated": False, "reason": "missing_meeting_current_date"}

    mode = normalize_auto_advance_mode(
        config_map.get("meeting_auto_advance").value if config_map.get("meeting_auto_advance") else None
    )
    if mode != AUTO_ADVANCE_SCHEDULED:
        return {"updated": False, "reason": "manual_mode"}

    current_meeting_date = _parse_meeting_date(meeting_row.value)
    if current_meeting_date is None:
        return {"updated": False, "reason": "invalid_meeting_current_date"}

    target_week_start = calculate_due_week_start(
        now,
        advance_day=config_map.get("snapshot_advance_day").value if config_map.get("snapshot_advance_day") else None,
        advance_time=config_map.get("snapshot_advance_time").value if config_map.get("snapshot_advance_time") else None,
    )
    target_value = target_week_start.isoformat()
    if target_week_start <= current_meeting_date:
        return {
            "updated": False,
            "reason": "already_current",
            "current": current_meeting_date.isoformat(),
            "target": target_value,
        }

    before_value = meeting_row.value
    result = await db.execute(
        update(SystemConfig)
        .where(
            SystemConfig.id == meeting_row.id,
            SystemConfig.value == before_value,
        )
        .values(
            value=target_value,
            updated_by=None,
            updated_at=datetime.now(timezone.utc),
        )
    )
    if result.rowcount != 1:
        await db.rollback()
        return {
            "updated": False,
            "reason": "lost_race",
            "current": before_value,
            "target": target_value,
        }

    await write_audit(
        db,
        actor_id=None,
        entity_type="system_config",
        entity_id=meeting_row.id,
        action="AUTO_ADVANCE",
        before={"key": meeting_row.key, "value": before_value},
        after={"key": meeting_row.key, "value": target_value},
    )
    await db.commit()
    return {
        "updated": True,
        "current": before_value,
        "target": target_value,
    }


async def run_auto_week_advancement_loop(*, stop_event: asyncio.Event) -> None:
    session_factory = get_session_factory()

    while not stop_event.is_set():
        try:
            async with session_factory() as session:
                await maybe_auto_advance_working_week(session)
        except Exception:
            logger.exception("Automatic week advancement loop failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=AUTO_ADVANCE_POLL_SECONDS)
        except asyncio.TimeoutError:
            continue
