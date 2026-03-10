"""OT Statistics SSR views (Branch 11 commit 3)."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.stats import _parse_month, _team_user_ids, COUNTABLE_STATUSES
from app.models.ot import OtApproval, OtRequest
from app.models.user import User
from app.services.ot_service import MONTHLY_LIMIT_MINUTES
from app.views import templates

router = APIRouter(tags=["stats-views"])


def _ctx(request, user, **kw):
    page = kw.pop("active_page", "ot_stats")
    return {
        "request": request,
        "current_user": {
            "user_id": user["user_id"],
            "display_name": user.get("display_name", ""),
            "roles": user.get("roles", []),
            "team": user.get("team"),
            "employee_no": user.get("employee_no", ""),
        },
        "active_tab": "ot",
        "page": page,
        **kw,
    }


@router.get("/stats/ot")
async def ot_stats_page(
    request: Request,
    month: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = current_user.get("roles", [])
    is_sup_or_admin = "SUPERVISOR" in roles or "ADMIN" in roles

    first, last = _parse_month(month)
    month_label = first.strftime("%Y-%m")

    # ── Summary KPIs ───────────────────────────────────────────────
    q = select(OtRequest).where(OtRequest.date >= first, OtRequest.date <= last)
    if "ADMIN" not in roles:
        uids = await _team_user_ids(db, current_user.get("team"))
        q = q.where(OtRequest.user_id.in_(uids))

    rows = (await db.execute(q)).scalars().all()

    total_min = sum(r.requested_minutes for r in rows if r.status in COUNTABLE_STATUSES)
    approved_min = sum(r.requested_minutes for r in rows if r.status == "APPROVED")
    pending_endorsed_min = sum(r.requested_minutes for r in rows if r.status in ("PENDING", "ENDORSED"))

    # Avg turnaround
    turnarounds: list[float] = []
    approved_ids = [r.id for r in rows if r.status == "APPROVED"]
    if approved_ids:
        approvals = (await db.execute(
            select(OtApproval).where(
                OtApproval.ot_request_id.in_(approved_ids),
                OtApproval.stage == "APPROVE", OtApproval.action == "APPROVE",
            )
        )).scalars().all()
        approve_map = {a.ot_request_id: a.acted_at for a in approvals}
        for r in rows:
            if r.id in approve_map and r.created_at and approve_map[r.id]:
                diff = (approve_map[r.id] - r.created_at).total_seconds() / 3600
                turnarounds.append(diff)
    avg_turnaround = round(sum(turnarounds) / len(turnarounds), 1) if turnarounds else 0

    summary = {
        "total_hours": round(total_min / 60, 1),
        "approved_hours": round(approved_min / 60, 1),
        "pending_endorsed_hours": round(pending_endorsed_min / 60, 1),
        "avg_turnaround": avg_turnaround,
    }

    # ── Individual Monthly Usage (top 6) ───────────────────────────
    if "ADMIN" in roles:
        user_rows = (await db.execute(select(User).where(User.is_active == True))).scalars().all()
    else:
        user_rows = (await db.execute(
            select(User).where(User.team == current_user.get("team"), User.is_active == True)
        )).scalars().all()

    usage_list: list[dict] = []
    for u in user_rows:
        used = (await db.execute(
            select(func.coalesce(func.sum(OtRequest.requested_minutes), 0)).where(
                OtRequest.user_id == u.id, OtRequest.date >= first, OtRequest.date <= last,
                OtRequest.status.in_(COUNTABLE_STATUSES),
            )
        )).scalar() or 0
        pct = round(used / MONTHLY_LIMIT_MINUTES * 100, 1) if MONTHLY_LIMIT_MINUTES else 0
        usage_list.append({
            "name": u.name, "employee_no": u.employee_no,
            "used_hours": round(used / 60, 1),
            "limit_hours": round(MONTHLY_LIMIT_MINUTES / 60, 1),
            "pct": pct,
            "remaining_hours": round(max(0, MONTHLY_LIMIT_MINUTES - used) / 60, 1),
            "bar_color": "bg-st-red" if pct >= 100 else "bg-gold-500" if pct >= 70 else "bg-navy-600",
        })
    usage_list.sort(key=lambda x: -x["pct"])
    usage_list = usage_list[:6]

    # ── Approval Pipeline ──────────────────────────────────────────
    pipeline = {
        "submitted": sum(1 for r in rows),
        "endorsed": sum(1 for r in rows if r.status == "ENDORSED"),
        "approved": sum(1 for r in rows if r.status == "APPROVED"),
        "rejected": sum(1 for r in rows if r.status == "REJECTED"),
    }

    # ── By Reason ──────────────────────────────────────────────────
    by_reason: dict[str, int] = {}
    for r in rows:
        if r.status in COUNTABLE_STATUSES:
            by_reason[r.reason_code] = by_reason.get(r.reason_code, 0) + r.requested_minutes
    total_reason = sum(by_reason.values()) or 1
    reason_bars = [
        {"code": rc, "hours": round(m / 60, 1), "pct": round(m / total_reason * 100, 1)}
        for rc, m in sorted(by_reason.items(), key=lambda x: -x[1])
    ]
    reason_colors = {
        "BACKLOG": "bg-navy-600", "SCHEDULE_PRESSURE": "bg-navy-400",
        "AOG": "bg-st-red", "MANPOWER_SHORTAGE": "bg-gold-500", "OTHER": "bg-surf-300",
    }

    # ── Weekly Trend ───────────────────────────────────────────────
    weekly: list[dict] = []
    ws = first
    wk = 1
    max_h = 0.1
    while ws <= last:
        we = min(ws + timedelta(days=6), last)
        minutes = sum(r.requested_minutes for r in rows if r.status in COUNTABLE_STATUSES and ws <= r.date <= we)
        h = round(minutes / 60, 1)
        if h > max_h:
            max_h = h
        weekly.append({"label": f"W{wk}", "hours": h})
        ws = we + timedelta(days=1)
        wk += 1
    for w in weekly:
        w["height"] = max(4, int(w["hours"] / max_h * 120)) if max_h else 4

    week_colors = ["bg-navy-200", "bg-navy-300", "bg-navy-500", "bg-gold-500", "bg-navy-400"]

    return templates.TemplateResponse("stats/ot_dashboard.html", _ctx(
        request, current_user,
        month_label=month_label,
        summary=summary,
        usage_list=usage_list,
        pipeline=pipeline,
        reason_bars=reason_bars,
        reason_colors=reason_colors,
        weekly=weekly,
        week_colors=week_colors,
        is_sup_or_admin=is_sup_or_admin,
    ))
