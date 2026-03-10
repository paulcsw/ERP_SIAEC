"""Stats & RFO API tests (Branch 11 — commit 4)."""
from datetime import date, datetime, time, timedelta, timezone

import pytest
from tests.conftest import CSRF_HEADERS


# ── Helpers ─────────────────────────────────────────────────────────────

NOW = datetime.now(timezone.utc)


async def _seed_ot(db_factory, *, user_id=1, wp_id=None, dt="2026-03-11",
                   start="18:00", end="20:00", reason="BACKLOG",
                   status="APPROVED", minutes=120):
    """Seed an OT request directly in the DB."""
    from app.models.ot import OtRequest
    async with db_factory() as s:
        h1, m1 = map(int, start.split(":"))
        h2, m2 = map(int, end.split(":"))
        r = OtRequest(
            user_id=user_id, work_package_id=wp_id,
            date=date.fromisoformat(dt),
            start_time=time(h1, m1), end_time=time(h2, m2),
            requested_minutes=minutes, reason_code=reason, status=status,
            created_at=NOW, updated_at=NOW,
        )
        s.add(r)
        await s.commit()
        await s.refresh(r)
        return r.id


async def _seed_approval(db_factory, ot_id, *, stage="APPROVE", action="APPROVE",
                         approver_id=1, acted_at=None):
    """Seed an OT approval record."""
    from app.models.ot import OtApproval
    async with db_factory() as s:
        a = OtApproval(
            ot_request_id=ot_id, approver_id=approver_id,
            stage=stage, action=action,
            acted_at=acted_at or NOW,
        )
        s.add(a)
        await s.commit()


async def _seed_wp(db_factory):
    """Seed an aircraft + work package; return wp.id."""
    from app.models.reference import Aircraft, WorkPackage
    async with db_factory() as s:
        ac = Aircraft(ac_reg="9V-TST", airline="Test Air", created_at=NOW)
        s.add(ac)
        await s.flush()
        wp = WorkPackage(
            aircraft_id=ac.id, rfo_no="RFO-001", title="Test WP",
            start_date=date(2026, 3, 1), end_date=date(2026, 3, 31),
            created_at=NOW,
        )
        s.add(wp)
        await s.commit()
        await s.refresh(wp)
        return wp.id


async def _seed_shop(db_factory):
    """Seed a shop; return shop.id."""
    from app.models.shop import Shop
    async with db_factory() as s:
        shop = Shop(code="SM", name="Sheet Metal", created_at=NOW)
        s.add(shop)
        await s.commit()
        await s.refresh(shop)
        return shop.id


async def _seed_task(db_factory, *, wp_id, shop_id, ac_id=None,
                     worker_id=None, planned_mh=10):
    """Seed a task_item; return task.id."""
    from app.models.task import TaskItem
    # Get aircraft_id from wp if not provided
    if ac_id is None:
        from app.models.reference import WorkPackage
        from sqlalchemy import select
        async with db_factory() as s:
            wp = (await s.execute(
                select(WorkPackage).where(WorkPackage.id == wp_id)
            )).scalar_one()
            ac_id = wp.aircraft_id

    async with db_factory() as s:
        ti = TaskItem(
            aircraft_id=ac_id, shop_id=shop_id, work_package_id=wp_id,
            assigned_worker_id=worker_id, planned_mh=planned_mh,
            task_text="Test task", is_active=True, created_by=1, created_at=NOW,
        )
        s.add(ti)
        await s.commit()
        await s.refresh(ti)
        return ti.id


async def _seed_snapshot(db_factory, *, task_id, meeting_date, status="IN_PROGRESS",
                         mh=5.0, has_issue=False, critical_issue=None):
    """Seed a task snapshot."""
    from app.models.task import TaskSnapshot
    async with db_factory() as s:
        snap = TaskSnapshot(
            task_id=task_id, meeting_date=meeting_date, status=status,
            mh_incurred_hours=mh, has_issue=has_issue,
            critical_issue=critical_issue,
            version=1, last_updated_by=1, last_updated_at=NOW, created_at=NOW,
        )
        s.add(snap)
        await s.commit()
        await s.refresh(snap)
        return snap.id


# ═══════════════════════════════════════════════════════════════════════
#  OT STATISTICS API
# ═══════════════════════════════════════════════════════════════════════


# ── /api/stats/ot-summary ──────────────────────────────────────────────


async def test_ot_summary_worker_forbidden(worker_client, db):
    """WORKER cannot access stats endpoints."""
    resp = await worker_client.get("/api/stats/ot-summary")
    assert resp.status_code == 403


async def test_ot_summary_supervisor_ok(sup_client, db):
    """SUPERVISOR can access ot-summary."""
    await _seed_ot(db, user_id=2, status="APPROVED", minutes=120)
    resp = await sup_client.get("/api/stats/ot-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_hours" in data
    assert "approved_hours" in data
    assert "pending_endorsed_hours" in data
    assert "avg_turnaround_hours" in data


async def test_ot_summary_admin_all_teams(async_client, db):
    """ADMIN sees all teams."""
    await _seed_ot(db, user_id=1, dt="2026-03-11", minutes=60, status="APPROVED")
    await _seed_ot(db, user_id=4, dt="2026-03-12", minutes=90, status="APPROVED")  # different team
    resp = await async_client.get("/api/stats/ot-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_hours"] == 2.5  # (60+90)/60
    assert data["total_requests"] == 2


async def test_ot_summary_date_filter(async_client, db):
    """date_from / date_to filtering."""
    await _seed_ot(db, user_id=1, dt="2026-03-05", minutes=60)
    await _seed_ot(db, user_id=1, dt="2026-03-15", minutes=120)
    resp = await async_client.get(
        "/api/stats/ot-summary?date_from=2026-03-10&date_to=2026-03-20"
    )
    assert resp.status_code == 200
    assert resp.json()["total_hours"] == 2.0  # only the 120-min one


async def test_ot_summary_supervisor_team_scope(sup_client, db):
    """SUPERVISOR only sees own team's data."""
    # user_id=2 is in Sheet Metal team, user_id=4 is in Airframe
    await _seed_ot(db, user_id=2, dt="2026-03-11", minutes=60)
    await _seed_ot(db, user_id=4, dt="2026-03-12", minutes=120)  # Airframe
    resp = await sup_client.get("/api/stats/ot-summary")
    assert resp.status_code == 200
    # Should only see Sheet Metal user's 60 min = 1.0h
    assert resp.json()["total_hours"] == 1.0


async def test_ot_summary_avg_turnaround(async_client, db):
    """avg_turnaround_hours computed from approval records."""
    ot_id = await _seed_ot(db, user_id=1, dt="2026-03-11", minutes=60, status="APPROVED")
    await _seed_approval(db, ot_id, acted_at=NOW + timedelta(hours=2))
    resp = await async_client.get("/api/stats/ot-summary")
    assert resp.status_code == 200
    assert resp.json()["avg_turnaround_hours"] > 0


# ── /api/stats/ot-monthly-usage ───────────────────────────────────────


async def test_monthly_usage_default_month(async_client, db):
    """Without ?month=, uses current month."""
    await _seed_ot(db, user_id=1, dt="2026-03-11", minutes=120)
    resp = await async_client.get("/api/stats/ot-monthly-usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["month"] == "2026-03"
    assert data["limit_minutes"] == 4320
    assert isinstance(data["users"], list)


async def test_monthly_usage_explicit_month(async_client, db):
    """?month=2026-03 filters correctly."""
    await _seed_ot(db, user_id=1, dt="2026-03-11", minutes=180)
    await _seed_ot(db, user_id=1, dt="2026-02-11", minutes=60)  # Feb — excluded
    resp = await async_client.get("/api/stats/ot-monthly-usage?month=2026-03")
    assert resp.status_code == 200
    data = resp.json()
    # Find admin user in list
    admin_entry = next((u for u in data["users"] if u["user_id"] == 1), None)
    assert admin_entry is not None
    assert admin_entry["used_minutes"] == 180


async def test_monthly_usage_sorted_desc(async_client, db):
    """Users sorted by usage_pct descending."""
    await _seed_ot(db, user_id=1, dt="2026-03-11", minutes=200)
    await _seed_ot(db, user_id=3, dt="2026-03-12", minutes=60)  # worker, same team
    resp = await async_client.get("/api/stats/ot-monthly-usage?month=2026-03")
    assert resp.status_code == 200
    users = resp.json()["users"]
    if len(users) >= 2:
        assert users[0]["usage_pct"] >= users[1]["usage_pct"]


async def test_monthly_usage_worker_forbidden(worker_client, db):
    resp = await worker_client.get("/api/stats/ot-monthly-usage")
    assert resp.status_code == 403


# ── /api/stats/ot-by-reason ──────────────────────────────────────────


async def test_by_reason_aggregation(async_client, db):
    """Groups by reason_code with hours and pct."""
    await _seed_ot(db, user_id=1, dt="2026-03-11", minutes=120, reason="BACKLOG")
    await _seed_ot(db, user_id=1, dt="2026-03-12", minutes=60, reason="AOG")
    resp = await async_client.get("/api/stats/ot-by-reason?month=2026-03")
    assert resp.status_code == 200
    data = resp.json()
    assert data["month"] == "2026-03"
    breakdown = data["breakdown"]
    assert len(breakdown) == 2
    # BACKLOG is larger, so should come first
    assert breakdown[0]["reason_code"] == "BACKLOG"
    assert breakdown[0]["hours"] == 2.0
    assert breakdown[1]["reason_code"] == "AOG"
    assert breakdown[1]["hours"] == 1.0
    # pcts should sum to ~100
    total_pct = sum(b["pct"] for b in breakdown)
    assert 99 <= total_pct <= 101


async def test_by_reason_empty(async_client, db):
    """No data → empty breakdown."""
    resp = await async_client.get("/api/stats/ot-by-reason?month=2025-01")
    assert resp.status_code == 200
    assert resp.json()["breakdown"] == []


async def test_by_reason_team_filter_admin(async_client, db):
    """ADMIN can filter by team."""
    await _seed_ot(db, user_id=1, dt="2026-03-11", minutes=120, reason="BACKLOG")
    await _seed_ot(db, user_id=4, dt="2026-03-12", minutes=60, reason="AOG")  # Airframe
    resp = await async_client.get(
        "/api/stats/ot-by-reason?month=2026-03&team=Airframe"
    )
    assert resp.status_code == 200
    breakdown = resp.json()["breakdown"]
    assert len(breakdown) == 1
    assert breakdown[0]["reason_code"] == "AOG"


# ── /api/stats/ot-weekly-trend ───────────────────────────────────────


async def test_weekly_trend_labels(async_client, db):
    """Returns weekly buckets for the month."""
    await _seed_ot(db, user_id=1, dt="2026-03-02", minutes=120)
    await _seed_ot(db, user_id=1, dt="2026-03-10", minutes=60)
    resp = await async_client.get("/api/stats/ot-weekly-trend?month=2026-03")
    assert resp.status_code == 200
    data = resp.json()
    assert data["month"] == "2026-03"
    weeks = data["weeks"]
    assert len(weeks) >= 4  # March has 4-5 weeks
    for w in weeks:
        assert "week" in w
        assert "label" in w
        assert "hours" in w


async def test_weekly_trend_hours(async_client, db):
    """Hours placed in correct week bucket."""
    # March 1 is Sunday → W1 = Mar 1-7
    await _seed_ot(db, user_id=1, dt="2026-03-01", minutes=120)
    resp = await async_client.get("/api/stats/ot-weekly-trend?month=2026-03")
    assert resp.status_code == 200
    weeks = resp.json()["weeks"]
    # First week should have 2.0h
    assert weeks[0]["hours"] == 2.0


async def test_weekly_trend_worker_forbidden(worker_client, db):
    resp = await worker_client.get("/api/stats/ot-weekly-trend")
    assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════
#  RFO METRICS API
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
async def rfo_env(db):
    """Seed a full RFO environment: aircraft + WP + shop + tasks + snapshots + OT."""
    wp_id = await _seed_wp(db)
    shop_id = await _seed_shop(db)

    # Task 1: IN_PROGRESS, assigned to worker 3, 5 MH
    t1 = await _seed_task(db, wp_id=wp_id, shop_id=shop_id, worker_id=3, planned_mh=10)
    await _seed_snapshot(db, task_id=t1, meeting_date=date(2026, 3, 3),
                         status="IN_PROGRESS", mh=5)

    # Task 2: COMPLETED, assigned to worker 3, 8 MH
    t2 = await _seed_task(db, wp_id=wp_id, shop_id=shop_id, worker_id=3, planned_mh=10)
    await _seed_snapshot(db, task_id=t2, meeting_date=date(2026, 3, 3),
                         status="IN_PROGRESS", mh=3)
    await _seed_snapshot(db, task_id=t2, meeting_date=date(2026, 3, 10),
                         status="COMPLETED", mh=8)

    # Task 3: WAITING with blocker, unassigned, 2 MH
    t3 = await _seed_task(db, wp_id=wp_id, shop_id=shop_id, worker_id=None, planned_mh=5)
    await _seed_snapshot(db, task_id=t3, meeting_date=date(2026, 3, 3),
                         status="WAITING", mh=2, has_issue=True,
                         critical_issue="Missing parts")

    # OT linked to this WP
    await _seed_ot(db, user_id=3, wp_id=wp_id, dt="2026-03-05",
                   minutes=120, status="APPROVED")

    return {"wp_id": wp_id, "shop_id": shop_id, "t1": t1, "t2": t2, "t3": t3}


# ── /api/rfo/{id}/summary ─────────────────────────────────────────────


async def test_rfo_summary(async_client, rfo_env):
    """RFO summary returns task counts and OT info."""
    wp_id = rfo_env["wp_id"]
    resp = await async_client.get(f"/api/rfo/{wp_id}/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["work_package_id"] == wp_id
    assert data["rfo_no"] == "RFO-001"
    assert data["aircraft"]["ac_reg"] == "9V-TST"
    assert data["tasks"]["total"] == 3
    assert data["tasks"]["by_status"]["IN_PROGRESS"] == 1
    assert data["tasks"]["by_status"]["COMPLETED"] == 1
    assert data["tasks"]["by_status"]["WAITING"] == 1
    assert data["ot"]["total_requests"] == 1


async def test_rfo_summary_404(async_client, db):
    """Non-existent WP → 404."""
    resp = await async_client.get("/api/rfo/99999/summary")
    assert resp.status_code == 404


async def test_rfo_summary_worker_forbidden(worker_client, db):
    wp_id = await _seed_wp(db)
    resp = await worker_client.get(f"/api/rfo/{wp_id}/summary")
    assert resp.status_code == 403


# ── /api/rfo/{id}/metrics ─────────────────────────────────────────────


async def test_rfo_metrics_kpi(async_client, rfo_env):
    """§7.4 KPI calculations."""
    wp_id = rfo_env["wp_id"]
    resp = await async_client.get(f"/api/rfo/{wp_id}/metrics")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_tasks"] == 3
    assert data["planned_mh"] == 25.0  # 10+10+5
    # actual_mh: latest snapshots: t1=5, t2=8, t3=2 → 15
    assert data["actual_mh"] == 15.0
    assert data["mh_variance"] == -10.0  # 15 - 25
    assert data["ot_hours"] == 2.0  # 120 min
    assert data["blocker_count"] == 1  # t3
    assert data["unassigned_count"] == 1  # t3

    # Productive ratio: (actual - waiting) / actual * 100 = (15-2)/15 * 100
    assert data["productive_ratio_pct"] == pytest.approx(86.7, abs=0.1)

    # OT ratio: ot_hours / actual_mh * 100 = 2/15*100
    assert data["ot_ratio_pct"] == pytest.approx(13.3, abs=0.1)

    # FTC: completed / total * 100 = 1/3 * 100
    assert data["first_time_completion_pct"] == pytest.approx(33.3, abs=0.1)

    # Avg cycle: t2 went IP@Mar3 → COMPLETED@Mar10 = 7 days = 1.0 week
    assert data["avg_cycle_time_weeks"] == 1.0


async def test_rfo_metrics_empty_wp(async_client, db):
    """WP with no tasks → zeroed metrics."""
    wp_id = await _seed_wp(db)
    resp = await async_client.get(f"/api/rfo/{wp_id}/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_tasks"] == 0
    assert data["actual_mh"] == 0
    assert data["productive_ratio_pct"] == 0


# ── /api/rfo/{id}/blockers ────────────────────────────────────────────


async def test_rfo_blockers(async_client, rfo_env):
    """Returns tasks with WAITING + has_issue."""
    wp_id = rfo_env["wp_id"]
    resp = await async_client.get(f"/api/rfo/{wp_id}/blockers")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    b = data["blockers"][0]
    assert b["task_id"] == rfo_env["t3"]
    assert b["critical_issue"] == "Missing parts"
    assert b["days_blocked"] >= 0
    assert b["mh_incurred_hours"] == 2.0


async def test_rfo_blockers_empty(async_client, db):
    """WP with no blockers → empty list."""
    wp_id = await _seed_wp(db)
    resp = await async_client.get(f"/api/rfo/{wp_id}/blockers")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    assert resp.json()["blockers"] == []


async def test_rfo_blockers_sorted_desc(async_client, db):
    """Blockers sorted by days_blocked descending."""
    wp_id = await _seed_wp(db)
    shop_id = await _seed_shop(db)
    t1 = await _seed_task(db, wp_id=wp_id, shop_id=shop_id, planned_mh=5)
    t2 = await _seed_task(db, wp_id=wp_id, shop_id=shop_id, planned_mh=5)
    # t1 blocked since Mar 1 (older)
    await _seed_snapshot(db, task_id=t1, meeting_date=date(2026, 3, 1),
                         status="WAITING", mh=1, has_issue=True,
                         critical_issue="Old issue")
    # t2 blocked since Mar 8 (newer)
    await _seed_snapshot(db, task_id=t2, meeting_date=date(2026, 3, 8),
                         status="WAITING", mh=1, has_issue=True,
                         critical_issue="New issue")
    resp = await async_client.get(f"/api/rfo/{wp_id}/blockers")
    data = resp.json()
    assert data["count"] == 2
    assert data["blockers"][0]["days_blocked"] >= data["blockers"][1]["days_blocked"]


# ── /api/rfo/{id}/worker-allocation ──────────────────────────────────


async def test_rfo_worker_allocation(async_client, rfo_env):
    """Returns worker distribution."""
    wp_id = rfo_env["wp_id"]
    resp = await async_client.get(f"/api/rfo/{wp_id}/worker-allocation")
    assert resp.status_code == 200
    data = resp.json()
    workers = data["workers"]
    assert len(workers) == 2  # worker 3 + Unassigned

    # worker 3 has t1(5h) + t2(8h) = 13h, Unassigned has t3(2h)
    w3 = next(w for w in workers if w["worker_id"] == 3)
    assert w3["task_count"] == 2
    assert w3["mh_total"] == 13.0
    assert w3["name"] == "Test Worker"

    unassigned = next(w for w in workers if w["worker_id"] is None)
    assert unassigned["task_count"] == 1
    assert unassigned["name"] == "Unassigned"


async def test_rfo_worker_allocation_sorted(async_client, rfo_env):
    """Workers sorted by mh_total descending."""
    wp_id = rfo_env["wp_id"]
    resp = await async_client.get(f"/api/rfo/{wp_id}/worker-allocation")
    workers = resp.json()["workers"]
    for i in range(len(workers) - 1):
        assert workers[i]["mh_total"] >= workers[i + 1]["mh_total"]


# ── /api/rfo/{id}/burndown ──────────────────────────────────────────


async def test_rfo_burndown(async_client, rfo_env):
    """Returns weekly cumulative/remaining MH."""
    wp_id = rfo_env["wp_id"]
    resp = await async_client.get(f"/api/rfo/{wp_id}/burndown")
    assert resp.status_code == 200
    data = resp.json()
    assert data["work_package_id"] == wp_id
    assert data["planned_mh"] == 25.0
    weeks = data["weeks"]
    assert len(weeks) >= 1
    for w in weeks:
        assert "week" in w
        assert "cumulative_mh" in w
        assert "remaining_mh" in w


async def test_rfo_burndown_remaining_decreases(async_client, rfo_env):
    """remaining_mh should be planned - cumulative."""
    wp_id = rfo_env["wp_id"]
    resp = await async_client.get(f"/api/rfo/{wp_id}/burndown")
    weeks = resp.json()["weeks"]
    planned = resp.json()["planned_mh"]
    for w in weeks:
        expected_remaining = round(max(0, planned - w["cumulative_mh"]), 1)
        assert w["remaining_mh"] == expected_remaining


async def test_rfo_burndown_empty(async_client, db):
    """WP with no tasks → empty weeks."""
    wp_id = await _seed_wp(db)
    resp = await async_client.get(f"/api/rfo/{wp_id}/burndown")
    assert resp.status_code == 200
    assert resp.json()["weeks"] == []


# ── SSR view smoke tests ────────────────────────────────────────────


async def test_ot_dashboard_ssr(async_client, db):
    """GET /stats/ot returns HTML."""
    resp = await async_client.get("/stats/ot")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "OT Dashboard" in resp.text


async def test_rfo_detail_ssr(async_client, db):
    """GET /rfo returns HTML."""
    resp = await async_client.get("/rfo")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


async def test_rfo_detail_ssr_with_id(async_client, rfo_env):
    """GET /rfo?id=X returns selected RFO details."""
    wp_id = rfo_env["wp_id"]
    resp = await async_client.get(f"/rfo?id={wp_id}")
    assert resp.status_code == 200
    assert "RFO-001" in resp.text


# ── Admin SSR smoke tests ────────────────────────────────────────────


async def test_admin_users_ssr(async_client, db):
    """GET /admin/users returns HTML with user table."""
    resp = await async_client.get("/admin/users")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Personnel" in resp.text
    assert "E001" in resp.text  # seeded admin


async def test_admin_reference_ssr(async_client, db):
    """GET /admin/reference returns HTML with tabs."""
    resp = await async_client.get("/admin/reference")
    assert resp.status_code == 200
    assert "Reference Data" in resp.text
    assert "Aircraft" in resp.text


async def test_admin_shops_ssr(async_client, db):
    """GET /admin/shops returns HTML."""
    resp = await async_client.get("/admin/shops")
    assert resp.status_code == 200
    assert "Shop Management" in resp.text


async def test_admin_shop_access_ssr(async_client, db):
    """GET /admin/shop-access returns HTML."""
    resp = await async_client.get("/admin/shop-access")
    assert resp.status_code == 200
    assert "Shop Access" in resp.text


async def test_admin_pages_worker_forbidden(worker_client, db):
    """WORKER cannot access admin pages."""
    for url in ["/admin/users", "/admin/reference", "/admin/shops", "/admin/shop-access"]:
        resp = await worker_client.get(url)
        assert resp.status_code == 403, f"{url} should be 403 for WORKER"


async def test_mobile_m4_route(async_client, db):
    """GET /tasks/entry/mobile/m4 returns add-task partial."""
    resp = await async_client.get("/tasks/entry/mobile/m4")
    assert resp.status_code == 200
    assert "Add Task" in resp.text
