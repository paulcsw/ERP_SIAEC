"""Config API for admin settings."""
from datetime import date, datetime, timezone
import re

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_role
from app.models.system_config import SystemConfig
from app.schemas.common import APIError
from app.schemas.system_config import (
    ConfigBatchUpdate,
    ConfigItem,
    ConfigListResponse,
    ConfigSingleResponse,
    ConfigUpdateResponse,
)
from app.services.audit_service import write_audit

router = APIRouter(prefix="/api/config", tags=["config"])

SUPERVISOR_READABLE_KEYS = {"meeting_current_date"}
BOOLEAN_CONFIG_KEYS = {"teams_enabled", "outlook_enabled", "critical_alert_enabled"}
INTEGER_CONFIG_RANGES = {"needs_update_threshold_hours": (1, 720)}
DATE_CONFIG_KEYS = {"meeting_current_date"}


def _normalize_free_text(value: str | None) -> str:
    if value is None:
        return ""
    return "" if value.strip() == "" else value


def _validate_config_value(key: str, value: str | None) -> str:
    raw = value if value is not None else ""
    stripped = raw.strip()

    if key in BOOLEAN_CONFIG_KEYS:
        if stripped not in {"true", "false"}:
            raise APIError(
                422,
                f"Config '{key}' must be 'true' or 'false'",
                "VALIDATION_ERROR",
                field=key,
            )
        return stripped

    if key in INTEGER_CONFIG_RANGES:
        if not stripped:
            raise APIError(
                422,
                f"Config '{key}' requires a whole number",
                "VALIDATION_ERROR",
                field=key,
            )
        try:
            parsed = int(stripped)
        except ValueError as exc:
            raise APIError(
                422,
                f"Config '{key}' requires a whole number",
                "VALIDATION_ERROR",
                field=key,
            ) from exc
        min_value, max_value = INTEGER_CONFIG_RANGES[key]
        if parsed < min_value or parsed > max_value:
            raise APIError(
                422,
                f"Config '{key}' must be between {min_value} and {max_value}",
                "VALIDATION_ERROR",
                field=key,
            )
        return str(parsed)

    if key in DATE_CONFIG_KEYS:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
            raise APIError(
                422,
                f"Config '{key}' must use YYYY-MM-DD format",
                "VALIDATION_ERROR",
                field=key,
            )
        try:
            parsed_date = date.fromisoformat(stripped)
        except ValueError as exc:
            raise APIError(
                422,
                f"Config '{key}' must be a valid calendar date",
                "VALIDATION_ERROR",
                field=key,
            ) from exc
        return parsed_date.isoformat()

    return _normalize_free_text(raw)


@router.get("", response_model=ConfigListResponse)
async def list_configs(
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(SystemConfig).order_by(SystemConfig.key))).scalars().all()
    return {
        "configs": [
            ConfigItem(key=row.key, value=row.value, updated_at=row.updated_at)
            for row in rows
        ]
    }


@router.patch("", response_model=ConfigUpdateResponse)
async def update_configs(
    body: ConfigBatchUpdate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    updated = 0
    for item in body.configs:
        row = (
            await db.execute(select(SystemConfig).where(SystemConfig.key == item.key))
        ).scalar_one_or_none()
        if not row:
            raise APIError(404, f"Config key '{item.key}' not found", "NOT_FOUND")

        normalized_value = _validate_config_value(item.key, item.value)
        before_value = row.value
        row.value = normalized_value
        row.updated_by = current_user["user_id"]
        row.updated_at = datetime.now(timezone.utc)
        await db.flush()

        await write_audit(
            db,
            actor_id=current_user["user_id"],
            entity_type="system_config",
            entity_id=row.id,
            action="UPDATE",
            before={"key": row.key, "value": before_value},
            after={"key": row.key, "value": normalized_value},
        )
        updated += 1

    await db.commit()
    return {"updated": updated}


@router.get("/{key}", response_model=ConfigSingleResponse)
async def get_config(
    key: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_roles = set(current_user.get("roles", []))
    if key not in SUPERVISOR_READABLE_KEYS and "ADMIN" not in user_roles:
        raise APIError(403, "Insufficient permissions", "FORBIDDEN")

    row = (
        await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    ).scalar_one_or_none()
    if not row:
        raise APIError(404, f"Config key '{key}' not found", "NOT_FOUND")

    return {"key": row.key, "value": row.value}
