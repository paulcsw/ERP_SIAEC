"""Stats & RFO API tests (Branch 11 — commit 4)."""
from datetime import date, datetime, time, timedelta, timezone

import pytest
from tests.conftest import CSRF_HEADERS


# ── Helpers ─────────────────────────────────────────────────────────────

NOW = datetime.now(timezone.utc)
HTMX_HEADERS = {"HX-Request": "true"}


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


async def _grant_shop_access(db_factory, *, user_id: int, shop_id: int, access: str = "VIEW"):
    from app.models.user_shop_access import UserShopAccess

    async with db_factory() as s:
        row = UserShopAccess(user_id=user_id, shop_id=shop_id, access=access, granted_by=1)
        s.add(row)
        await s.commit()


async def _seed_config_value(db_factory, *, key: str, value: str):
    from app.models.system_config import SystemConfig

    async with db_factory() as s:
        s.add(SystemConfig(key=key, value=value, updated_by=1))
        await s.commit()


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
    """GET /rfo?id=X redirects to /rfo/{id} (backward compat)."""
    wp_id = rfo_env["wp_id"]
    # Query-param form now redirects to path form
    resp = await async_client.get(f"/rfo?id={wp_id}", follow_redirects=False)
    assert resp.status_code == 302
    assert f"/rfo/{wp_id}" in resp.headers["location"]
    # Following the redirect should render successfully
    resp2 = await async_client.get(f"/rfo/{wp_id}")
    assert resp2.status_code == 200
    assert "RFO-001" in resp2.text


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
    from app.models.reference import WorkPackage
    from sqlalchemy import select

    wp_id = await _seed_wp(db)
    shop_id = await _seed_shop(db)
    await _grant_shop_access(db, user_id=3, shop_id=shop_id, access="VIEW")

    async with db() as s:
        wp = (await s.execute(select(WorkPackage).where(WorkPackage.id == wp_id))).scalar_one()
        ac_id = wp.aircraft_id

    resp = await async_client.get(f"/tasks/entry/mobile/m4?ac_id={ac_id}&meeting_date=2026-03-10")
    assert resp.status_code == 200
    html = resp.text
    assert "Add Task" in html
    assert 'id="m4-shop-id"' in html
    assert 'id="m4-wp-id"' in html
    assert "credentials: 'same-origin'" in html
    assert "Test Worker" in html
    assert "Other Team Worker" not in html


async def test_mobile_m4_forbidden_without_create_access(worker_client, db):
    """GET /tasks/entry/mobile/m4 should reject users without editable shop access."""
    from app.models.reference import WorkPackage
    from sqlalchemy import select

    wp_id = await _seed_wp(db)
    shop_id = await _seed_shop(db)
    await _grant_shop_access(db, user_id=3, shop_id=shop_id, access="VIEW")

    async with db() as s:
        wp = (await s.execute(select(WorkPackage).where(WorkPackage.id == wp_id))).scalar_one()
        ac_id = wp.aircraft_id

    resp = await worker_client.get(f"/tasks/entry/mobile/m4?ac_id={ac_id}&meeting_date=2026-03-10")
    assert resp.status_code == 403
    assert resp.json()["code"] == "SHOP_ACCESS_DENIED"


async def test_task_entry_scopes_shop_visibility(worker_client, db):
    """Data Entry SSR should not expose tasks outside worker domain."""
    from sqlalchemy import select
    from app.models.shop import Shop
    from app.models.task import TaskItem

    wp_id = await _seed_wp(db)
    async with db() as s:
        shop_visible = Shop(code="SMA", name="Shop A", created_at=NOW)
        shop_hidden = Shop(code="SMB", name="Shop B", created_at=NOW)
        s.add(shop_visible)
        s.add(shop_hidden)
        await s.flush()
        visible_shop_id = shop_visible.id
        hidden_shop_id = shop_hidden.id
        await s.commit()

    visible_task_id = await _seed_task(db, wp_id=wp_id, shop_id=visible_shop_id, worker_id=3)
    hidden_task_id = await _seed_task(db, wp_id=wp_id, shop_id=hidden_shop_id, worker_id=4)
    await _seed_snapshot(db, task_id=visible_task_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")
    await _seed_snapshot(db, task_id=hidden_task_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")
    await _grant_shop_access(db, user_id=3, shop_id=visible_shop_id, access="VIEW")

    async with db() as s:
        visible_task = (await s.execute(select(TaskItem).where(TaskItem.id == visible_task_id))).scalar_one()
        hidden_task = (await s.execute(select(TaskItem).where(TaskItem.id == hidden_task_id))).scalar_one()
        visible_task.task_text = "Visible entry task"
        hidden_task.task_text = "Hidden entry task"
        await s.commit()

    resp = await worker_client.get("/tasks/entry?ac=9V-TST")
    assert resp.status_code == 200
    assert "Visible entry task" in resp.text
    assert "Hidden entry task" not in resp.text


async def test_mobile_m5_forbidden_outside_scope(worker_client, db):
    from app.models.shop import Shop

    wp_id = await _seed_wp(db)
    async with db() as s:
        hidden_shop = Shop(code="SMH", name="Hidden Shop", created_at=NOW)
        s.add(hidden_shop)
        await s.flush()
        hidden_shop_id = hidden_shop.id
        await s.commit()

    task_id = await _seed_task(db, wp_id=wp_id, shop_id=hidden_shop_id, worker_id=4)
    snap_id = await _seed_snapshot(db, task_id=task_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")

    resp = await worker_client.get(f"/tasks/entry/mobile/m5?snapshot_id={snap_id}&ac=9V-TST")
    assert resp.status_code == 403
    assert resp.json()["code"] == "SHOP_ACCESS_DENIED"


async def test_mobile_m5_allows_assigned_worker_without_shop_row(worker_client, db):
    from app.models.shop import Shop

    wp_id = await _seed_wp(db)
    async with db() as s:
        shop = Shop(code="SMAW", name="Assigned Shop", created_at=NOW)
        s.add(shop)
        await s.flush()
        shop_id = shop.id
        await s.commit()

    task_id = await _seed_task(db, wp_id=wp_id, shop_id=shop_id, worker_id=3)
    snap_id = await _seed_snapshot(db, task_id=task_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")

    resp = await worker_client.get(f"/tasks/entry/mobile/m5?snapshot_id={snap_id}&ac=9V-TST")
    assert resp.status_code == 200
    assert "Test task" in resp.text


async def test_mobile_m2_routes_readonly_and_editable_tasks_differ(worker_client, db):
    """Mobile M2 should route editable tasks to m3 and read-only tasks to m5."""
    from sqlalchemy import select

    from app.models.shop import Shop
    from app.models.task import TaskItem

    wp_id = await _seed_wp(db)
    async with db() as s:
        shop_edit = Shop(code="SME", name="Editable Shop", created_at=NOW)
        shop_readonly = Shop(code="SMR", name="Readonly Shop", created_at=NOW)
        s.add(shop_edit)
        s.add(shop_readonly)
        await s.flush()
        shop_edit_id = shop_edit.id
        shop_readonly_id = shop_readonly.id
        await s.commit()

    task_edit_id = await _seed_task(db, wp_id=wp_id, shop_id=shop_edit_id, worker_id=3)
    task_readonly_id = await _seed_task(db, wp_id=wp_id, shop_id=shop_readonly_id, worker_id=3)
    snap_edit_id = await _seed_snapshot(db, task_id=task_edit_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")
    snap_readonly_id = await _seed_snapshot(db, task_id=task_readonly_id, meeting_date=date(2026, 3, 10), status="WAITING")
    await _grant_shop_access(db, user_id=3, shop_id=shop_edit_id, access="EDIT")

    async with db() as s:
        editable_task = (await s.execute(select(TaskItem).where(TaskItem.id == task_edit_id))).scalar_one()
        readonly_task = (await s.execute(select(TaskItem).where(TaskItem.id == task_readonly_id))).scalar_one()
        editable_task.task_text = "Editable mobile task"
        readonly_task.task_text = "Readonly mobile task"
        await s.commit()

    resp = await worker_client.get("/tasks/entry/mobile/m2?ac=9V-TST&meeting_date=2026-03-10")
    assert resp.status_code == 200
    html = resp.text
    assert "Editable mobile task" in html
    assert "Readonly mobile task" in html
    assert f'/tasks/entry/mobile/m3?snapshot_id={snap_edit_id}' in html
    assert f'/tasks/entry/mobile/m5?snapshot_id={snap_readonly_id}' in html
    assert f'/tasks/entry?ac=9V-TST&amp;edit={task_edit_id}' in html
    assert f'/tasks/entry?ac=9V-TST&amp;meeting_date=2026-03-10' in html


async def test_mobile_m3_readonly_task_falls_back_to_detail(worker_client, db):
    """Mobile M3 direct access should fall back to the read-only detail stage when the task is not editable."""
    from app.models.shop import Shop

    wp_id = await _seed_wp(db)
    async with db() as s:
        shop = Shop(code="SMD", name="Detail Only Shop", created_at=NOW)
        s.add(shop)
        await s.flush()
        shop_id = shop.id
        await s.commit()

    task_id = await _seed_task(db, wp_id=wp_id, shop_id=shop_id, worker_id=3)
    snap_id = await _seed_snapshot(db, task_id=task_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")

    resp = await worker_client.get(
        f"/tasks/entry/mobile/m3?snapshot_id={snap_id}&ac=9V-TST&meeting_date=2026-03-10"
    )
    assert resp.status_code == 200
    assert 'id="mob-m5"' in resp.text
    assert 'id="mob-m3"' not in resp.text
    assert "Back to Tasks" in resp.text
    assert "Quick Update" not in resp.text


async def test_mobile_badges_scope_to_visible_domain(db):
    """Mobile badge counts should exclude tasks outside visible domain."""
    from sqlalchemy import select
    from app.models.shop import Shop
    from app.models.task import TaskItem
    from app.views.tasks import _compute_mob_badges

    wp_id = await _seed_wp(db)
    async with db() as s:
        visible_shop = Shop(code="SBC1", name="Badge Shop 1", created_at=NOW)
        hidden_shop = Shop(code="SBC2", name="Badge Shop 2", created_at=NOW)
        s.add(visible_shop)
        s.add(hidden_shop)
        await s.flush()
        visible_shop_id = visible_shop.id
        hidden_shop_id = hidden_shop.id
        await s.commit()

    visible_task_id = await _seed_task(db, wp_id=wp_id, shop_id=visible_shop_id, worker_id=3)
    hidden_task_id = await _seed_task(db, wp_id=wp_id, shop_id=hidden_shop_id, worker_id=4)
    await _seed_snapshot(db, task_id=visible_task_id, meeting_date=date(2026, 3, 10), status="NOT_STARTED")
    await _seed_snapshot(db, task_id=hidden_task_id, meeting_date=date(2026, 3, 10), status="NOT_STARTED")
    await _grant_shop_access(db, user_id=3, shop_id=visible_shop_id, access="VIEW")

    async with db() as s:
        visible_task = (await s.execute(select(TaskItem).where(TaskItem.id == visible_task_id))).scalar_one()
        hidden_task = (await s.execute(select(TaskItem).where(TaskItem.id == hidden_task_id))).scalar_one()
        visible_task.distributed_at = NOW
        hidden_task.distributed_at = NOW
        await s.commit()

    async with db() as s:
        badges = await _compute_mob_badges(s, {"user_id": 3, "roles": ["WORKER"], "team": "Sheet Metal"})
    assert badges["mob_badge_tasks"] == 1


# ── Dashboard SSR tests ──────────────────────────────────────────────────

async def test_dashboard_root_redirect(async_client, db):
    """GET / redirects to /dashboard."""
    resp = await async_client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/dashboard" in resp.headers["location"]


async def test_dashboard_page(async_client, db):
    """GET /dashboard returns 200 with KPI widgets."""
    resp = await async_client.get("/dashboard")
    assert resp.status_code == 200
    body = resp.text
    assert "Dashboard" in body
    assert "Active Tasks" in body
    assert "OT Pending" in body
    assert "OT Endorsed" in body
    assert "Critical Issues" in body
    assert "Total MH" in body
    assert "Monthly OT Quota" in body
    assert "RFO Progress" in body
    assert "OT Approval Pipeline" in body


async def test_dashboard_unauthenticated(async_anon_client, db):
    """Unauthenticated user gets 401 on /dashboard."""
    resp = await async_anon_client.get("/dashboard")
    assert resp.status_code == 401


# ── Task Manager enhanced SSR tests ──────────────────────────────────

async def test_task_manager_page(async_client, db):
    """GET /tasks returns 200 with all 3 view containers and filters."""
    resp = await async_client.get("/tasks")
    assert resp.status_code == 200
    body = resp.text
    assert "Task Manager" in body
    # 3 view tabs
    assert "task-view-table" in body
    assert "task-view-kanban" in body
    assert "task-view-rfo" in body
    # Progress bar
    assert "tasks" in body
    # Filters
    assert "All Shops" in body
    assert "All Status" in body
    # Modals
    assert "modal-new-task" in body
    assert "modal-import-rfo" in body
    assert "modal-bulk-assign" in body
    # Detail panel
    assert "split-detail" in body


async def test_task_manager_with_data(rfo_env, async_client, db):
    """GET /tasks with seeded data shows tasks and summary stats."""
    resp = await async_client.get("/tasks")
    assert resp.status_code == 200
    body = resp.text
    # Should show tasks from rfo_env
    assert "NOT_STARTED" in body or "Not Started" in body
    # Summary bar should have MH
    assert "MH" in body


async def test_task_manager_status_filter(async_client, db):
    """GET /tasks?status=COMPLETED returns 200."""
    resp = await async_client.get("/tasks?status=COMPLETED")
    assert resp.status_code == 200


async def test_task_manager_shell_assets_render_inside_main_wrap(async_client, db):
    """Task Manager page-specific assets must render inside #main-wrap for HTMX sidebar swaps."""
    resp = await async_client.get("/tasks")
    assert resp.status_code == 200
    body = resp.text
    main_wrap = body.split('<div class="main-wrap" id="main-wrap">', 1)[1].split('<div id="mobile-app"', 1)[0]
    assert ".split-detail{background:#fff" in main_wrap
    assert "function selectTaskRow" in main_wrap
    assert 'id="split-detail" class="split-detail w-full sm:w-[380px] overflow-y-auto"' in body
    assert 'id="split-backdrop" class="split-backdrop"' in body


async def test_task_entry_defaults_meeting_date_from_config(async_client, db):
    """Data Entry should default to configured meeting_current_date when visible rows exist."""
    from app.models.reference import WorkPackage
    from sqlalchemy import select

    await _seed_config_value(db, key="meeting_current_date", value="2026-03-03")
    wp_id = await _seed_wp(db)
    shop_id = await _seed_shop(db)

    async with db() as s:
        wp = (await s.execute(select(WorkPackage).where(WorkPackage.id == wp_id))).scalar_one()
        ac_id = wp.aircraft_id

    task_old = await _seed_task(db, wp_id=wp_id, shop_id=shop_id, ac_id=ac_id)
    task_new = await _seed_task(db, wp_id=wp_id, shop_id=shop_id, ac_id=ac_id)
    async with db() as s:
        from app.models.task import TaskItem
        old_row = (await s.execute(select(TaskItem).where(TaskItem.id == task_old))).scalar_one()
        new_row = (await s.execute(select(TaskItem).where(TaskItem.id == task_new))).scalar_one()
        old_row.task_text = "Configured week task"
        new_row.task_text = "Later week task"
        await s.commit()

    await _seed_snapshot(db, task_id=task_old, meeting_date=date(2026, 3, 3), status="IN_PROGRESS")
    await _seed_snapshot(db, task_id=task_new, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")

    resp = await async_client.get("/tasks/entry?ac=9V-TST")
    assert resp.status_code == 200
    body = resp.text
    assert '<option value="2026-03-03" selected>' in body
    assert "Configured week task" in body
    assert "Later week task" not in body


async def test_task_entry_falls_back_to_latest_visible_meeting_date(async_client, db):
    """Data Entry should fall back to the latest visible snapshot week if config has no visible rows."""
    from app.models.reference import WorkPackage
    from sqlalchemy import select

    await _seed_config_value(db, key="meeting_current_date", value="2026-03-17")
    wp_id = await _seed_wp(db)
    shop_id = await _seed_shop(db)

    async with db() as s:
        wp = (await s.execute(select(WorkPackage).where(WorkPackage.id == wp_id))).scalar_one()
        ac_id = wp.aircraft_id

    task_old = await _seed_task(db, wp_id=wp_id, shop_id=shop_id, ac_id=ac_id)
    task_new = await _seed_task(db, wp_id=wp_id, shop_id=shop_id, ac_id=ac_id)
    async with db() as s:
        from app.models.task import TaskItem
        old_row = (await s.execute(select(TaskItem).where(TaskItem.id == task_old))).scalar_one()
        new_row = (await s.execute(select(TaskItem).where(TaskItem.id == task_new))).scalar_one()
        old_row.task_text = "Older visible week task"
        new_row.task_text = "Latest visible week task"
        await s.commit()

    await _seed_snapshot(db, task_id=task_old, meeting_date=date(2026, 3, 3), status="IN_PROGRESS")
    await _seed_snapshot(db, task_id=task_new, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")

    resp = await async_client.get("/tasks/entry?ac=9V-TST")
    assert resp.status_code == 200
    body = resp.text
    assert '<option value="2026-03-10" selected>' in body
    assert "Latest visible week task" in body
    assert "Older visible week task" not in body


async def test_task_entry_search_filters_by_aircraft_reg_and_task_text(async_client, db):
    """Data Entry search should match both aircraft registration and task text."""
    from app.models.reference import Aircraft, WorkPackage
    from app.models.task import TaskItem
    from sqlalchemy import select

    shop_id = await _seed_shop(db)

    async with db() as s:
        ac1 = Aircraft(ac_reg="9V-AAA", airline="Test Air", created_at=NOW)
        ac2 = Aircraft(ac_reg="9V-BBB", airline="Test Air", created_at=NOW)
        s.add_all([ac1, ac2])
        await s.flush()
        wp1 = WorkPackage(
            aircraft_id=ac1.id, rfo_no="RFO-AAA", title="WP AAA",
            start_date=date(2026, 3, 1), end_date=date(2026, 3, 31), created_at=NOW,
        )
        wp2 = WorkPackage(
            aircraft_id=ac2.id, rfo_no="RFO-BBB", title="WP BBB",
            start_date=date(2026, 3, 1), end_date=date(2026, 3, 31), created_at=NOW,
        )
        s.add_all([wp1, wp2])
        await s.flush()
        task1 = TaskItem(
            aircraft_id=ac1.id, shop_id=shop_id, work_package_id=wp1.id,
            planned_mh=10, task_text="Alpha torque check", is_active=True,
            created_by=1, created_at=NOW,
        )
        task2 = TaskItem(
            aircraft_id=ac2.id, shop_id=shop_id, work_package_id=wp2.id,
            planned_mh=10, task_text="Zulu inspection", is_active=True,
            created_by=1, created_at=NOW,
        )
        s.add_all([task1, task2])
        await s.flush()
        task1_id = task1.id
        task2_id = task2.id
        await s.commit()

    await _seed_snapshot(db, task_id=task1_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")
    await _seed_snapshot(db, task_id=task2_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")

    reg_resp = await async_client.get("/tasks/entry?search=9V-BBB")
    assert reg_resp.status_code == 200
    assert "9V-BBB" in reg_resp.text
    assert "9V-AAA" not in reg_resp.text

    text_resp = await async_client.get("/tasks/entry?search=Alpha")
    assert text_resp.status_code == 200
    assert "9V-AAA" in text_resp.text
    assert "9V-BBB" not in text_resp.text


async def test_task_entry_mobile_partials_preserve_state_and_new_filter_icon(async_client, db):
    """Mobile Data Entry partials should preserve filter params and render the bell-style New filter."""
    wp_id = await _seed_wp(db)
    shop_id = await _seed_shop(db)
    task_id = await _seed_task(db, wp_id=wp_id, shop_id=shop_id)
    snap_id = await _seed_snapshot(db, task_id=task_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")

    m1 = await async_client.get("/tasks/entry/mobile/m1?meeting_date=2026-03-10&search=Test&status=IN_PROGRESS&quick=new")
    assert m1.status_code == 200
    assert 'hx-push-url="/tasks/entry?meeting_date=2026-03-10' in m1.text
    assert "search=Test" in m1.text
    assert "quick=new" in m1.text
    assert 'M15 17h5l-1.405-1.405A2.032' in m1.text
    assert 'hx-push-url="true"' not in m1.text
    assert "mobSubmitEntryFilters" in m1.text

    m3 = await async_client.get(
        f"/tasks/entry/mobile/m3?snapshot_id={snap_id}&ac=9V-TST&meeting_date=2026-03-10&search=Test&status=IN_PROGRESS&quick=new"
    )
    assert m3.status_code == 200
    assert 'id="mob-meeting-date" value="2026-03-10"' in m3.text
    assert 'id="mob-search-filter" value="Test"' in m3.text
    assert "search=Test" in m3.text
    assert "quick=new" in m3.text


async def test_desktop_new_filter_uses_bell_icon(async_client, db):
    """Desktop Data Entry New quick filter should use the bell icon semantics."""
    wp_id = await _seed_wp(db)
    shop_id = await _seed_shop(db)
    task_id = await _seed_task(db, wp_id=wp_id, shop_id=shop_id)
    await _seed_snapshot(db, task_id=task_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")

    resp = await async_client.get("/tasks/entry?ac=9V-TST")
    assert resp.status_code == 200
    assert 'M15 17h5l-1.405-1.405A2.032' in resp.text
    assert "New" in resp.text


async def test_shared_top_bar_notification_bell_removed(async_client, db):
    """Shared desktop header should not render the inert notification bell."""
    resp = await async_client.get("/dashboard")
    assert resp.status_code == 200
    assert 'M15 17h5l-1.405-1.405A2.032' not in resp.text


async def test_shared_header_search_form_targets_global_search(async_client, db):
    """Shared desktop header search should submit to the global search page."""
    resp = await async_client.get("/dashboard")
    assert resp.status_code == 200
    body = resp.text
    assert 'action="/search"' in body
    assert 'method="get"' in body
    assert 'id="desktop-global-search"' in body
    assert 'name="q"' in body
    assert 'hx-get="/search"' in body
    assert 'hx-target="#main-wrap"' in body
    assert 'hx-select="#main-wrap > *"' in body
    assert 'hx-push-url="true"' in body


async def test_global_search_page_aggregates_task_ot_and_rfo_results(async_client, db):
    """Global search should aggregate matches across task, OT, and RFO domains."""
    from sqlalchemy import select

    from app.models.ot import OtRequest
    from app.models.reference import WorkPackage
    from app.models.task import TaskItem

    wp_id = await _seed_wp(db)
    shop_id = await _seed_shop(db)
    task_id = await _seed_task(db, wp_id=wp_id, shop_id=shop_id, worker_id=3)
    await _seed_snapshot(db, task_id=task_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")
    ot_id = await _seed_ot(
        db,
        user_id=1,
        wp_id=wp_id,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=120,
    )

    async with db() as s:
        wp = (await s.execute(select(WorkPackage).where(WorkPackage.id == wp_id))).scalar_one()
        task = (await s.execute(select(TaskItem).where(TaskItem.id == task_id))).scalar_one()
        ot = (await s.execute(select(OtRequest).where(OtRequest.id == ot_id))).scalar_one()
        wp.rfo_no = "RFO-HYD"
        wp.title = "Hydraulic Package"
        task.task_text = "Hydraulic leak check"
        ot.reason_text = "Hydraulic support coverage"
        await s.commit()

    resp = await async_client.get("/search?q=Hydraulic")
    assert resp.status_code == 200
    body = resp.text
    assert "Global Search" in body
    assert "Task Results" in body
    assert "OT Results" in body
    assert "RFO Results" in body
    assert "Hydraulic leak check" in body
    assert "Hydraulic support coverage" in body
    assert "RFO-HYD" in body
    assert f'href="/ot/{ot_id}"' in body
    assert 'href="/ot?search=Hydraulic"' in body
    assert f'href="/rfo?id={wp_id}"' in body
    assert "/tasks/entry?ac=9V-TST" in body


async def test_global_search_worker_scope_and_role_visibility(worker_client, db):
    """Worker global search should respect task and OT scope and omit RFO results."""
    from sqlalchemy import select

    from app.models.ot import OtRequest
    from app.models.reference import WorkPackage
    from app.models.shop import Shop
    from app.models.task import TaskItem

    wp_id = await _seed_wp(db)
    async with db() as s:
        visible_shop = Shop(code="SCH1", name="Scope Visible", created_at=NOW)
        hidden_shop = Shop(code="SCH2", name="Scope Hidden", created_at=NOW)
        s.add_all([visible_shop, hidden_shop])
        await s.flush()
        visible_shop_id = visible_shop.id
        hidden_shop_id = hidden_shop.id
        wp = (await s.execute(select(WorkPackage).where(WorkPackage.id == wp_id))).scalar_one()
        wp.rfo_no = "RFO-SCOPE"
        wp.title = "Scope Package"
        await s.commit()

    visible_task_id = await _seed_task(db, wp_id=wp_id, shop_id=visible_shop_id, worker_id=3)
    hidden_task_id = await _seed_task(db, wp_id=wp_id, shop_id=hidden_shop_id, worker_id=4)
    await _seed_snapshot(db, task_id=visible_task_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")
    await _seed_snapshot(db, task_id=hidden_task_id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS")
    own_ot_id = await _seed_ot(
        db,
        user_id=3,
        wp_id=wp_id,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=60,
    )
    hidden_ot_id = await _seed_ot(
        db,
        user_id=4,
        wp_id=wp_id,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=60,
    )

    async with db() as s:
        visible_task = (await s.execute(select(TaskItem).where(TaskItem.id == visible_task_id))).scalar_one()
        hidden_task = (await s.execute(select(TaskItem).where(TaskItem.id == hidden_task_id))).scalar_one()
        own_ot = (await s.execute(select(OtRequest).where(OtRequest.id == own_ot_id))).scalar_one()
        hidden_ot = (await s.execute(select(OtRequest).where(OtRequest.id == hidden_ot_id))).scalar_one()
        visible_task.task_text = "Visible scope task"
        hidden_task.task_text = "Hidden scope task"
        own_ot.reason_text = "Visible scope OT"
        hidden_ot.reason_text = "Hidden scope OT"
        await s.commit()

    resp = await worker_client.get("/search?q=scope")
    assert resp.status_code == 200
    body = resp.text
    assert "Task Results" in body
    assert "OT Results" in body
    assert "Visible scope task" in body
    assert "Hidden scope task" not in body
    assert "Visible scope OT" in body
    assert "Hidden scope OT" not in body
    assert "RFO Results" not in body


async def test_ot_list_search_filters_and_detail_links_preserve_state(async_client, db):
    """OT list should filter by search/date text and carry list state into detail links."""
    from sqlalchemy import select

    from app.models.ot import OtRequest
    from app.models.reference import WorkPackage

    wp_id = await _seed_wp(db)
    first_ot_id = await _seed_ot(
        db,
        user_id=1,
        wp_id=wp_id,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=60,
    )
    second_ot_id = await _seed_ot(
        db,
        user_id=1,
        wp_id=wp_id,
        dt="2026-03-15",
        reason="OTHER",
        status="PENDING",
        minutes=60,
    )

    async with db() as s:
        wp = (await s.execute(select(WorkPackage).where(WorkPackage.id == wp_id))).scalar_one()
        first_ot = (await s.execute(select(OtRequest).where(OtRequest.id == first_ot_id))).scalar_one()
        second_ot = (await s.execute(select(OtRequest).where(OtRequest.id == second_ot_id))).scalar_one()
        wp.rfo_no = "RFO-OTSEARCH"
        first_ot.reason_text = "Hydraulic support"
        second_ot.reason_text = "Cabin repaint"
        await s.commit()

    resp = await async_client.get("/ot?search=Hydraulic&status=PENDING&date_from=2026-03-09&date_to=2026-03-12")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="ot-search"' in body
    assert 'value="Hydraulic"' in body
    assert 'id="ot-date-from"' in body
    assert 'value="2026-03-09"' in body
    assert 'id="ot-date-to"' in body
    assert 'value="2026-03-12"' in body
    assert f"OT-{first_ot_id:03d}" in body
    assert f"OT-{second_ot_id:03d}" not in body
    assert f'hx-get="/ot/{first_ot_id}?status=PENDING&amp;search=Hydraulic&amp;date_from=2026-03-09&amp;date_to=2026-03-12"' in body
    assert 'search=Hydraulic' in body


async def test_ot_detail_back_link_preserves_list_state(async_client, db):
    """OT detail breadcrumb and back link should preserve OT list filters."""
    ot_id = await _seed_ot(
        db,
        user_id=1,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=60,
    )

    resp = await async_client.get(f"/ot/{ot_id}?status=PENDING&search=Hydraulic&date_from=2026-03-09&date_to=2026-03-12&page=3")
    assert resp.status_code == 200
    body = resp.text
    assert 'href="/ot?status=PENDING&amp;search=Hydraulic&amp;date_from=2026-03-09&amp;date_to=2026-03-12&amp;page=3"' in body


async def test_ot_list_export_button_visibility_matches_role(async_client, worker_client, sup_client, db):
    """Desktop OT list should only show CSV export to SUPERVISOR+ roles."""
    worker_resp = await worker_client.get("/ot")
    assert worker_resp.status_code == 200
    assert "Export CSV" not in worker_resp.text

    sup_resp = await sup_client.get("/ot")
    assert sup_resp.status_code == 200
    assert "Export CSV" in sup_resp.text

    admin_resp = await async_client.get("/ot")
    assert admin_resp.status_code == 200
    assert "Export CSV" in admin_resp.text


async def test_ot_detail_refresh_target_preserves_list_state(async_client, db):
    """OT detail actions should refresh the same filtered detail URL, not drop back to a bare OT detail route."""
    ot_id = await _seed_ot(
        db,
        user_id=1,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=60,
    )

    resp = await async_client.get(
        f"/ot/{ot_id}?status=PENDING&search=Hydraulic&date_from=2026-03-09&date_to=2026-03-12&page=3"
    )
    assert resp.status_code == 200
    body = resp.text
    assert (
        f'data-detail-href="/ot/{ot_id}?status=PENDING&amp;search=Hydraulic&amp;date_from=2026-03-09&amp;date_to=2026-03-12&amp;page=3"'
        in body
    )
    assert "const otDetailRefreshHref =" in body
    assert "refreshOtDesktop(otDetailRefreshHref);" in body


async def test_ot_detail_worker_forbidden_outside_scope(worker_client, db):
    """Worker SSR OT detail should return an explicit 403 screen for someone else's request."""
    ot_id = await _seed_ot(
        db,
        user_id=4,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=60,
    )

    resp = await worker_client.get(f"/ot/{ot_id}")
    assert resp.status_code == 403
    body = resp.text
    assert "Access denied" in body
    assert "You do not have permission to view this OT request." in body
    assert "Other Team Worker" not in body
    assert "Cancel Request" not in body
    assert 'onclick="endorseOt(' not in body
    assert 'onclick="approveOt(' not in body


async def test_ot_detail_cross_team_supervisor_forbidden_outside_scope(sup_client, db):
    """Cross-team supervisor SSR OT detail should return an explicit 403 screen."""
    ot_id = await _seed_ot(
        db,
        user_id=4,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=60,
    )

    resp = await sup_client.get(f"/ot/{ot_id}")
    assert resp.status_code == 403
    body = resp.text
    assert "Access denied" in body
    assert "Other Team Worker" not in body
    assert 'onclick="endorseOt(' not in body


async def test_ot_mobile_detail_worker_forbidden_outside_scope(worker_client, db):
    """Worker HTMX mobile OT detail should return an explicit 403 screen for someone else's request."""
    ot_id = await _seed_ot(
        db,
        user_id=4,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=60,
    )

    resp = await worker_client.get(f"/ot/detail/{ot_id}", headers=HTMX_HEADERS)
    assert resp.status_code == 403
    body = resp.text
    assert "Access denied" in body
    assert "Other Team Worker" not in body
    assert "Cancel Request" not in body


async def test_ot_mobile_detail_cross_team_supervisor_forbidden_outside_scope(sup_client, db):
    """Cross-team supervisor HTMX mobile OT detail should return an explicit 403 screen."""
    ot_id = await _seed_ot(
        db,
        user_id=4,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=60,
    )

    resp = await sup_client.get(f"/ot/detail/{ot_id}", headers=HTMX_HEADERS)
    assert resp.status_code == 403
    body = resp.text
    assert "Access denied" in body
    assert "Other Team Worker" not in body


async def test_ot_submit_admin_roster_includes_all_workers(async_client, db):
    """Admin OT submit roster should include workers across all teams in desktop and mobile submit views."""
    resp = await async_client.get("/ot/new")
    assert resp.status_code == 200
    assert "All Teams" in resp.text
    assert "Test Worker" in resp.text
    assert "Other Team Worker" in resp.text

    mobile_resp = await async_client.get("/ot/segment/o1", headers=HTMX_HEADERS)
    assert mobile_resp.status_code == 200
    assert "Test Worker" in mobile_resp.text
    assert "Other Team Worker" in mobile_resp.text


async def test_ot_submit_supervisor_roster_stays_team_scoped(sup_client, db):
    """Supervisor OT submit roster should remain scoped to the supervisor's own team."""
    resp = await sup_client.get("/ot/new")
    assert resp.status_code == 200
    assert "Test Worker" in resp.text
    assert "Other Team Worker" not in resp.text

    mobile_resp = await sup_client.get("/ot/segment/o1", headers=HTMX_HEADERS)
    assert mobile_resp.status_code == 200
    assert "Test Worker" in mobile_resp.text
    assert "Other Team Worker" not in mobile_resp.text


async def test_ot_mobile_history_detail_and_back_preserve_status_filter(async_client, db):
    """Mobile OT history should carry the selected status filter into detail and back/cancel paths."""
    ot_id = await _seed_ot(
        db,
        user_id=1,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=60,
    )

    list_resp = await async_client.get("/ot/segment/o2?status=PENDING", headers=HTMX_HEADERS)
    assert list_resp.status_code == 200
    assert f'hx-get="/ot/detail/{ot_id}?status=PENDING"' in list_resp.text
    assert 'hx-push-url="true"' not in list_resp.text

    detail_resp = await async_client.get(f"/ot/detail/{ot_id}?status=PENDING", headers=HTMX_HEADERS)
    assert detail_resp.status_code == 200
    body = detail_resp.text
    assert 'data-back-href="/ot/segment/o2?status=PENDING"' in body
    assert 'hx-get="/ot/segment/o2?status=PENDING"' in body
    assert "refreshMobOtHistory()" in body
    assert 'hx-push-url="true"' not in body


async def test_ot_mobile_history_paginates_and_preserves_page_state(async_client, db):
    """Mobile OT history should paginate beyond 50 items and preserve page/status on detail back paths."""
    ot_ids = []
    for _ in range(55):
        ot_ids.append(
            await _seed_ot(
                db,
                user_id=1,
                dt="2026-03-10",
                reason="OTHER",
                status="PENDING",
                minutes=60,
            )
        )

    newest_ot_id = ot_ids[-1]
    oldest_ot_id = ot_ids[0]

    first_page = await async_client.get("/ot/segment/o2?status=PENDING", headers=HTMX_HEADERS)
    assert first_page.status_code == 200
    assert f'hx-get="/ot/detail/{newest_ot_id}?status=PENDING"' in first_page.text
    assert f'hx-get="/ot/detail/{oldest_ot_id}?status=PENDING"' not in first_page.text
    assert "Page 1 of 3" in first_page.text
    assert '/ot/segment/o2?page=2' in first_page.text
    assert 'status=PENDING' in first_page.text

    third_page = await async_client.get("/ot/segment/o2?status=PENDING&page=3", headers=HTMX_HEADERS)
    assert third_page.status_code == 200
    assert f'hx-get="/ot/detail/{oldest_ot_id}?status=PENDING&amp;page=3"' in third_page.text
    assert f'hx-get="/ot/detail/{newest_ot_id}?status=PENDING"' not in third_page.text
    assert "Page 3 of 3" in third_page.text
    assert '/ot/segment/o2?page=2' in third_page.text
    assert 'status=PENDING' in third_page.text
    assert f'hx-get="/ot/detail/{oldest_ot_id}?status=PENDING&amp;page=3"' in third_page.text

    detail_resp = await async_client.get(
        f"/ot/detail/{oldest_ot_id}?status=PENDING&page=3",
        headers=HTMX_HEADERS,
    )
    assert detail_resp.status_code == 200
    body = detail_resp.text
    assert 'data-back-href="/ot/segment/o2?status=PENDING&amp;page=3"' in body
    assert 'hx-get="/ot/segment/o2?status=PENDING&amp;page=3"' in body
    assert 'var mobOtBackHref = "/ot/segment/o2?status=PENDING\\u0026page=3";' in body


async def test_ot_mobile_fragment_routes_redirect_without_htmx(async_client, db):
    """Direct non-HTMX access to OT mobile partial routes should redirect to the OT shell."""
    ot_id = await _seed_ot(
        db,
        user_id=1,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=60,
    )

    segment_resp = await async_client.get("/ot/segment/o2?status=PENDING")
    assert segment_resp.status_code == 302
    assert segment_resp.headers["location"] == "/ot"

    detail_resp = await async_client.get(f"/ot/detail/{ot_id}?status=PENDING&page=2")
    assert detail_resp.status_code == 302
    assert detail_resp.headers["location"] == "/ot"


async def test_ot_approve_queue_excludes_admin_owned_endorsed_requests(async_client, db):
    """Admin approve queues should exclude the admin's own ENDORSED OT on desktop and mobile."""
    own_ot_id = await _seed_ot(
        db,
        user_id=1,
        dt="2026-03-10",
        reason="OTHER",
        status="ENDORSED",
        minutes=60,
    )
    other_ot_id = await _seed_ot(
        db,
        user_id=3,
        dt="2026-03-10",
        reason="OTHER",
        status="ENDORSED",
        minutes=60,
    )
    await _seed_approval(db, own_ot_id, stage="ENDORSE", action="APPROVE", approver_id=2)
    await _seed_approval(db, other_ot_id, stage="ENDORSE", action="APPROVE", approver_id=2)

    desktop_resp = await async_client.get("/admin/ot-approve")
    assert desktop_resp.status_code == 200
    desktop_body = desktop_resp.text
    assert f"OT-{other_ot_id:03d}" in desktop_body
    assert f"OT-{own_ot_id:03d}" not in desktop_body
    assert "1 awaiting approval" in desktop_body

    mobile_resp = await async_client.get("/ot/segment/o3", headers=HTMX_HEADERS)
    assert mobile_resp.status_code == 200
    mobile_body = mobile_resp.text
    assert f"OT-{other_ot_id:03d}" in mobile_body
    assert f"OT-{own_ot_id:03d}" not in mobile_body
    assert "1 endorsed request awaiting final approval" in mobile_body


async def test_ot_mobile_o3_cards_include_context_and_approval_metadata(async_client, sup_client, db):
    """Mobile O3 cards should include OT context, justification, and approval metadata."""
    from sqlalchemy import select

    from app.models.ot import OtRequest

    wp_id = await _seed_wp(db)
    pending_ot_id = await _seed_ot(
        db,
        user_id=3,
        wp_id=wp_id,
        dt="2026-03-10",
        reason="OTHER",
        status="PENDING",
        minutes=90,
    )
    endorsed_ot_id = await _seed_ot(
        db,
        user_id=3,
        wp_id=wp_id,
        dt="2026-03-11",
        reason="OTHER",
        status="ENDORSED",
        minutes=120,
    )
    await _seed_approval(
        db,
        endorsed_ot_id,
        stage="ENDORSE",
        action="APPROVE",
        approver_id=2,
        acted_at=datetime(2026, 3, 11, 8, 30, tzinfo=timezone.utc),
    )

    async with db() as s:
        pending_ot = (await s.execute(select(OtRequest).where(OtRequest.id == pending_ot_id))).scalar_one()
        endorsed_ot = (await s.execute(select(OtRequest).where(OtRequest.id == endorsed_ot_id))).scalar_one()
        pending_ot.reason_text = "Weekend coverage for line recovery"
        endorsed_ot.reason_text = "Weekend engine run extension"
        await s.commit()

    sup_resp = await sup_client.get("/ot/segment/o3", headers=HTMX_HEADERS)
    assert sup_resp.status_code == 200
    sup_body = sup_resp.text
    assert f"OT-{pending_ot_id:03d}" in sup_body
    assert "Test Worker (E003)" in sup_body
    assert "Sheet Metal" in sup_body
    assert "Weekend coverage for line recovery" in sup_body
    assert "RFO-001" in sup_body

    admin_resp = await async_client.get("/ot/segment/o3", headers=HTMX_HEADERS)
    assert admin_resp.status_code == 200
    admin_body = admin_resp.text
    assert f"OT-{endorsed_ot_id:03d}" in admin_body
    assert "Test Worker (E003)" in admin_body
    assert "Sheet Metal" in admin_body
    assert "Weekend engine run extension" in admin_body
    assert "RFO-001" in admin_body
    assert "Test Supervisor" in admin_body
    assert "08:30" in admin_body


async def test_ot_approve_templates_refresh_queue_on_stale_errors(async_client, db):
    """Desktop and mobile approve UIs should refresh the queue after 403/409 stale action errors."""
    desktop_resp = await async_client.get("/admin/ot-approve")
    assert desktop_resp.status_code == 200
    desktop_body = desktop_resp.text
    assert "function shouldRefreshOtApproveQueue(err)" in desktop_body
    assert "if (shouldRefreshOtApproveQueue(err)) refreshOtApprove();" in desktop_body

    mobile_resp = await async_client.get("/ot/segment/o3", headers=HTMX_HEADERS)
    assert mobile_resp.status_code == 200
    mobile_body = mobile_resp.text
    assert "function shouldRefreshMobOtApproveSegment(err)" in mobile_body
    assert "if (shouldRefreshMobOtApproveSegment(err)) refreshMobOtApproveSegment();" in mobile_body


async def test_ot_new_page_contains_mobile_responsive_submit_layout(async_client, db):
    """OT submit page should include the responsive mobile layout guards."""
    resp = await async_client.get("/ot")
    assert resp.status_code == 200
    body = resp.text
    assert "mob-ot-time-grid" in body
    assert "#ot-o1{overflow-x:hidden}" in body
    assert "max-width:100%" in body
    assert "@media(max-width:380px)" in body


# ── Fix A: Dashboard RFO card navigation ──────────────────────────────────

async def test_dashboard_rfo_links_use_path_route(rfo_env, async_client, db):
    """Dashboard RFO cards link to /rfo/<wp_id> (SSOT §11 path route)."""
    resp = await async_client.get("/dashboard")
    assert resp.status_code == 200
    body = resp.text
    # Should NOT contain /rfo/RFO- pattern (rfo_no in URL is wrong)
    import re
    broken_links = re.findall(r'href="/rfo/[A-Z]', body)
    assert broken_links == [], f"Found broken /rfo/<rfo_no> links: {broken_links}"
    # Should contain /rfo/<int> path pattern (correct SSOT route)
    wp_id = rfo_env["wp_id"]
    assert f"/rfo/{wp_id}" in body or "No active work packages" in body


# ── Fix B: Mobile logout uses POST + CSRF ──────────────────────────────────

async def test_more_logout_is_not_get_link(async_client, db):
    """More tab logout is a JS button that POSTs, not a GET <a> link."""
    resp = await async_client.get("/more")
    assert resp.status_code == 200
    body = resp.text
    # Must NOT have a direct GET link to /logout
    assert 'href="/logout"' not in body
    # Must have the POST-based logout function
    assert "mobLogout" in body
    assert "POST" in body


async def test_logout_post_works(async_client, db):
    """POST /logout with CSRF clears session and redirects."""
    resp = await async_client.post("/logout", headers=CSRF_HEADERS, follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("location", "")


# ── Fix C: Mobile task tab disabled for workers without shop_access ────────

async def test_mobile_task_tab_disabled_no_shop_access(worker_client, db):
    """Worker without shop_access sees disabled Tasks tab on OT page."""
    resp = await worker_client.get("/ot")
    assert resp.status_code == 200
    body = resp.text
    assert "pointer-events:none" in body or "aria-disabled" in body


async def test_mobile_task_tab_enabled_with_shop_access(worker_client, db):
    """Worker with VIEW shop_access sees active Tasks tab."""
    from app.models.shop import Shop
    from app.models.user_shop_access import UserShopAccess

    async with db() as s:
        shop = Shop(code="TST", name="Test Shop", created_at=NOW)
        s.add(shop)
        await s.flush()
        access = UserShopAccess(
            user_id=3, shop_id=shop.id, access="VIEW",
            granted_by=1, granted_at=NOW,
        )
        s.add(access)
        await s.commit()

    resp = await worker_client.get("/ot")
    assert resp.status_code == 200
    body = resp.text
    # Tasks tab should be active/clickable (no pointer-events:none on the tasks tab)
    assert 'href="/tasks/entry"' in body


# ── Fix E/F: Sidebar role gating for OT Stats and RFO Detail ──────────────

async def test_sidebar_ot_stats_hidden_for_worker(worker_client, db):
    """Worker should not see OT Dashboard link in sidebar."""
    resp = await worker_client.get("/ot")
    assert resp.status_code == 200
    body = resp.text
    assert 'href="/stats/ot"' not in body


async def test_sidebar_rfo_hidden_for_worker(worker_client, db):
    """Worker should not see RFO Detail link in sidebar."""
    resp = await worker_client.get("/ot")
    assert resp.status_code == 200
    body = resp.text
    assert 'href="/rfo"' not in body


async def test_sidebar_ot_stats_visible_for_admin(async_client, db):
    """Admin should see OT Dashboard link in sidebar."""
    resp = await async_client.get("/dashboard")
    assert resp.status_code == 200
    assert 'href="/stats/ot"' in resp.text


async def test_sidebar_rfo_visible_for_supervisor(sup_client, db):
    """Supervisor should see RFO Detail link in sidebar."""
    resp = await sup_client.get("/ot")
    assert resp.status_code == 200
    assert 'href="/rfo"' in resp.text
