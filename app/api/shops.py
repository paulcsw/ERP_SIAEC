"""Shop CRUD — ADMIN only (§8.5)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_role
from app.models.shop import Shop
from app.schemas.common import APIError, PaginatedResponse, pagination_params
from app.schemas.shop import ShopCreate, ShopResponse, ShopUpdate
from app.services.audit_service import write_audit

router = APIRouter(prefix="/api/shops", tags=["shops"])


def _shop_to_dict(shop: Shop) -> dict:
    return {
        "id": shop.id,
        "code": shop.code,
        "name": shop.name,
        "created_at": shop.created_at,
        "updated_at": shop.updated_at,
        "created_by": shop.created_by,
    }


# ── GET /api/shops ────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse[ShopResponse])
async def list_shops(
    paging: dict = Depends(pagination_params),
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    total = (await db.execute(select(func.count()).select_from(Shop))).scalar()
    rows = (
        await db.execute(
            select(Shop)
            .order_by(Shop.id)
            .offset(paging["offset"])
            .limit(paging["per_page"])
        )
    ).scalars().all()

    return {
        "items": [_shop_to_dict(s) for s in rows],
        "total": total,
        "page": paging["page"],
        "per_page": paging["per_page"],
    }


# ── POST /api/shops ───────────────────────────────────────────────────

@router.post("", response_model=ShopResponse, status_code=201)
async def create_shop(
    body: ShopCreate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(select(Shop).where(Shop.code == body.code))
    ).scalar_one_or_none()
    if existing:
        raise APIError(
            422, f"Shop code '{body.code}' already exists",
            "VALIDATION_ERROR", field="code",
        )

    shop = Shop(
        code=body.code,
        name=body.name,
        created_by=current_user["user_id"],
    )
    db.add(shop)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="shop",
        entity_id=shop.id,
        action="CREATE",
        after=_shop_to_dict(shop),
    )
    await db.commit()
    await db.refresh(shop)

    return _shop_to_dict(shop)


# ── PATCH /api/shops/{shop_id} ────────────────────────────────────────

@router.patch("/{shop_id}", response_model=ShopResponse)
async def update_shop(
    shop_id: int,
    body: ShopUpdate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    shop = (
        await db.execute(select(Shop).where(Shop.id == shop_id))
    ).scalar_one_or_none()
    if not shop:
        raise APIError(404, "Shop not found", "NOT_FOUND")

    before = _shop_to_dict(shop)

    if body.code is not None:
        dup = (
            await db.execute(
                select(Shop).where(Shop.code == body.code, Shop.id != shop_id)
            )
        ).scalar_one_or_none()
        if dup:
            raise APIError(
                422, f"Shop code '{body.code}' already exists",
                "VALIDATION_ERROR", field="code",
            )
        shop.code = body.code
    if body.name is not None:
        shop.name = body.name

    shop.updated_at = datetime.now(timezone.utc)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="shop",
        entity_id=shop.id,
        action="UPDATE",
        before=before,
        after=_shop_to_dict(shop),
    )
    await db.commit()

    return _shop_to_dict(shop)
