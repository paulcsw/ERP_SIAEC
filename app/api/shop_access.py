"""Shop Access management — ADMIN only (§8.5)."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_role
from app.models.shop import Shop
from app.models.user import User
from app.models.user_shop_access import UserShopAccess
from app.schemas.common import APIError, PaginatedResponse, pagination_params
from app.schemas.shop import ShopAccessCreate, ShopAccessResponse, ShopAccessUpdate
from app.services.audit_service import write_audit
from app.services.shop_access_service import VALID_ACCESS

router = APIRouter(prefix="/api/shop-access", tags=["shop-access"])


def _access_to_dict(a: UserShopAccess) -> dict:
    return {
        "id": a.id,
        "user_id": a.user_id,
        "shop_id": a.shop_id,
        "access": a.access,
        "granted_at": a.granted_at,
        "granted_by": a.granted_by,
    }


def _validate_access(access: str) -> None:
    if access not in VALID_ACCESS:
        raise APIError(
            422,
            f"Invalid access level '{access}'. Must be one of: {', '.join(sorted(VALID_ACCESS))}",
            "VALIDATION_ERROR",
            field="access",
        )


# ── GET /api/shop-access ─────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse[ShopAccessResponse])
async def list_shop_access(
    paging: dict = Depends(pagination_params),
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    total = (
        await db.execute(select(func.count()).select_from(UserShopAccess))
    ).scalar()
    rows = (
        await db.execute(
            select(UserShopAccess)
            .order_by(UserShopAccess.id)
            .offset(paging["offset"])
            .limit(paging["per_page"])
        )
    ).scalars().all()

    return {
        "items": [_access_to_dict(a) for a in rows],
        "total": total,
        "page": paging["page"],
        "per_page": paging["per_page"],
    }


# ── POST /api/shop-access ────────────────────────────────────────────

@router.post("", response_model=ShopAccessResponse, status_code=201)
async def create_shop_access(
    body: ShopAccessCreate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    _validate_access(body.access)

    # FK validation
    user = (
        await db.execute(select(User).where(User.id == body.user_id))
    ).scalar_one_or_none()
    if not user:
        raise APIError(422, "User not found", "VALIDATION_ERROR", field="user_id")

    shop = (
        await db.execute(select(Shop).where(Shop.id == body.shop_id))
    ).scalar_one_or_none()
    if not shop:
        raise APIError(422, "Shop not found", "VALIDATION_ERROR", field="shop_id")

    # Duplicate check
    existing = (
        await db.execute(
            select(UserShopAccess).where(
                UserShopAccess.user_id == body.user_id,
                UserShopAccess.shop_id == body.shop_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise APIError(
            422,
            f"User {body.user_id} already has access to shop {body.shop_id}",
            "VALIDATION_ERROR",
        )

    access = UserShopAccess(
        user_id=body.user_id,
        shop_id=body.shop_id,
        access=body.access,
        granted_by=current_user["user_id"],
    )
    db.add(access)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="user_shop_access",
        entity_id=access.id,
        action="CREATE",
        after=_access_to_dict(access),
    )
    await db.commit()
    await db.refresh(access)

    return _access_to_dict(access)


# ── PATCH /api/shop-access/{access_id} ───────────────────────────────

@router.patch("/{access_id}", response_model=ShopAccessResponse)
async def update_shop_access(
    access_id: int,
    body: ShopAccessUpdate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    _validate_access(body.access)

    row = (
        await db.execute(select(UserShopAccess).where(UserShopAccess.id == access_id))
    ).scalar_one_or_none()
    if not row:
        raise APIError(404, "Shop access not found", "NOT_FOUND")

    before = _access_to_dict(row)
    row.access = body.access
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="user_shop_access",
        entity_id=row.id,
        action="UPDATE",
        before=before,
        after=_access_to_dict(row),
    )
    await db.commit()

    return _access_to_dict(row)


# ── DELETE /api/shop-access/{access_id} ──────────────────────────────

@router.delete("/{access_id}")
async def delete_shop_access(
    access_id: int,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(select(UserShopAccess).where(UserShopAccess.id == access_id))
    ).scalar_one_or_none()
    if not row:
        raise APIError(404, "Shop access not found", "NOT_FOUND")

    before = _access_to_dict(row)
    await db.delete(row)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="user_shop_access",
        entity_id=access_id,
        action="DELETE",
        before=before,
    )
    await db.commit()

    return {"deleted": True, "id": access_id}
