"""Task service — §7.2 business rules + §8.4 helpers."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskItem, TaskSnapshot
from app.schemas.common import APIError

VALID_STATUSES = {"NOT_STARTED", "IN_PROGRESS", "WAITING", "COMPLETED"}

# §7.2.8 Airline classification
_SQ_NAMES = {"sq", "singapore airlines"}


def is_sq_airline(airline: str | None) -> bool:
    if airline is None:
        return False
    return airline.strip().lower() in _SQ_NAMES


def validate_status(status: str) -> str:
    """§7.2.3 normalise status or raise."""
    upper = status.upper().replace(" ", "_")
    if upper in VALID_STATUSES:
        return upper
    raise APIError(
        422, f"Invalid status: {status}", "VALIDATION_ERROR", field="status",
    )


async def check_mh_decrease(
    db: AsyncSession,
    snapshot: TaskSnapshot,
    new_mh: Decimal,
    user: dict,
    shop_id: int,
    correction_reason: str | None,
) -> None:
    """§7.2.7 MH decrease rules."""
    old_mh = snapshot.mh_incurred_hours
    if new_mh >= old_mh:
        return  # no decrease — nothing to check

    # Determine user's effective access level
    from app.services.shop_access_service import check_shop_access

    has_manage = await check_shop_access(db, user, shop_id, "MANAGE")

    if not has_manage:
        # EDIT permission → decrease forbidden
        raise APIError(
            422,
            "MH decrease not allowed with EDIT permission. "
            "Use MANAGE or correct via meeting console.",
            "MH_DECREASE_FORBIDDEN",
            field="mh_incurred_hours",
        )

    # MANAGE: correction_reason required
    if not correction_reason:
        raise APIError(
            422,
            "correction_reason is required when decreasing mh_incurred_hours.",
            "CORRECTION_REASON_REQUIRED",
            field="correction_reason",
        )


async def init_week(
    db: AsyncSession,
    shop_id: int,
    meeting_date: date,
    actor_id: int,
) -> dict:
    """§7.2.2 Carry-over: copy eligible snapshots to new meeting_date.

    Returns {meeting_date, shop_id, created_count, skipped_count}.
    """
    prev_date = meeting_date - timedelta(days=7)

    # Find eligible previous-week snapshots
    q = (
        select(TaskSnapshot)
        .join(TaskItem, TaskSnapshot.task_id == TaskItem.id)
        .where(
            TaskItem.shop_id == shop_id,
            TaskItem.is_active == True,
            TaskSnapshot.meeting_date == prev_date,
            TaskSnapshot.status != "COMPLETED",
            TaskSnapshot.is_deleted == False,
        )
    )
    prev_snapshots = (await db.execute(q)).scalars().all()

    created = 0
    skipped = 0
    now = datetime.now(timezone.utc)

    for ps in prev_snapshots:
        # Idempotent: check if snapshot already exists for this week
        existing = (
            await db.execute(
                select(TaskSnapshot).where(
                    TaskSnapshot.task_id == ps.task_id,
                    TaskSnapshot.meeting_date == meeting_date,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            skipped += 1
            continue

        # Copy policy per §7.2.2
        new_snap = TaskSnapshot(
            task_id=ps.task_id,
            meeting_date=meeting_date,
            status=ps.status,
            mh_incurred_hours=ps.mh_incurred_hours,
            remarks=ps.remarks,
            critical_issue=ps.critical_issue,
            has_issue=ps.has_issue,
            deadline_date=ps.deadline_date,
            correction_reason=None,
            supervisor_updated_at=None,
            is_deleted=False,
            version=1,
            last_updated_by=actor_id,
            last_updated_at=now,
            created_at=now,
        )
        db.add(new_snap)
        created += 1

    if created:
        await db.flush()

    return {
        "meeting_date": meeting_date,
        "shop_id": shop_id,
        "created_count": created,
        "skipped_count": skipped,
    }
