"""OT business logic (§7.1, §8.3)."""
from datetime import date, datetime, time, timezone, timedelta

from sqlalchemy import and_, extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ot import OtApproval, OtRequest
from app.models.reference import WorkPackage
from app.models.user import User
from app.schemas.common import APIError
from app.services.audit_service import write_audit

MONTHLY_LIMIT_MINUTES = 4320  # 72 hours

VALID_REASON_CODES = {
    "BACKLOG", "AOG", "SCHEDULE_PRESSURE", "MANPOWER_SHORTAGE", "OTHER"
}


def normalize_ot_search(value: str | None) -> str:
    """Normalize the free-text OT search query."""
    return (value or "").strip()


async def get_visible_ot_user_ids(db: AsyncSession, user: dict) -> list[int] | None:
    """Return OT-visible user ids for non-admin roles, or None for full admin scope."""
    roles = user.get("roles", [])
    if "ADMIN" in roles:
        return None
    if "SUPERVISOR" in roles:
        return (
            await db.execute(select(User.id).where(User.team == user.get("team")))
        ).scalars().all()
    return [user["user_id"]]


def apply_ot_role_scope(base_q, visible_user_ids: list[int] | None):
    """Restrict an OT query to the caller's visible users."""
    if visible_user_ids is None:
        return base_q
    if not visible_user_ids:
        return base_q.where(OtRequest.id == -1)
    return base_q.where(OtRequest.user_id.in_(visible_user_ids))


def apply_ot_search_filter(base_q, search: str | None):
    """Apply free-text OT search against id, worker, reason, and RFO."""
    normalized = normalize_ot_search(search)
    if not normalized:
        return base_q

    term = f"%{normalized}%"
    predicates = [
        User.name.ilike(term),
        User.employee_no.ilike(term),
        OtRequest.reason_code.ilike(term),
        OtRequest.reason_text.ilike(term),
        WorkPackage.rfo_no.ilike(term),
    ]
    if normalized.isdigit():
        predicates.append(OtRequest.id == int(normalized))
    return base_q.where(or_(*predicates))


def compute_minutes(start: time, end: time) -> int:
    """Compute minutes between start and end times."""
    s = start.hour * 60 + start.minute
    e = end.hour * 60 + end.minute
    return e - s


async def _get_user(db: AsyncSession, user_id: int) -> User:
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise APIError(404, f"User {user_id} not found", "NOT_FOUND")
    return u


async def _monthly_used_minutes(db: AsyncSession, user_id: int, month_date: date) -> int:
    """Sum of requested_minutes for PENDING+ENDORSED+APPROVED in the calendar month."""
    first_day = month_date.replace(day=1)
    if month_date.month == 12:
        next_month = first_day.replace(year=first_day.year + 1, month=1)
    else:
        next_month = first_day.replace(month=first_day.month + 1)

    total = (
        await db.execute(
            select(func.coalesce(func.sum(OtRequest.requested_minutes), 0)).where(
                OtRequest.user_id == user_id,
                OtRequest.date >= first_day,
                OtRequest.date < next_month,
                OtRequest.status.in_(["PENDING", "ENDORSED", "APPROVED"]),
            )
        )
    ).scalar()
    return int(total)


async def _check_time_overlap(
    db: AsyncSession, user_id: int, ot_date: date, start: time, end: time
) -> bool:
    """Return True if there's an overlapping OT for this user/date."""
    existing = (
        await db.execute(
            select(OtRequest).where(
                OtRequest.user_id == user_id,
                OtRequest.date == ot_date,
                OtRequest.status.in_(["PENDING", "ENDORSED", "APPROVED"]),
                OtRequest.start_time < end,
                OtRequest.end_time > start,
            )
        )
    ).scalars().all()
    return len(existing) > 0


async def submit_single(
    db: AsyncSession,
    *,
    actor_id: int,
    target_user_id: int,
    submitted_by: int | None,
    ot_date: date,
    start_time: time,
    end_time: time,
    requested_minutes: int | None,
    reason_code: str,
    reason_text: str | None,
    work_package_id: int | None,
    shop_stream_id: int | None,
) -> OtRequest:
    """Create a single OT request with full validation.

    Raises APIError on validation failure.
    """
    # Validate reason_code
    if reason_code not in VALID_REASON_CODES:
        raise APIError(
            422,
            f"Invalid reason_code '{reason_code}'",
            "VALIDATION_ERROR",
            field="reason_code",
        )

    # SSOT: OT can only be submitted for today/future.
    if ot_date < date.today():
        raise APIError(
            422,
            "OT date must be today or in the future",
            "VALIDATION_ERROR",
            field="date",
        )

    # Compute minutes
    computed = compute_minutes(start_time, end_time)
    if computed <= 0:
        raise APIError(422, "end_time must be after start_time", "VALIDATION_ERROR", field="end_time")

    if requested_minutes is not None and requested_minutes != computed:
        raise APIError(
            422,
            f"requested_minutes ({requested_minutes}) does not match computed ({computed})",
            "VALIDATION_ERROR",
            field="requested_minutes",
        )
    minutes = computed

    # Duplicate check (time overlap)
    if await _check_time_overlap(db, target_user_id, ot_date, start_time, end_time):
        raise APIError(422, "Overlapping OT request exists for this user/date", "DUPLICATE_OT")

    # Monthly limit check
    used = await _monthly_used_minutes(db, target_user_id, ot_date)
    if used + minutes > MONTHLY_LIMIT_MINUTES:
        raise APIError(
            422,
            f"Monthly OT limit exceeded ({used + minutes}/{MONTHLY_LIMIT_MINUTES} min)",
            "OT_MONTHLY_LIMIT_EXCEEDED",
        )

    ot = OtRequest(
        user_id=target_user_id,
        submitted_by=submitted_by,
        work_package_id=work_package_id,
        shop_stream_id=shop_stream_id,
        date=ot_date,
        start_time=start_time,
        end_time=end_time,
        requested_minutes=minutes,
        reason_code=reason_code,
        reason_text=reason_text,
        status="PENDING",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(ot)
    await db.flush()

    await write_audit(
        db,
        actor_id=actor_id,
        entity_type="ot_request",
        entity_id=ot.id,
        action="CREATE",
        after={"user_id": ot.user_id, "date": str(ot.date), "minutes": ot.requested_minutes},
    )

    return ot


async def submit_bulk(
    db: AsyncSession,
    *,
    actor_id: int,
    actor_team: str | None,
    actor_roles: list[str],
    user_ids: list[int],
    ot_date: date,
    start_time: time,
    end_time: time,
    requested_minutes: int | None,
    reason_code: str,
    reason_text: str | None,
    work_package_id: int | None,
    shop_stream_id: int | None,
) -> dict:
    """Bulk OT submit. Returns {created: [...], skipped: [...]}."""
    if reason_code not in VALID_REASON_CODES:
        raise APIError(422, f"Invalid reason_code '{reason_code}'", "VALIDATION_ERROR", field="reason_code")

    # SSOT: OT can only be submitted for today/future.
    if ot_date < date.today():
        raise APIError(
            422,
            "OT date must be today or in the future",
            "VALIDATION_ERROR",
            field="date",
        )

    computed = compute_minutes(start_time, end_time)
    if computed <= 0:
        raise APIError(422, "end_time must be after start_time", "VALIDATION_ERROR", field="end_time")

    if requested_minutes is not None and requested_minutes != computed:
        raise APIError(
            422,
            f"requested_minutes ({requested_minutes}) does not match computed ({computed})",
            "VALIDATION_ERROR",
            field="requested_minutes",
        )
    minutes = computed

    is_admin = "ADMIN" in actor_roles
    created = []
    skipped = []

    for uid in user_ids:
        # Check user exists and team scope
        target = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        if not target:
            skipped.append({"user_id": uid, "reason": "USER_NOT_FOUND"})
            continue

        # Team check for non-admin
        if not is_admin and target.team != actor_team:
            skipped.append({"user_id": uid, "reason": "WRONG_TEAM"})
            continue

        # Duplicate check (any overlap on this date)
        if await _check_time_overlap(db, uid, ot_date, start_time, end_time):
            skipped.append({"user_id": uid, "reason": "DUPLICATE_DATE"})
            continue

        # Monthly limit check
        used = await _monthly_used_minutes(db, uid, ot_date)
        if used + minutes > MONTHLY_LIMIT_MINUTES:
            skipped.append({"user_id": uid, "reason": "MONTHLY_LIMIT_EXCEEDED"})
            continue

        ot = OtRequest(
            user_id=uid,
            submitted_by=actor_id,
            work_package_id=work_package_id,
            shop_stream_id=shop_stream_id,
            date=ot_date,
            start_time=start_time,
            end_time=end_time,
            requested_minutes=minutes,
            reason_code=reason_code,
            reason_text=reason_text,
            status="PENDING",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(ot)
        await db.flush()

        await write_audit(
            db,
            actor_id=actor_id,
            entity_type="ot_request",
            entity_id=ot.id,
            action="CREATE",
            after={"user_id": ot.user_id, "date": str(ot.date), "minutes": ot.requested_minutes},
        )
        created.append({
            "id": ot.id,
            "user_id": ot.user_id,
            "submitted_by": ot.submitted_by,
            "status": ot.status,
        })

    return {
        "created": created,
        "skipped": skipped,
        "created_count": len(created),
        "skipped_count": len(skipped),
    }
