"""Shared SSR context helpers."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.shop_access_service import has_any_shop_access


async def build_task_access_context(db: AsyncSession, current_user: dict) -> dict[str, bool]:
    """Return task-surface access flags for base navigation rendering."""
    return {"has_task_access": await has_any_shop_access(db, current_user)}
