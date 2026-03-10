"""RFO Detail SSR views (Branch 11 commit 3)."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.ot import OtRequest
from app.models.reference import Aircraft, WorkPackage
from app.models.task import TaskItem, TaskSnapshot
from app.models.user import User
from app.views import templates

router = APIRouter(tags=["rfo-views"])


def _ctx(request, user, **kw):
    page = kw.pop("active_page", "rfo")
    return {
        "request": request,
        "current_user": {
            "user_id": user["user_id"],
            "display_name": user.get("display_name", ""),
            "roles": user.get("roles", []),
            "team": user.get("team"),
            "employee_no": user.get("employee_no", ""),
        },
        "active_tab": "tasks",
        "page": page,
        **kw,
    }


@router.get("/rfo")
async def rfo_index(
    request: Request,
    id: int | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # RFO selector: list all work packages
    wps = (await db.execute(
        select(WorkPackage).order_by(WorkPackage.id)
    )).scalars().all()

    # Enrich with aircraft info for selector
    ac_map: dict[int, Aircraft] = {}
    ac_ids = set(wp.aircraft_id for wp in wps if wp.aircraft_id)
    if ac_ids:
        acs = (await db.execute(select(Aircraft).where(Aircraft.id.in_(ac_ids)))).scalars().all()
        ac_map = {a.id: a for a in acs}

    rfo_options = []
    for wp in wps:
        ac = ac_map.get(wp.aircraft_id)
        rfo_options.append({
            "id": wp.id,
            "rfo_no": wp.rfo_no or f"WP-{wp.id}",
            "title": wp.title,
            "ac_reg": ac.ac_reg if ac else None,
            "airline": ac.airline if ac else None,
            "status": wp.status,
        })

    # Selected RFO
    selected = None
    metrics = None
    summary_strip = None
    kpi = None
    blockers_data = None
    workers_data = None
    burndown_data = None
    task_status_bar = None

    if id:
        wp = next((w for w in wps if w.id == id), None)
        if wp:
            ac = ac_map.get(wp.aircraft_id)
            selected = {
                "id": wp.id, "rfo_no": wp.rfo_no, "title": wp.title,
                "status": wp.status, "start_date": wp.start_date, "end_date": wp.end_date,
                "priority": wp.priority,
            }
            summary_strip = {
                "ac_reg": ac.ac_reg if ac else "N/A",
                "airline": ac.airline if ac else "N/A",
                "start_date": wp.start_date.isoformat() if wp.start_date else "–",
                "end_date": wp.end_date.isoformat() if wp.end_date else "–",
                "days_remaining": (wp.end_date - date.today()).days if wp.end_date else None,
                "priority": wp.priority or 0,
            }

            # Tasks
            tasks = (await db.execute(
                select(TaskItem).where(TaskItem.work_package_id == wp.id, TaskItem.is_active == True)
            )).scalars().all()

            planned_mh = sum(float(t.planned_mh or 0) for t in tasks)
            actual_mh = 0.0
            waiting_mh = 0.0
            completed = 0
            blocker_count = 0
            unassigned = sum(1 for t in tasks if not t.assigned_worker_id)
            status_counts = {"NOT_STARTED": 0, "IN_PROGRESS": 0, "WAITING": 0, "COMPLETED": 0}
            cycle_times: list[float] = []
            blocker_list: list[dict] = []
            now = datetime.now(timezone.utc)

            worker_agg: dict[int | None, dict] = {}

            for ti in tasks:
                snap = (await db.execute(
                    select(TaskSnapshot)
                    .where(TaskSnapshot.task_id == ti.id, TaskSnapshot.is_deleted == False)
                    .order_by(TaskSnapshot.meeting_date.desc()).limit(1)
                )).scalar_one_or_none()
                if not snap:
                    continue

                mh = float(snap.mh_incurred_hours or 0)
                actual_mh += mh
                status_counts[snap.status] = status_counts.get(snap.status, 0) + 1

                if snap.status == "WAITING":
                    waiting_mh += mh
                    if snap.has_issue:
                        blocker_count += 1
                        days = (now.date() - snap.meeting_date).days if snap.meeting_date else 0
                        blocker_list.append({
                            "task_text": ti.task_text,
                            "critical_issue": snap.critical_issue,
                            "days_blocked": max(0, days),
                        })
                elif snap.status == "COMPLETED":
                    completed += 1

                # Worker allocation
                wid = ti.assigned_worker_id
                if wid not in worker_agg:
                    worker_agg[wid] = {"count": 0, "mh": 0.0}
                worker_agg[wid]["count"] += 1
                worker_agg[wid]["mh"] += mh

                # Cycle time
                first_ip = (await db.execute(
                    select(func.min(TaskSnapshot.meeting_date)).where(
                        TaskSnapshot.task_id == ti.id, TaskSnapshot.status == "IN_PROGRESS",
                        TaskSnapshot.is_deleted == False,
                    )
                )).scalar()
                last_comp = (await db.execute(
                    select(func.max(TaskSnapshot.meeting_date)).where(
                        TaskSnapshot.task_id == ti.id, TaskSnapshot.status == "COMPLETED",
                        TaskSnapshot.is_deleted == False,
                    )
                )).scalar()
                if first_ip and last_comp and last_comp >= first_ip:
                    cycle_times.append((last_comp - first_ip).days / 7.0)

            # OT
            ot_min = (await db.execute(
                select(func.coalesce(func.sum(OtRequest.requested_minutes), 0)).where(
                    OtRequest.work_package_id == wp.id,
                    OtRequest.status.in_(("APPROVED", "ENDORSED", "PENDING")),
                )
            )).scalar() or 0
            ot_hours = round(ot_min / 60, 1)

            total_tasks = len(tasks)
            prod_ratio = round((actual_mh - waiting_mh) / actual_mh * 100, 1) if actual_mh else 0
            ot_ratio = round(ot_hours / actual_mh * 100, 1) if actual_mh else 0
            ftc = round(completed / total_tasks * 100, 1) if total_tasks else 0
            avg_cycle = round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else 0
            plan_pct = round(actual_mh / planned_mh * 100) if planned_mh else 0

            kpi = {
                "total_tasks": total_tasks, "planned_mh": round(planned_mh, 1),
                "actual_mh": round(actual_mh, 1), "plan_pct": plan_pct,
                "mh_variance": round(actual_mh - planned_mh, 1),
                "ot_hours": ot_hours, "ot_ratio": ot_ratio,
                "blocker_count": blocker_count,
                "productive_ratio": prod_ratio, "ftc": ftc, "avg_cycle": avg_cycle,
                "unassigned": unassigned,
            }

            # Task status bar percentages
            task_status_bar = {}
            for s, c in status_counts.items():
                task_status_bar[s] = round(c / total_tasks * 100) if total_tasks else 0
            task_status_bar["counts"] = status_counts

            # Blockers
            blocker_list.sort(key=lambda x: -x["days_blocked"])
            blockers_data = {"count": blocker_count, "items": blocker_list[:5]}

            # Worker allocation
            user_ids = [wid for wid in worker_agg if wid is not None]
            users_map: dict[int, User] = {}
            if user_ids:
                us = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
                users_map = {u.id: u for u in us}

            total_alloc_mh = sum(d["mh"] for d in worker_agg.values()) or 1
            workers_list = []
            for wid, d in worker_agg.items():
                u = users_map.get(wid) if wid else None
                workers_list.append({
                    "name": u.name if u else "Unassigned",
                    "employee_no": u.employee_no if u else None,
                    "count": d["count"], "mh": round(d["mh"], 1),
                    "pct": round(d["mh"] / total_alloc_mh * 100),
                    "is_unassigned": wid is None,
                })
            workers_list.sort(key=lambda x: -x["mh"])
            workers_data = workers_list[:5]

            # Burndown
            task_ids = [t.id for t in tasks]
            burndown_weeks: list[dict] = []
            if task_ids:
                snaps = (await db.execute(
                    select(TaskSnapshot).where(
                        TaskSnapshot.task_id.in_(task_ids), TaskSnapshot.is_deleted == False,
                    ).order_by(TaskSnapshot.meeting_date)
                )).scalars().all()
                by_week: dict[date, float] = {}
                for s in snaps:
                    by_week[s.meeting_date] = by_week.get(s.meeting_date, 0) + float(s.mh_incurred_hours or 0)
                max_mh = planned_mh or 1
                for i, md in enumerate(sorted(by_week.keys())):
                    cum = by_week[md]
                    rem = max(0, planned_mh - cum)
                    burndown_weeks.append({
                        "label": f"W{i+1}", "date": md.isoformat(),
                        "actual": round(cum, 1), "remaining": round(rem, 1),
                        "actual_h": max(4, int(cum / max_mh * 120)),
                        "remaining_h": max(4, int(rem / max_mh * 120)),
                    })
            burndown_data = {"planned_mh": round(planned_mh, 1), "weeks": burndown_weeks}

    return templates.TemplateResponse("rfo/detail.html", _ctx(
        request, current_user,
        rfo_options=rfo_options,
        selected=selected,
        summary_strip=summary_strip,
        kpi=kpi,
        task_status_bar=task_status_bar,
        blockers_data=blockers_data,
        workers_data=workers_data,
        burndown_data=burndown_data,
    ))
