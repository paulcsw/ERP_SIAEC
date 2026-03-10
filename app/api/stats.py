"""OT Statistics API (§8.7, §8.12) — Branch 11 commit 1."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.ot import OtApproval, OtRequest
from app.models.user import User
from app.schemas.common import APIError
from app.services.ot_service import MONTHLY_LIMIT_MINUTES

router = APIRouter(prefix="/api/stats", tags=["stats"])

# ── Helpers ────────────────────────────────────────────────────────────

COUNTABLE_STATUSES = ("APPROVED", "ENDORSED", "PENDING")


def _parse_month(month_str: str | None) -> tuple[date, date]:
    """Parse 'YYYY-MM' → (first_day, last_day). Default: current month."""
    if month_str:
        parts = month_str.split("-")
        y, m = int(parts[0]), int(parts[1])
    else:
        today = date.today()
        y, m = today.year, today.month
    first = date(y, m, 1)
    last = date(y, m, calendar.monthrange(y, m)[1])
    return first, last


async def _team_user_ids(db: AsyncSession, team: str | None) -> list[int]:
    if not team:
        return []
    return list(
        (await db.execute(select(User.id).where(User.team == team, User.is_active == True))).scalars().all()
    )


def _require_sup_plus(current_user: dict):
    roles = current_user.get("roles", [])
    if "SUPERVISOR" not in roles and "ADMIN" not in roles:
        raise APIError(403, "SUPERVISOR+ required", "FORBIDDEN")


# ── GET /api/stats/ot-summary — §8.7 ──────────────────────────────────

@router.get("/ot-summary")
async def ot_summary(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    team: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    roles = current_user.get("roles", [])

    q = select(OtRequest)
    if date_from:
        q = q.where(OtRequest.date >= date_from)
    if date_to:
        q = q.where(OtRequest.date <= date_to)

    # Role scoping
    if "ADMIN" not in roles:
        uids = await _team_user_ids(db, current_user.get("team"))
        q = q.where(OtRequest.user_id.in_(uids))
    elif team:
        uids = await _team_user_ids(db, team)
        q = q.where(OtRequest.user_id.in_(uids))

    rows = (await db.execute(q)).scalars().all()

    total_minutes = sum(r.requested_minutes for r in rows if r.status in COUNTABLE_STATUSES)
    approved_minutes = sum(r.requested_minutes for r in rows if r.status == "APPROVED")
    pending_endorsed_minutes = sum(
        r.requested_minutes for r in rows if r.status in ("PENDING", "ENDORSED")
    )

    # Average turnaround (submit → final approve)
    turnarounds = []
    ot_ids = [r.id for r in rows if r.status == "APPROVED"]
    if ot_ids:
        approvals = (await db.execute(
            select(OtApproval).where(
                OtApproval.ot_request_id.in_(ot_ids),
                OtApproval.stage == "APPROVE",
                OtApproval.action == "APPROVE",
            )
        )).scalars().all()
        approve_map = {a.ot_request_id: a.acted_at for a in approvals}
        for r in rows:
            if r.id in approve_map and r.created_at and approve_map[r.id]:
                diff = (approve_map[r.id] - r.created_at).total_seconds() / 3600
                turnarounds.append(diff)

    avg_turnaround = round(sum(turnarounds) / len(turnarounds), 1) if turnarounds else 0

    return {
        "total_hours": round(total_minutes / 60, 1),
        "approved_hours": round(approved_minutes / 60, 1),
        "pending_endorsed_hours": round(pending_endorsed_minutes / 60, 1),
        "avg_turnaround_hours": avg_turnaround,
        "total_requests": len(rows),
    }


# ── GET /api/stats/ot-monthly-usage — §8.7.1 ──────────────────────────

@router.get("/ot-monthly-usage")
async def ot_monthly_usage(
    month: str | None = Query(None, description="YYYY-MM"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    roles = current_user.get("roles", [])
    first, last = _parse_month(month)
    month_label = first.strftime("%Y-%m")

    # Determine users to report on
    if "ADMIN" in roles:
        user_rows = (await db.execute(
            select(User).where(User.is_active == True)
        )).scalars().all()
    else:
        user_rows = (await db.execute(
            select(User).where(User.team == current_user.get("team"), User.is_active == True)
        )).scalars().all()

    users_data = []
    for u in user_rows:
        q = select(func.coalesce(func.sum(OtRequest.requested_minutes), 0)).where(
            OtRequest.user_id == u.id,
            OtRequest.date >= first,
            OtRequest.date <= last,
            OtRequest.status.in_(COUNTABLE_STATUSES),
        )
        used = (await db.execute(q)).scalar() or 0

        pending_q = select(func.coalesce(func.sum(OtRequest.requested_minutes), 0)).where(
            OtRequest.user_id == u.id,
            OtRequest.date >= first,
            OtRequest.date <= last,
            OtRequest.status.in_(("PENDING", "ENDORSED")),
        )
        pending = (await db.execute(pending_q)).scalar() or 0

        users_data.append({
            "user_id": u.id,
            "name": u.name,
            "employee_no": u.employee_no,
            "used_minutes": used,
            "remaining_minutes": max(0, MONTHLY_LIMIT_MINUTES - used),
            "pending_minutes": pending,
            "usage_pct": round(used / MONTHLY_LIMIT_MINUTES * 100, 1) if MONTHLY_LIMIT_MINUTES else 0,
        })

    # Sort by usage_pct descending
    users_data.sort(key=lambda x: x["usage_pct"], reverse=True)

    return {
        "month": month_label,
        "limit_minutes": MONTHLY_LIMIT_MINUTES,
        "users": users_data,
    }


# ── GET /api/stats/ot-by-reason — §8.12.1 ─────────────────────────────

@router.get("/ot-by-reason")
async def ot_by_reason(
    month: str | None = Query(None, description="YYYY-MM"),
    team: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    roles = current_user.get("roles", [])
    first, last = _parse_month(month)
    month_label = first.strftime("%Y-%m")

    q = select(OtRequest).where(
        OtRequest.date >= first,
        OtRequest.date <= last,
        OtRequest.status.in_(COUNTABLE_STATUSES),
    )

    if "ADMIN" not in roles:
        uids = await _team_user_ids(db, current_user.get("team"))
        q = q.where(OtRequest.user_id.in_(uids))
    elif team:
        uids = await _team_user_ids(db, team)
        q = q.where(OtRequest.user_id.in_(uids))

    rows = (await db.execute(q)).scalars().all()

    # Aggregate by reason_code
    by_reason: dict[str, int] = {}
    for r in rows:
        by_reason[r.reason_code] = by_reason.get(r.reason_code, 0) + r.requested_minutes
    total_min = sum(by_reason.values()) or 1  # avoid div-by-zero

    breakdown = [
        {
            "reason_code": rc,
            "hours": round(minutes / 60, 1),
            "pct": round(minutes / total_min * 100, 1),
        }
        for rc, minutes in sorted(by_reason.items(), key=lambda x: -x[1])
    ]

    effective_team = team if "ADMIN" in roles else current_user.get("team")

    return {
        "month": month_label,
        "team": effective_team,
        "breakdown": breakdown,
    }


# ── GET /api/stats/ot-weekly-trend — §8.12.2 ──────────────────────────

@router.get("/ot-weekly-trend")
async def ot_weekly_trend(
    month: str | None = Query(None, description="YYYY-MM"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_sup_plus(current_user)
    roles = current_user.get("roles", [])
    first, last = _parse_month(month)
    month_label = first.strftime("%Y-%m")

    q = select(OtRequest).where(
        OtRequest.date >= first,
        OtRequest.date <= last,
        OtRequest.status.in_(COUNTABLE_STATUSES),
    )

    if "ADMIN" not in roles:
        uids = await _team_user_ids(db, current_user.get("team"))
        q = q.where(OtRequest.user_id.in_(uids))

    rows = (await db.execute(q)).scalars().all()

    # Split into weeks within the month
    weeks: list[dict] = []
    week_start = first
    week_num = 1
    while week_start <= last:
        week_end = min(week_start + timedelta(days=6), last)
        label = f"{week_start.strftime('%b %d')}–{week_end.strftime('%d')}"
        minutes = sum(
            r.requested_minutes for r in rows
            if week_start <= r.date <= week_end
        )
        weeks.append({
            "week": week_num,
            "label": label,
            "hours": round(minutes / 60, 1),
        })
        week_start = week_end + timedelta(days=1)
        week_num += 1

    return {
        "month": month_label,
        "weeks": weeks,
    }
