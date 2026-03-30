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


def _provided_fields(body) -> set[str]:
    if hasattr(body, "model_fields_set"):
        return set(body.model_fields_set)
    return set(getattr(body, "__fields_set__", set()))


def _normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


async def _resolve_role_objects(db: AsyncSession, role_names: list[str]) -> list[Role]:
    role_objects = []
    for role_name in role_names:
        role = (
            await db.execute(select(Role).where(Role.name == role_name))
        ).scalar_one_or_none()
        if not role:
            raise APIError(
                422, f"Unknown role: {role_name}", "VALIDATION_ERROR", field="roles"
            )
        role_objects.append(role)
    return role_objects


async def _count_active_admins(db: AsyncSession, *, exclude_user_id: int | None = None) -> int:
    q = (
        select(func.count(func.distinct(User.id)))
        .select_from(User)
        .join(user_roles, user_roles.c.user_id == User.id)
        .join(Role, Role.id == user_roles.c.role_id)
        .where(User.is_active == True, Role.name == "ADMIN")  # noqa: E712
    )
    if exclude_user_id is not None:
        q = q.where(User.id != exclude_user_id)
    return (await db.execute(q)).scalar() or 0


async def _enforce_admin_lockout_guards(
    db: AsyncSession,
    current_user: dict,
    user: User,
    *,
    target_roles: set[str],
    target_is_active: bool,
):
    current_roles = {r.name for r in user.roles}
    current_has_admin = "ADMIN" in current_roles
    target_has_admin = "ADMIN" in target_roles
    is_self = user.id == current_user["user_id"]

    if is_self and "ADMIN" in current_user.get("roles", []) and (not target_is_active or not target_has_admin):
        raise APIError(
            422,
            "You cannot deactivate your own account or remove your own ADMIN role.",
            "SELF_LOCKOUT_FORBIDDEN",
        )

    if user.is_active and current_has_admin and (not target_is_active or not target_has_admin):
        remaining_admins = await _count_active_admins(db, exclude_user_id=user.id)
        if remaining_admins == 0:
            raise APIError(
                422,
                "At least one active ADMIN must remain in the system.",
                "LAST_ACTIVE_ADMIN",
            )


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
    normalized_email = _normalize_email(body.email)

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
    if normalized_email:
        existing_email = (
            await db.execute(select(User).where(User.email == normalized_email))
        ).scalar_one_or_none()
        if existing_email:
            raise APIError(
                422,
                f"Email '{normalized_email}' already exists",
                "VALIDATION_ERROR",
                field="email",
            )

    # Resolve role names → Role objects
    if not body.roles:
        raise APIError(
            422,
            "At least one role is required.",
            "VALIDATION_ERROR",
            field="roles",
        )
    role_objects = await _resolve_role_objects(db, body.roles)

    user = User(
        employee_no=body.employee_no,
        name=body.name,
        email=normalized_email,
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
    provided_fields = _provided_fields(body)
    next_roles = {r.name for r in user.roles}
    next_is_active = user.is_active
    resolved_role_objects: list[Role] | None = None

    if "roles" in provided_fields:
        if not body.roles:
            raise APIError(
                422,
                "At least one role is required.",
                "VALIDATION_ERROR",
                field="roles",
            )
        resolved_role_objects = await _resolve_role_objects(db, body.roles)
        next_roles = {role.name for role in resolved_role_objects}

    if "is_active" in provided_fields and body.is_active is not None:
        next_is_active = body.is_active

    await _enforce_admin_lockout_guards(
        db,
        current_user,
        user,
        target_roles=next_roles,
        target_is_active=next_is_active,
    )

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
    if "email" in provided_fields:
        normalized_email = _normalize_email(body.email)
        if normalized_email:
            dup = (
                await db.execute(
                    select(User).where(User.email == normalized_email, User.id != user_id)
                )
            ).scalar_one_or_none()
            if dup:
                raise APIError(
                    422,
                    f"Email '{normalized_email}' already exists",
                    "VALIDATION_ERROR",
                    field="email",
                )
        user.email = normalized_email
    if body.team is not None:
        user.team = body.team
    if body.is_active is not None:
        user.is_active = body.is_active
    if "roles" in provided_fields:
        user.roles = resolved_role_objects

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
    await _enforce_admin_lockout_guards(
        db,
        current_user,
        user,
        target_roles={r.name for r in user.roles},
        target_is_active=False,
    )
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
