"""Audit logging helper (§0.2 Principle 3)."""
import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def write_audit(
    db: AsyncSession,
    *,
    actor_id: int,
    entity_type: str,
    entity_id: int,
    action: str,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """Insert a row into audit_logs."""
    log = AuditLog(
        actor_id=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        before_json=json.dumps(before, default=str) if before else None,
        after_json=json.dumps(after, default=str) if after else None,
    )
    db.add(log)
