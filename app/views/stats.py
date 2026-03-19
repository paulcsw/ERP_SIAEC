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

LIMIT_HOURS = round(MONTHLY_LIMIT_MINUTES / 60, 1)

# Hex colors for weekly bars (light ??dark navy), last week uses gold
_WEEK_HEX = ["#d5dde8", "#aebdd2", "#5a7ba3", "#2e5a8a", "#1e3a5f"]
_WEEK_GOLD = "#c8850a"


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


def _month_options() -> list[dict]:
    """Generate last 6 months as dropdown options."""
    y, m = date.today().year, date.today().month
    opts = []
    for _ in range(6):
        d = date(y, m, 1)
        opts.append({"value": d.strftime("%Y-%m"), "label": d.strftime("%B %Y")})
        m -= 1
        if m <= 0:
            m = 12
            y -= 1
    return opts


@router.get("/stats/ot")
async def ot_stats_page(
    request: Request,
    month: str | None = Query(None),
    team: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = current_user.get("roles", [])
    is_sup_or_admin = "SUPERVISOR" in roles or "ADMIN" in roles

    first, last = _parse_month(month)
    month_label = first.strftime("%Y-%m")
    month_display = first.strftime("%B %Y")
    months = _month_options()

    # Teams for dropdown
    team_rows = (await db.execute(
        select(User.team).where(User.team.isnot(None), User.is_active == True).distinct()  # noqa: E712
    )).scalars().all()
    teams = sorted(t for t in team_rows if t)

    # ?€?€ OT query (filtered by role & team) ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
    q = select(OtRequest).where(OtRequest.date >= first, OtRequest.date <= last)
    if "ADMIN" not in roles:
        uids = await _team_user_ids(db, current_user.get("team"))
        q = q.where(OtRequest.user_id.in_(uids))
    elif team:
        uids = await _team_user_ids(db, team)
        if uids:
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

    # ?€?€ Individual Monthly Usage (top 6) ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
    user_q = select(User).where(User.is_active == True)  # noqa: E712
    if "ADMIN" not in roles:
        user_q = user_q.where(User.team == current_user.get("team"))
    elif team:
        user_q = user_q.where(User.team == team)

    user_rows_list = (await db.execute(user_q)).scalars().all()

    usage_list: list[dict] = []
    for u in user_rows_list:
        used = (await db.execute(
            select(func.coalesce(func.sum(OtRequest.requested_minutes), 0)).where(
                OtRequest.user_id == u.id, OtRequest.date >= first, OtRequest.date <= last,
                OtRequest.status.in_(COUNTABLE_STATUSES),
            )
        )).scalar() or 0
        used_hours = round(used / 60, 1)
        pct = round(used / MONTHLY_LIMIT_MINUTES * 100, 1) if MONTHLY_LIMIT_MINUTES else 0
        remaining = round(max(0, MONTHLY_LIMIT_MINUTES - used) / 60, 1)
        sessions_left = int(remaining / 3.5) if remaining > 0 else 0

        if pct >= 100:
            bar_color = "bg-st-red"
            pct_color = "text-st-red"
        elif used_hours >= 60:
            bar_color = "bg-gold-500"
            pct_color = "text-gold-500"
        else:
            bar_color = "bg-st-blue"
            pct_color = "text-st-blue"

        usage_list.append({
            "name": u.name, "employee_no": u.employee_no,
            "used_hours": used_hours,
            "limit_hours": LIMIT_HOURS,
            "pct": pct,
            "remaining_hours": remaining,
            "sessions_left": sessions_left,
            "bar_color": bar_color,
            "pct_color": pct_color,
        })
    usage_list.sort(key=lambda x: -x["pct"])
    usage_list = usage_list[:6]

    # Usage footer
    all_used = [u["used_hours"] for u in usage_list]
    team_avg = round(sum(all_used) / len(all_used), 1) if all_used else 0
    team_avg_pct = round(team_avg / LIMIT_HOURS * 100, 1) if LIMIT_HOURS else 0
    at_limit_count = sum(1 for u in usage_list if u["pct"] >= 100)
    above_warning_count = sum(1 for u in usage_list if u["pct"] >= 70)
    usage_footer = {
        "team_avg": team_avg, "team_avg_pct": team_avg_pct,
        "at_limit": at_limit_count, "above_warning": above_warning_count,
    }

    # ?€?€ Approval Pipeline ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
    pipeline = {
        "submitted": sum(1 for _ in rows),
        "endorsed": sum(1 for r in rows if r.status == "ENDORSED"),
        "approved": sum(1 for r in rows if r.status == "APPROVED"),
        "rejected": sum(1 for r in rows if r.status == "REJECTED"),
    }
    pipe_max = max(pipeline["submitted"], 1)
    pipeline["conversion"] = round(pipeline["approved"] / pipeline["submitted"] * 100) if pipeline["submitted"] else 0

    # ?€?€ By Reason ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
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
    reason_text_colors = {
        "BACKLOG": "text-white", "SCHEDULE_PRESSURE": "text-white",
        "AOG": "text-white", "MANPOWER_SHORTAGE": "text-navy-900", "OTHER": "text-navy-700",
    }
    leading_reason = reason_bars[0]["code"].replace("_", " ").title() if reason_bars else None
    leading_reason_pct = round(reason_bars[0]["pct"]) if reason_bars else 0

    # ?€?€ Weekly Trend ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
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

    bar_max_px = 112
    total_weeks = len(weekly)
    for i, w in enumerate(weekly):
        w["height"] = max(4, int(w["hours"] / max_h * bar_max_px)) if max_h else 4
        is_last = (i == total_weeks - 1)
        if is_last:
            w["bg_hex"] = _WEEK_GOLD
        else:
            shade_idx = min(int(i / max(total_weeks - 1, 1) * (len(_WEEK_HEX) - 1)), len(_WEEK_HEX) - 1)
            w["bg_hex"] = _WEEK_HEX[shade_idx]
        w["is_current"] = is_last

    # Weekly footer
    peak_w = max(weekly, key=lambda x: x["hours"]) if weekly else None
    last_w = weekly[-1] if weekly else None
    if peak_w and last_w and peak_w["hours"] > 0 and last_w is not peak_w:
        change_pct = round((last_w["hours"] - peak_w["hours"]) / peak_w["hours"] * 100)
        if change_pct < 0:
            trend_text = f"\u2193 {abs(change_pct)}% from {peak_w['label']} peak"
            trend_color = "text-st-green"
        else:
            trend_text = f"\u2191 {change_pct}% from {peak_w['label']}"
            trend_color = "text-st-red"
    else:
        trend_text = ""
        trend_color = ""

    weekly_footer = {
        "month_total": summary["total_hours"],
        "trend_text": trend_text,
        "trend_color": trend_color,
    }

    return templates.TemplateResponse(request, "stats/ot_dashboard.html", _ctx(
        request, current_user,
        month_label=month_label,
        month_display=month_display,
        months=months,
        team_filter=team or "",
        teams=teams,
        summary=summary,
        usage_list=usage_list,
        usage_footer=usage_footer,
        pipeline=pipeline,
        pipe_max=pipe_max,
        reason_bars=reason_bars,
        reason_colors=reason_colors,
        reason_text_colors=reason_text_colors,
        leading_reason=leading_reason,
        leading_reason_pct=leading_reason_pct,
        weekly=weekly,
        weekly_footer=weekly_footer,
        is_sup_or_admin=is_sup_or_admin,
    ))
