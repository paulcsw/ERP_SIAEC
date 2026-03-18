"""Users CRUD — ADMIN only (§8.8)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, require_role
from app.models.audit import AuditLog
from app.models.ot import OtApproval, OtRequest
from app.models.user import Role, User, user_roles
from app.schemas.common import APIError, PaginatedResponse, pagination_params
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services.audit_service import write_audit

router = APIRouter(prefix="/api/users", tags=["users"])


def _user_to_response(user: User) -> dict:
    """Convert User ORM object to serialisable dict."""
    return {
        "id": user.id,
        "employee_no": user.employee_no,
        "name": user.name,
        "email": user.email,
        "team": user.team,
        "is_active": user.is_active,
        "roles": sorted(r.name for r in user.roles),
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


# ── GET /api/users ─────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse[UserResponse])
async def list_users(
    paging: dict = Depends(pagination_params),
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    total = (
        await db.execute(select(func.count()).select_from(User))
    ).scalar()

    users = (
        await db.execute(
            select(User)
            .options(selectinload(User.roles))
            .order_by(User.id)
            .offset(paging["offset"])
            .limit(paging["per_page"])
        )
    ).scalars().all()

    return {
        "items": [_user_to_response(u) for u in users],
        "total": total,
        "page": paging["page"],
        "per_page": paging["per_page"],
    }


# ── POST /api/users ────────────────────────────────────────────────

@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    # Duplicate employee_no check
    existing = (
        await db.execute(select(User).where(User.employee_no == body.employee_no))
    ).scalar_one_or_none()
    if existing:
        raise APIError(
            422,
            f"employee_no '{body.employee_no}' already exists",
            "VALIDATION_ERROR",
            field="employee_no",
        )

    # Duplicate email check
    if body.email:
        existing_email = (
            await db.execute(select(User).where(User.email == body.email))
        ).scalar_one_or_none()
        if existing_email:
            raise APIError(
                422,
                f"Email '{body.email}' already exists",
                "VALIDATION_ERROR",
                field="email",
            )

    # Resolve role names → Role objects
    role_objects = []
    for role_name in body.roles:
        role = (
            await db.execute(select(Role).where(Role.name == role_name))
        ).scalar_one_or_none()
        if not role:
            raise APIError(
                422, f"Unknown role: {role_name}", "VALIDATION_ERROR", field="roles"
            )
        role_objects.append(role)

    user = User(
        employee_no=body.employee_no,
        name=body.name,
        email=body.email,
        team=body.team,
    )
    user.roles = role_objects
    db.add(user)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="user",
        entity_id=user.id,
        action="CREATE",
        after=_user_to_response(user),
    )
    await db.commit()
    await db.refresh(user, attribute_names=["roles"])

    return _user_to_response(user)


# ── PATCH /api/users/{user_id} ─────────────────────────────────────

@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    body: UserUpdate,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    user = (
        await db.execute(
            select(User).options(selectinload(User.roles)).where(User.id == user_id)
        )
    ).scalar_one_or_none()
    if not user:
        raise APIError(404, "User not found", "NOT_FOUND")

    before = _user_to_response(user)

    if body.employee_no is not None:
        dup_emp = (
            await db.execute(
                select(User).where(
                    User.employee_no == body.employee_no,
                    User.id != user_id,
                )
            )
        ).scalar_one_or_none()
        if dup_emp:
            raise APIError(
                422,
                f"employee_no '{body.employee_no}' already exists",
                "VALIDATION_ERROR",
                field="employee_no",
            )
        user.employee_no = body.employee_no

    if body.name is not None:
        user.name = body.name
    if body.email is not None:
        dup = (
            await db.execute(
                select(User).where(User.email == body.email, User.id != user_id)
            )
        ).scalar_one_or_none()
        if dup:
            raise APIError(
                422,
                f"Email '{body.email}' already exists",
                "VALIDATION_ERROR",
                field="email",
            )
        user.email = body.email
    if body.team is not None:
        user.team = body.team
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.roles is not None:
        role_objects = []
        for role_name in body.roles:
            role = (
                await db.execute(select(Role).where(Role.name == role_name))
            ).scalar_one_or_none()
            if not role:
                raise APIError(
                    422,
                    f"Unknown role: {role_name}",
                    "VALIDATION_ERROR",
                    field="roles",
                )
            role_objects.append(role)
        user.roles = role_objects

    user.updated_at = datetime.now(timezone.utc)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="user",
        entity_id=user.id,
        action="UPDATE",
        before=before,
        after=_user_to_response(user),
    )
    await db.commit()

    return _user_to_response(user)


# ── PATCH /api/users/{user_id}/deactivate ──────────────────────────

@router.patch("/{user_id}/deactivate", response_model=UserResponse)
async def deactivate_user(
    user_id: int,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    user = (
        await db.execute(
            select(User).options(selectinload(User.roles)).where(User.id == user_id)
        )
    ).scalar_one_or_none()
    if not user:
        raise APIError(404, "User not found", "NOT_FOUND")

    before = _user_to_response(user)
    user.is_active = False
    user.updated_at = datetime.now(timezone.utc)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="user",
        entity_id=user.id,
        action="DEACTIVATE",
        before=before,
        after=_user_to_response(user),
    )
    await db.commit()

    return _user_to_response(user)


# ── PATCH /api/users/{user_id}/reactivate ──────────────────────────

@router.patch("/{user_id}/reactivate", response_model=UserResponse)
async def reactivate_user(
    user_id: int,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    user = (
        await db.execute(
            select(User).options(selectinload(User.roles)).where(User.id == user_id)
        )
    ).scalar_one_or_none()
    if not user:
        raise APIError(404, "User not found", "NOT_FOUND")

    before = _user_to_response(user)
    user.is_active = True
    user.updated_at = datetime.now(timezone.utc)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="user",
        entity_id=user.id,
        action="REACTIVATE",
        before=before,
        after=_user_to_response(user),
    )
    await db.commit()

    return _user_to_response(user)


# ── DELETE /api/users/{user_id} — conditional hard delete (§0.2 P5)─

@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    current_user: dict = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    user = (
        await db.execute(
            select(User).options(selectinload(User.roles)).where(User.id == user_id)
        )
    ).scalar_one_or_none()
    if not user:
        raise APIError(404, "User not found", "NOT_FOUND")

    # §0.2 Principle 5 — check all reference tables
    ref_error = APIError(
        422, "User has references. Use deactivation instead.", "USER_HAS_REFERENCES"
    )

    # 1. audit_logs.actor_id
    cnt = (
        await db.execute(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.actor_id == user_id
            )
        )
    ).scalar()
    if cnt:
        raise ref_error

    # 2. ot_requests.user_id
    cnt = (
        await db.execute(
            select(func.count()).select_from(OtRequest).where(
                OtRequest.user_id == user_id
            )
        )
    ).scalar()
    if cnt:
        raise ref_error

    # 3. ot_requests.submitted_by
    cnt = (
        await db.execute(
            select(func.count()).select_from(OtRequest).where(
                OtRequest.submitted_by == user_id
            )
        )
    ).scalar()
    if cnt:
        raise ref_error

    # 4. ot_approvals.approver_id
    cnt = (
        await db.execute(
            select(func.count()).select_from(OtApproval).where(
                OtApproval.approver_id == user_id
            )
        )
    ).scalar()
    if cnt:
        raise ref_error

    # 5. task_snapshots.last_updated_by / deleted_by (table may not exist until Branch 05)
    try:
        ts_cnt = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM task_snapshots "
                    "WHERE last_updated_by = :uid OR deleted_by = :uid"
                ),
                {"uid": user_id},
            )
        ).scalar()
        if ts_cnt:
            raise ref_error
    except APIError:
        raise
    except Exception:
        pass  # table does not exist yet

    # All checks passed — perform hard delete
    before = _user_to_response(user)

    # Clear role associations, then delete user row
    user.roles = []
    await db.flush()
    await db.delete(user)
    await db.flush()

    await write_audit(
        db,
        actor_id=current_user["user_id"],
        entity_type="user",
        entity_id=user_id,
        action="DELETE",
        before=before,
    )
    await db.commit()

    return {"deleted": True, "user_id": user_id}
