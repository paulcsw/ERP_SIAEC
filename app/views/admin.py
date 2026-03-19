"""Admin SSR views ??Users, Reference, Shops, Shop Access."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, require_role
from app.models.reference import Aircraft, ShopStream, WorkPackage
from app.models.shop import Shop
from app.models.user import User
from app.models.user_shop_access import UserShopAccess
from app.views import templates

router = APIRouter(tags=["admin-views"])


def _ctx(request, user, **kw):
    page = kw.pop("page", "admin")
    return {
        "request": request,
        "current_user": {
            "user_id": user["user_id"],
            "display_name": user.get("display_name", ""),
            "roles": user.get("roles", []),
            "team": user.get("team"),
            "employee_no": user.get("employee_no", ""),
        },
        "active_tab": "admin",
        "page": page,
        **kw,
    }


# ?€?€ GET /admin/users ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€

@router.get("/admin/users")
async def admin_users_page(
    request: Request,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    users = (await db.execute(
        select(User).options(selectinload(User.roles)).order_by(User.id)
    )).scalars().all()

    user_list = []
    for u in users:
        user_list.append({
            "id": u.id,
            "employee_no": u.employee_no,
            "name": u.name,
            "email": u.email or "",
            "team": u.team or "",
            "is_active": u.is_active,
            "roles": sorted(r.name for r in u.roles),
        })

    # Distinct teams for filter/select
    teams = sorted(set(u["team"] for u in user_list if u["team"]))

    return templates.TemplateResponse(request, "admin/users.html", _ctx(
        request, current_user,
        page="users",
        users=user_list,
        teams=teams,
    ))


# ?€?€ GET /admin/reference ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€

@router.get("/admin/reference")
async def admin_reference_page(
    request: Request,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    aircraft_rows = (await db.execute(
        select(Aircraft).order_by(Aircraft.id)
    )).scalars().all()
    aircraft = [{
        "id": a.id, "ac_reg": a.ac_reg, "airline": a.airline or "",
        "status": a.status,
    } for a in aircraft_rows]

    wp_rows = (await db.execute(
        select(WorkPackage, Aircraft)
        .outerjoin(Aircraft, WorkPackage.aircraft_id == Aircraft.id)
        .order_by(WorkPackage.id)
    )).all()
    work_packages = [{
        "id": wp.id, "rfo_no": wp.rfo_no or f"WP-{wp.id}",
        "title": wp.title, "ac_reg": ac.ac_reg if ac else "",
        "start_date": wp.start_date.isoformat() if wp.start_date else "",
        "end_date": wp.end_date.isoformat() if wp.end_date else "",
        "priority": wp.priority or 0, "status": wp.status,
    } for wp, ac in wp_rows]

    ss_rows = (await db.execute(
        select(ShopStream, WorkPackage)
        .outerjoin(WorkPackage, ShopStream.work_package_id == WorkPackage.id)
        .order_by(ShopStream.id)
    )).all()
    shop_streams = [{
        "id": ss.id, "shop_code": ss.shop_code,
        "rfo_no": wp.rfo_no if wp else f"WP-{ss.work_package_id}",
        "status": ss.status,
    } for ss, wp in ss_rows]

    return templates.TemplateResponse(request, "admin/reference.html", _ctx(
        request, current_user,
        page="reference",
        aircraft=aircraft,
        work_packages=work_packages,
        shop_streams=shop_streams,
    ))


# ?€?€ GET /admin/shops ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€

@router.get("/admin/shops")
async def admin_shops_page(
    request: Request,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(Shop).order_by(Shop.id))).scalars().all()
    shops = [{
        "id": s.id, "code": s.code, "name": s.name,
    } for s in rows]

    return templates.TemplateResponse(request, "admin/shops.html", _ctx(
        request, current_user,
        page="shops",
        shops=shops,
    ))


# ?€?€ GET /admin/shop-access ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€

@router.get("/admin/shop-access")
async def admin_shop_access_page(
    request: Request,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(UserShopAccess, User, Shop)
        .join(User, UserShopAccess.user_id == User.id)
        .join(Shop, UserShopAccess.shop_id == Shop.id)
        .order_by(UserShopAccess.id)
    )).all()
    access_list = [{
        "id": a.id, "user_name": u.name, "employee_no": u.employee_no,
        "shop_code": s.code, "shop_name": s.name, "access": a.access,
    } for a, u, s in rows]

    # Users & shops for add modal
    users = (await db.execute(
        select(User).where(User.is_active == True).order_by(User.name)
    )).scalars().all()
    shops = (await db.execute(select(Shop).order_by(Shop.code))).scalars().all()

    return templates.TemplateResponse(request, "admin/shop_access.html", _ctx(
        request, current_user,
        page="shop_access",
        access_list=access_list,
        users=[{"id": u.id, "name": u.name, "employee_no": u.employee_no} for u in users],
        shops=[{"id": s.id, "code": s.code, "name": s.name} for s in shops],
    ))
