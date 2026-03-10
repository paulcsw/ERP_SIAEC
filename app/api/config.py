"""Config API — Admin Settings (§8.9)."""
from datetime import datetime, timezone

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

# Keys that SUPERVISOR+ can read (§8.9.3)
SUPERVISOR_READABLE_KEYS = {"meeting_current_date"}


# ── GET /api/config — list all configs (ADMIN) ─────────────────────

@router.get("", response_model=ConfigListResponse)
async def list_configs(
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(select(SystemConfig).order_by(SystemConfig.key))
    ).scalars().all()
    return {
        "configs": [
            ConfigItem(key=r.key, value=r.value, updated_at=r.updated_at)
            for r in rows
        ]
    }


# ── PATCH /api/config — batch update (ADMIN) ───────────────────────

@router.patch("", response_model=ConfigUpdateResponse)
async def update_configs(
    body: ConfigBatchUpdate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    updated = 0
    for item in body.configs:
        row = (
            await db.execute(
                select(SystemConfig).where(SystemConfig.key == item.key)
            )
        ).scalar_one_or_none()
        if not row:
            raise APIError(404, f"Config key '{item.key}' not found", "NOT_FOUND")

        before_val = row.value
        row.value = item.value
        row.updated_by = current_user["user_id"]
        row.updated_at = datetime.now(timezone.utc)
        await db.flush()

        await write_audit(
            db,
            actor_id=current_user["user_id"],
            entity_type="system_config",
            entity_id=row.id,
            action="UPDATE",
            before={"key": row.key, "value": before_val},
            after={"key": row.key, "value": item.value},
        )
        updated += 1

    await db.commit()
    return {"updated": updated}


# ── GET /api/config/{key} — single config ──────────────────────────

@router.get("/{key}", response_model=ConfigSingleResponse)
async def get_config(
    key: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Auth check: SUPERVISOR+ for meeting_current_date, ADMIN for others
    user_roles = set(current_user.get("roles", []))
    if key not in SUPERVISOR_READABLE_KEYS:
        if "ADMIN" not in user_roles:
            raise APIError(403, "Insufficient permissions", "FORBIDDEN")

    row = (
        await db.execute(
            select(SystemConfig).where(SystemConfig.key == key)
        )
    ).scalar_one_or_none()
    if not row:
        raise APIError(404, f"Config key '{key}' not found", "NOT_FOUND")

    return {"key": row.key, "value": row.value}
