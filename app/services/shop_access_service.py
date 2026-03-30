"""Shop access service — §6.3 check_shop_access + require_shop_access dependency."""
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.user_shop_access import UserShopAccess
from app.schemas.common import APIError

# MANAGE(3) > EDIT(2) > VIEW(1)
ACCESS_LEVELS = {"VIEW": 1, "EDIT": 2, "MANAGE": 3}
VALID_ACCESS = set(ACCESS_LEVELS.keys())


def _access_level(access: str) -> int:
    return ACCESS_LEVELS.get(access, 0)


async def has_any_shop_access(db: AsyncSession, user: dict) -> bool:
    """Return True when the user can enter task surfaces.

    ADMIN keeps the global bypass. All other roles require at least one
    explicit ``user_shop_access`` row.
    """
    if "ADMIN" in user.get("roles", []):
        return True
    row = (
        await db.execute(
            select(UserShopAccess.id).where(
                UserShopAccess.user_id == user["user_id"]
            ).limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def check_shop_access(
    db: AsyncSession, user: dict, shop_id: int, required: str,
) -> bool:
    """§6.3 pseudocode: ADMIN bypass, else check user_shop_access row.

    Returns True if the user has sufficient access, False otherwise.
    """
    if "ADMIN" in user.get("roles", []):
        return True  # bypass — no user_shop_access row needed
    row = (
        await db.execute(
            select(UserShopAccess).where(
                UserShopAccess.user_id == user["user_id"],
                UserShopAccess.shop_id == shop_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    return _access_level(row.access) >= _access_level(required)


async def enforce_shop_access(
    db: AsyncSession, user: dict, shop_id: int, required: str,
) -> None:
    """Check and raise 403 SHOP_ACCESS_DENIED if insufficient."""
    ok = await check_shop_access(db, user, shop_id, required)
    if not ok:
        raise APIError(403, "Shop access denied", "SHOP_ACCESS_DENIED")


async def enforce_task_surface_access(db: AsyncSession, user: dict) -> None:
    """Raise 403 when the user cannot enter task list / entry surfaces."""
    if not await has_any_shop_access(db, user):
        raise APIError(403, "Shop access denied", "SHOP_ACCESS_DENIED")


def require_shop_access(required: str = "VIEW"):
    """FastAPI dependency factory.

    The endpoint must declare ``shop_id: int`` as a path or query param.
    FastAPI injects the same value into this dependency automatically.

    Usage::

        @router.get("/tasks/snapshots")
        async def list_snapshots(
            shop_id: int = Query(...),
            _access=Depends(require_shop_access("VIEW")),
            db: AsyncSession = Depends(get_db),
        ):
    """

    async def _dep(
        shop_id: int,
        current_user: dict = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> dict:
        await enforce_shop_access(db, current_user, shop_id, required)
        return current_user

    return _dep
