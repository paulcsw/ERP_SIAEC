"""Shared SSR context helpers."""
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.shop_access_service import has_any_shop_access


async def build_task_access_context(db: AsyncSession, current_user: dict) -> dict[str, bool]:
    """Return task-surface access flags for base navigation rendering."""
    return {"has_task_access": await has_any_shop_access(db, current_user)}


def build_href(path: str, **params) -> str:
    """Build a path with only non-empty query parameters."""
    filtered = {
        key: value
        for key, value in params.items()
        if value is not None and value != ""
    }
    if not filtered:
        return path
    return f"{path}?{urlencode(filtered)}"
