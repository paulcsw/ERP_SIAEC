"""Shared RFO analytics helpers for SSR views and API routes."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ot import OtRequest
from app.models.reference import Aircraft, WorkPackage
from app.models.task import TaskItem, TaskSnapshot
from app.models.user import User


def can_view_rfo(current_user: dict) -> bool:
    roles = current_user.get("roles", [])
    return "SUPERVISOR" in roles or "ADMIN" in roles


def display_rfo_no(work_package: WorkPackage) -> str:
    return work_package.rfo_no or f"WP-{work_package.id}"


async def get_work_package(db: AsyncSession, work_package_id: int) -> WorkPackage | None:
    return (
        await db.execute(select(WorkPackage).where(WorkPackage.id == work_package_id))
    ).scalar_one_or_none()


async def get_rfo_selector_options(
    db: AsyncSession,
    *,
    selected_wp: WorkPackage | None = None,
) -> list[dict]:
    active_wps = (
        await db.execute(
            select(WorkPackage)
            .where(WorkPackage.status == "ACTIVE")
            .order_by(WorkPackage.id)
        )
    ).scalars().all()

    option_wps = list(active_wps)
    active_ids = {wp.id for wp in active_wps}
    if selected_wp and selected_wp.id not in active_ids:
        option_wps.insert(0, selected_wp)

    ac_map = await _load_aircraft_map(db, option_wps)
    selector_stats = await _load_selector_stats(db, [wp.id for wp in option_wps])

    options = []
    for wp in option_wps:
        ac = ac_map.get(wp.aircraft_id)
        stats = selector_stats.get(wp.id, {"count": 0, "mh": 0.0})
        options.append(
            {
                "id": wp.id,
                "display_rfo_no": display_rfo_no(wp),
                "rfo_no": wp.rfo_no,
                "title": wp.title,
                "ac_reg": ac.ac_reg if ac else None,
                "airline": ac.airline if ac else None,
                "status": wp.status,
                "task_count": stats["count"],
                "planned_mh": stats["mh"],
                "is_selected_historical": bool(
                    selected_wp
                    and wp.id == selected_wp.id
                    and selected_wp.status != "ACTIVE"
                ),
            }
        )
    return options


async def build_rfo_analytics(
    db: AsyncSession,
    work_package: WorkPackage,
) -> dict:
    ac = (
        (
            await db.execute(
                select(Aircraft).where(Aircraft.id == work_package.aircraft_id)
            )
        ).scalar_one_or_none()
        if work_package.aircraft_id
        else None
    )
    tasks = (
        await db.execute(
            select(TaskItem).where(
                TaskItem.work_package_id == work_package.id,
                TaskItem.is_active == True,  # noqa: E712
            )
        )
    ).scalars().all()

    planned_mh = sum(float(task.planned_mh or 0) for task in tasks)
    total_tasks = len(tasks)
    actual_mh = 0.0
    waiting_mh = 0.0
    completed = 0
    blocker_count = 0
    unassigned_count = sum(1 for task in tasks if not task.assigned_worker_id)
    status_counts = {"NOT_STARTED": 0, "IN_PROGRESS": 0, "WAITING": 0, "COMPLETED": 0}
    cycle_times: list[float] = []
    blocker_rows: list[dict] = []
    worker_data: dict[int | None, dict] = {}
    now = datetime.now(timezone.utc)

    for task in tasks:
        wid = task.assigned_worker_id
        if wid not in worker_data:
            worker_data[wid] = {"task_count": 0, "mh_total": 0.0}
        worker_data[wid]["task_count"] += 1

        latest_snapshot = (
            await db.execute(
                select(TaskSnapshot)
                .where(
                    TaskSnapshot.task_id == task.id,
                    TaskSnapshot.is_deleted == False,  # noqa: E712
                )
                .order_by(TaskSnapshot.meeting_date.desc(), TaskSnapshot.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest_snapshot:
            mh = float(latest_snapshot.mh_incurred_hours or 0)
            actual_mh += mh
            worker_data[wid]["mh_total"] += mh
            status_counts[latest_snapshot.status] = status_counts.get(latest_snapshot.status, 0) + 1

            if latest_snapshot.status == "WAITING":
                waiting_mh += mh
                if latest_snapshot.has_issue:
                    blocker_count += 1
                    days = (
                        (now.date() - latest_snapshot.meeting_date).days
                        if latest_snapshot.meeting_date
                        else 0
                    )
                    blocker_rows.append(
                        {
                            "task_id": task.id,
                            "snapshot_id": latest_snapshot.id,
                            "task_text": task.task_text,
                            "critical_issue": latest_snapshot.critical_issue,
                            "remarks": latest_snapshot.remarks,
                            "meeting_date": (
                                latest_snapshot.meeting_date.isoformat()
                                if latest_snapshot.meeting_date
                                else None
                            ),
                            "days_blocked": max(0, days),
                            "mh_incurred_hours": mh,
                        }
                    )
            elif latest_snapshot.status == "COMPLETED":
                completed += 1

        first_in_progress = (
            await db.execute(
                select(func.min(TaskSnapshot.meeting_date)).where(
                    TaskSnapshot.task_id == task.id,
                    TaskSnapshot.status == "IN_PROGRESS",
                    TaskSnapshot.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar()
        last_completed = (
            await db.execute(
                select(func.max(TaskSnapshot.meeting_date)).where(
                    TaskSnapshot.task_id == task.id,
                    TaskSnapshot.status == "COMPLETED",
                    TaskSnapshot.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar()
        if first_in_progress and last_completed and last_completed >= first_in_progress:
            cycle_times.append((last_completed - first_in_progress).days / 7.0)

    ot_rows = (
        await db.execute(select(OtRequest).where(OtRequest.work_package_id == work_package.id))
    ).scalars().all()
    ot_by_status: dict[str, int] = {}
    total_approved_minutes = 0
    total_countable_minutes = 0
    for row in ot_rows:
        ot_by_status[row.status] = ot_by_status.get(row.status, 0) + 1
        if row.status == "APPROVED":
            total_approved_minutes += row.requested_minutes
        if row.status in {"APPROVED", "ENDORSED", "PENDING"}:
            total_countable_minutes += row.requested_minutes
    ot_hours = round(total_countable_minutes / 60, 1)

    blocker_rows.sort(key=lambda item: -item["days_blocked"])
    avg_cycle = round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else 0
    productive_ratio = round((actual_mh - waiting_mh) / actual_mh * 100, 1) if actual_mh else 0
    ot_ratio = round(ot_hours / actual_mh * 100, 1) if actual_mh else 0
    ftc = round(completed / total_tasks * 100, 1) if total_tasks else 0
    plan_pct = round(actual_mh / planned_mh * 100) if planned_mh else 0
    completion_rate = round(completed / total_tasks * 100, 1) if total_tasks else 0

    workers = await _build_worker_rows(db, worker_data)
    api_burndown = await _build_api_burndown(db, work_package.id, work_package.rfo_no, planned_mh, tasks)
    ssr_burndown = _build_ssr_burndown(api_burndown["weeks"], planned_mh, total_tasks, completion_rate, plan_pct)

    return {
        "selected": {
            "id": work_package.id,
            "rfo_no": work_package.rfo_no,
            "display_rfo_no": display_rfo_no(work_package),
            "title": work_package.title,
            "status": work_package.status,
            "start_date": work_package.start_date,
            "end_date": work_package.end_date,
            "priority": work_package.priority,
        },
        "summary_strip": {
            "ac_reg": ac.ac_reg if ac else "N/A",
            "ac_type": work_package.title or "N/A",
            "airline": ac.airline if ac else "N/A",
            "start_date": work_package.start_date.isoformat() if work_package.start_date else "-",
            "end_date": work_package.end_date.isoformat() if work_package.end_date else "-",
            "days_remaining": (
                (work_package.end_date - date.today()).days if work_package.end_date else None
            ),
            "priority": work_package.priority or 0,
        },
        "kpi": {
            "total_tasks": total_tasks,
            "planned_mh": round(planned_mh, 1),
            "actual_mh": round(actual_mh, 1),
            "plan_pct": plan_pct,
            "mh_variance": round(actual_mh - planned_mh, 1),
            "ot_hours": ot_hours,
            "ot_ratio": ot_ratio,
            "blocker_count": blocker_count,
            "productive_ratio": productive_ratio,
            "ftc": ftc,
            "avg_cycle": avg_cycle,
            "unassigned": unassigned_count,
            "assigned_count": total_tasks - unassigned_count,
            "completion_rate": completion_rate,
            "remaining_mh": round(max(0, planned_mh - actual_mh), 1),
        },
        "task_status_bar": {
            **{
                status: round(count / total_tasks * 100) if total_tasks else 0
                for status, count in status_counts.items()
            },
            "counts": status_counts,
        },
        "blockers_data": {
            "count": blocker_count,
            "items": [
                {
                    "task_text": item["task_text"],
                    "critical_issue": item["critical_issue"],
                    "days_blocked": item["days_blocked"],
                }
                for item in blocker_rows[:5]
            ],
            "avg_age": (
                round(sum(item["days_blocked"] for item in blocker_rows) / len(blocker_rows), 1)
                if blocker_rows
                else 0
            ),
            "max_age": max((item["days_blocked"] for item in blocker_rows), default=0),
        },
        "workers_data": _build_ssr_workers(workers),
        "burndown_data": ssr_burndown,
        "api_summary": {
            "work_package_id": work_package.id,
            "rfo_no": work_package.rfo_no,
            "title": work_package.title,
            "aircraft": {
                "ac_reg": ac.ac_reg if ac else None,
                "airline": ac.airline if ac else None,
            },
            "tasks": {
                "total": total_tasks,
                "by_status": status_counts,
                "total_mh": round(actual_mh, 1),
            },
            "ot": {
                "total_requests": len(ot_rows),
                "total_approved_minutes": total_approved_minutes,
                "by_status": ot_by_status,
            },
        },
        "api_metrics": {
            "work_package_id": work_package.id,
            "total_tasks": total_tasks,
            "planned_mh": round(planned_mh, 1),
            "actual_mh": round(actual_mh, 1),
            "mh_variance": round(actual_mh - planned_mh, 1),
            "ot_hours": ot_hours,
            "ot_ratio_pct": ot_ratio,
            "productive_ratio_pct": productive_ratio,
            "first_time_completion_pct": ftc,
            "avg_cycle_time_weeks": avg_cycle,
            "blocker_count": blocker_count,
            "unassigned_count": unassigned_count,
        },
        "api_blockers": {
            "work_package_id": work_package.id,
            "count": len(blocker_rows),
            "blockers": blocker_rows,
        },
        "api_workers": {
            "work_package_id": work_package.id,
            "workers": workers,
        },
        "api_burndown": api_burndown,
    }


async def _load_aircraft_map(
    db: AsyncSession,
    work_packages: list[WorkPackage],
) -> dict[int, Aircraft]:
    aircraft_ids = {wp.aircraft_id for wp in work_packages if wp.aircraft_id}
    if not aircraft_ids:
        return {}
    aircraft_rows = (
        await db.execute(select(Aircraft).where(Aircraft.id.in_(aircraft_ids)))
    ).scalars().all()
    return {aircraft.id: aircraft for aircraft in aircraft_rows}


async def _load_selector_stats(
    db: AsyncSession,
    work_package_ids: list[int],
) -> dict[int, dict]:
    if not work_package_ids:
        return {}
    rows = (
        await db.execute(
            select(
                TaskItem.work_package_id,
                func.count().label("cnt"),
                func.coalesce(func.sum(TaskItem.planned_mh), 0).label("mh"),
            )
            .where(
                TaskItem.work_package_id.in_(work_package_ids),
                TaskItem.is_active == True,  # noqa: E712
            )
            .group_by(TaskItem.work_package_id)
        )
    ).all()
    return {
        row[0]: {"count": row[1], "mh": round(float(row[2]), 1)}
        for row in rows
    }


async def _build_worker_rows(
    db: AsyncSession,
    worker_data: dict[int | None, dict],
) -> list[dict]:
    user_ids = [worker_id for worker_id in worker_data if worker_id is not None]
    users_map: dict[int, User] = {}
    if user_ids:
        users = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        users_map = {user.id: user for user in users}

    workers = []
    for worker_id, data in worker_data.items():
        user = users_map.get(worker_id) if worker_id else None
        workers.append(
            {
                "worker_id": worker_id,
                "name": user.name if user else "Unassigned",
                "employee_no": user.employee_no if user else None,
                "task_count": data["task_count"],
                "mh_total": round(data["mh_total"], 1),
            }
        )
    workers.sort(key=lambda item: -item["mh_total"])
    return workers


async def _build_api_burndown(
    db: AsyncSession,
    work_package_id: int,
    rfo_no: str | None,
    planned_mh: float,
    tasks: list[TaskItem],
) -> dict:
    task_ids = [task.id for task in tasks]
    weeks: list[dict] = []
    if task_ids:
        snapshots = (
            await db.execute(
                select(TaskSnapshot)
                .where(
                    TaskSnapshot.task_id.in_(task_ids),
                    TaskSnapshot.is_deleted == False,  # noqa: E712
                )
                .order_by(TaskSnapshot.meeting_date, TaskSnapshot.id)
            )
        ).scalars().all()
        by_week: dict[date, float] = {}
        for snapshot in snapshots:
            by_week[snapshot.meeting_date] = by_week.get(snapshot.meeting_date, 0) + float(
                snapshot.mh_incurred_hours or 0
            )

        cumulative = 0.0
        for meeting_date in sorted(by_week.keys()):
            cumulative += by_week[meeting_date]
            weeks.append(
                {
                    "week": meeting_date.isoformat(),
                    "cumulative_mh": round(cumulative, 1),
                    "remaining_mh": round(max(0, planned_mh - cumulative), 1),
                }
            )

    return {
        "work_package_id": work_package_id,
        "rfo_no": rfo_no,
        "planned_mh": round(planned_mh, 1),
        "weeks": weeks,
    }


def _build_ssr_workers(workers: list[dict]) -> dict:
    total_alloc_mh = sum(worker["mh_total"] for worker in workers) or 1
    navy_shades = ["bg-navy-600", "bg-navy-400", "bg-navy-200", "bg-navy-100", "bg-navy-50"]
    items = []
    navy_idx = 0
    for worker in workers:
        is_unassigned = worker["worker_id"] is None
        item = {
            "name": worker["name"],
            "employee_no": worker["employee_no"],
            "count": worker["task_count"],
            "mh": worker["mh_total"],
            "pct": round(worker["mh_total"] / total_alloc_mh * 100) if total_alloc_mh else 0,
            "is_unassigned": is_unassigned,
        }
        if is_unassigned:
            item["bar_color"] = "bg-st-red"
            item["bar_opacity"] = "opacity-50"
        else:
            item["bar_color"] = navy_shades[min(navy_idx, len(navy_shades) - 1)]
            item["bar_opacity"] = ""
            navy_idx += 1
        items.append(item)

    return {
        "items": items[:6],
        "unassigned_tasks": sum(item["count"] for item in items if item["is_unassigned"]),
    }


def _build_ssr_burndown(
    weeks: list[dict],
    planned_mh: float,
    total_tasks: int,
    completion_rate: float,
    plan_pct: int,
) -> dict:
    burndown_weeks: list[dict] = []
    max_mh = planned_mh or 1
    bar_max = 90
    for idx, week in enumerate(weeks):
        cumulative = float(week["cumulative_mh"])
        remaining = float(week["remaining_mh"])
        burndown_weeks.append(
            {
                "label": f"W{idx + 1}",
                "date": week["week"],
                "actual": round(cumulative, 1),
                "remaining": round(remaining, 1),
                "actual_px": max(4, min(bar_max, int(cumulative / max_mh * bar_max))),
                "remaining_px": max(4, min(bar_max, int(remaining / max_mh * bar_max))),
                "is_current": idx == len(weeks) - 1,
                "is_forecast": False,
            }
        )

    if burndown_weeks and burndown_weeks[-1]["remaining"] > 0:
        remaining = burndown_weeks[-1]["remaining"]
        burndown_weeks.append(
            {
                "label": f"W{len(burndown_weeks) + 1}",
                "date": None,
                "actual": round(planned_mh, 1),
                "remaining": round(remaining, 1),
                "actual_px": 0,
                "remaining_px": max(4, min(bar_max, int(remaining / max_mh * bar_max))),
                "is_current": False,
                "is_forecast": True,
            }
        )

    pace = "on track"
    if plan_pct > 100:
        pace = "over budget"
    elif total_tasks and completion_rate < 30 and plan_pct > 60:
        pace = "behind"

    final_remaining = burndown_weeks[-1]["remaining"] if burndown_weeks else round(planned_mh, 1)
    if burndown_weeks and burndown_weeks[-1]["is_forecast"] and len(burndown_weeks) > 1:
        final_remaining = burndown_weeks[-2]["remaining"]

    return {
        "planned_mh": round(planned_mh, 1),
        "remaining_mh": round(final_remaining, 1),
        "pace": pace,
        "weeks": burndown_weeks,
    }
