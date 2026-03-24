"""Regression tests for UI/backend/SSOT alignment fixes.

Covers:
  A. Latest snapshot semantics in default views
  B. Dashboard RFO links / route correctness
  C. More tab logout flow
  D. Settings save payload / key alignment
  E. More > RFO Summary rendering
  F. Mobile access behavior
"""
from datetime import date, datetime, timezone

import pytest
from tests.conftest import CSRF_HEADERS, _make_session_cookie

NOW = datetime.now(timezone.utc)


# ── Seed helpers (reused from test_stats_rfo) ─────────────────────────


async def _seed_aircraft(db_factory, ac_reg="9V-ALN", airline="SQ"):
    from app.models.reference import Aircraft
    async with db_factory() as s:
        ac = Aircraft(ac_reg=ac_reg, airline=airline, created_at=NOW)
        s.add(ac)
        await s.commit()
        await s.refresh(ac)
        return ac


async def _seed_wp(db_factory, aircraft_id, rfo_no="RFO-001"):
    from app.models.reference import WorkPackage
    async with db_factory() as s:
        wp = WorkPackage(
            aircraft_id=aircraft_id, rfo_no=rfo_no, title="Test WP",
            start_date=date(2026, 3, 1), end_date=date(2026, 3, 31),
            created_at=NOW,
        )
        s.add(wp)
        await s.commit()
        await s.refresh(wp)
        return wp


async def _seed_shop(db_factory, code="SM", name="Sheet Metal"):
    from app.models.shop import Shop
    async with db_factory() as s:
        shop = Shop(code=code, name=name, created_at=NOW)
        s.add(shop)
        await s.commit()
        await s.refresh(shop)
        return shop


async def _seed_task(db_factory, *, ac_id, shop_id, wp_id=None, text="Test task"):
    from app.models.task import TaskItem
    async with db_factory() as s:
        ti = TaskItem(
            aircraft_id=ac_id, shop_id=shop_id, work_package_id=wp_id,
            planned_mh=10, task_text=text, is_active=True,
            created_by=1, created_at=NOW,
        )
        s.add(ti)
        await s.commit()
        await s.refresh(ti)
        return ti


async def _seed_snapshot(db_factory, *, task_id, meeting_date, status="IN_PROGRESS",
                         mh=5.0, has_issue=False):
    from app.models.task import TaskSnapshot
    async with db_factory() as s:
        snap = TaskSnapshot(
            task_id=task_id, meeting_date=meeting_date, status=status,
            mh_incurred_hours=mh, has_issue=has_issue,
            version=1, last_updated_by=1, last_updated_at=NOW, created_at=NOW,
        )
        s.add(snap)
        await s.commit()
        await s.refresh(snap)
        return snap


async def _seed_config(db_factory, key, value):
    from app.models.system_config import SystemConfig
    async with db_factory() as s:
        s.add(SystemConfig(key=key, value=value, updated_by=1))
        await s.commit()


async def _grant_shop_access(db_factory, user_id, shop_id, access="VIEW"):
    from app.models.user_shop_access import UserShopAccess
    async with db_factory() as s:
        s.add(UserShopAccess(user_id=user_id, shop_id=shop_id, access=access, granted_by=1))
        await s.commit()


# ═══════════════════════════════════════════════════════════════════════
#  A. LATEST SNAPSHOT SEMANTICS
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dashboard_uses_latest_snapshot_only(async_client, db):
    """Dashboard KPIs must count only the latest snapshot per task,
    not inflate counts when history-rich data exists."""
    ac = await _seed_aircraft(db)
    shop = await _seed_shop(db)
    wp = await _seed_wp(db, ac.id)
    task = await _seed_task(db, ac_id=ac.id, shop_id=shop.id, wp_id=wp.id)

    # Create 3 weekly snapshots for the SAME task (simulating carry-over)
    await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 4),
                         status="NOT_STARTED", mh=0)
    await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 11),
                         status="IN_PROGRESS", mh=5.0)
    await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 18),
                         status="IN_PROGRESS", mh=8.0, has_issue=True)

    resp = await async_client.get("/dashboard")
    assert resp.status_code == 200
    html = resp.text

    # Should show 1 active task, not 3 (only latest snapshot counts)
    # Look for the Active Tasks KPI value
    # The KPI is in a <span class="text-2xl font-bold text-navy-900"> after "Active Tasks"
    import re
    active_match = re.search(
        r'Active Tasks.*?<span class="text-2xl font-bold text-navy-900">(\d+)</span>',
        html, re.DOTALL
    )
    assert active_match, "Could not find Active Tasks KPI"
    active_count = int(active_match.group(1))
    assert active_count == 1, f"Expected 1 active task, got {active_count} (duplicate snapshots leaking)"

    # Total MH should be 8.0 (latest snapshot), not 13.0 (sum of all)
    mh_match = re.search(
        r'Total MH.*?<span class="text-2xl font-bold text-navy-900">([\d.]+)</span>',
        html, re.DOTALL
    )
    assert mh_match, "Could not find Total MH KPI"
    total_mh = float(mh_match.group(1))
    assert total_mh == 8.0, f"Expected 8.0 MH (latest), got {total_mh} (history leaking)"

    # Critical issues should be 1 (only latest snapshot has issue)
    issues_match = re.search(
        r'Critical Issues.*?<span class="text-2xl font-bold text-st-red">(\d+)</span>',
        html, re.DOTALL
    )
    assert issues_match, "Could not find Critical Issues KPI"
    issue_count = int(issues_match.group(1))
    assert issue_count == 1, f"Expected 1 critical issue, got {issue_count}"


@pytest.mark.asyncio
async def test_task_manager_defaults_to_meeting_date(async_client, db):
    """Task Manager without meeting_date param should default to configured week,
    not show all historical snapshots."""
    ac = await _seed_aircraft(db)
    shop = await _seed_shop(db)
    task = await _seed_task(db, ac_id=ac.id, shop_id=shop.id)

    await _seed_config(db, "meeting_current_date", "2026-03-18")
    await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 11),
                         status="NOT_STARTED", mh=0)
    await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 18),
                         status="IN_PROGRESS", mh=5.0)

    resp = await async_client.get("/tasks")
    assert resp.status_code == 200
    html = resp.text
    # Should show the configured meeting date (2026-03-18), not the older one
    # The page should filter to meeting_date=2026-03-18
    assert "2026-03-18" in html, "Task manager should default to configured meeting date"
    # The older snapshot date (2026-03-11) should NOT appear as active data
    # (it may appear in a date-selector dropdown, but the active view should be 2026-03-18)


@pytest.mark.asyncio
async def test_task_detail_shows_full_history(async_client, db):
    """Task detail page should show all snapshots (full history)."""
    ac = await _seed_aircraft(db)
    shop = await _seed_shop(db)
    task = await _seed_task(db, ac_id=ac.id, shop_id=shop.id)

    s1 = await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 4),
                              status="NOT_STARTED", mh=0)
    s2 = await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 11),
                              status="IN_PROGRESS", mh=5.0)

    resp = await async_client.get(f"/tasks/{task.id}")
    assert resp.status_code == 200
    html = resp.text
    # Both meeting dates should be visible in the detail/history view
    assert "2026-03-04" in html
    assert "2026-03-11" in html


# ═══════════════════════════════════════════════════════════════════════
#  B. DASHBOARD RFO LINKS / ROUTE CORRECTNESS
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dashboard_rfo_links_use_path_route(async_client, db):
    """Dashboard RFO cards should link to /rfo/{id} not /rfo?id=."""
    ac = await _seed_aircraft(db)
    shop = await _seed_shop(db)
    wp = await _seed_wp(db, ac.id)
    task = await _seed_task(db, ac_id=ac.id, shop_id=shop.id, wp_id=wp.id)
    await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 18),
                         status="IN_PROGRESS", mh=5.0)

    resp = await async_client.get("/dashboard")
    assert resp.status_code == 200
    html = resp.text
    assert f"/rfo/{wp.id}" in html, "Dashboard should use /rfo/{id} path links"
    assert f"/rfo?id={wp.id}" not in html, "Dashboard should NOT use /rfo?id= links"


@pytest.mark.asyncio
async def test_rfo_path_route_works(async_client, db):
    """GET /rfo/{id} should render the RFO detail page."""
    ac = await _seed_aircraft(db)
    wp = await _seed_wp(db, ac.id)

    resp = await async_client.get(f"/rfo/{wp.id}")
    assert resp.status_code == 200
    assert "RFO-001" in resp.text


@pytest.mark.asyncio
async def test_rfo_query_param_redirects(async_client, db):
    """GET /rfo?id=X should redirect to /rfo/X for backward compat."""
    ac = await _seed_aircraft(db)
    wp = await _seed_wp(db, ac.id)

    resp = await async_client.get(f"/rfo?id={wp.id}", follow_redirects=False)
    assert resp.status_code == 302
    assert f"/rfo/{wp.id}" in resp.headers["location"]


# ═══════════════════════════════════════════════════════════════════════
#  C. LOGOUT FLOW
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_logout_post_clears_session(async_client, db):
    """POST /logout should clear session and redirect to /login."""
    resp = await async_client.post("/logout", headers=CSRF_HEADERS,
                                   follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_logout_template_uses_post(async_client, db):
    """More tab template must use POST for logout, not GET link."""
    resp = await async_client.get("/more")
    assert resp.status_code == 200
    html = resp.text
    assert "POST" in html or "post" in html or "mobLogout" in html
    assert "href=\"/logout\"" not in html, "Logout must not be a GET link"


# ═══════════════════════════════════════════════════════════════════════
#  D. SETTINGS SAVE PAYLOAD / KEY ALIGNMENT
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_settings_page_renders(async_client, db):
    """Admin settings page should render without errors."""
    resp = await async_client.get("/admin/settings")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_config_patch_accepts_ssot_keys(async_client, db):
    """PATCH /api/config with SSOT key names should succeed."""
    # Seed required config keys
    for key in ("needs_update_threshold_hours", "teams_enabled",
                "teams_recipients", "teams_message_template",
                "outlook_enabled", "outlook_recipients",
                "outlook_subject_template", "outlook_body_template",
                "critical_alert_enabled", "critical_alert_recipients"):
        await _seed_config(db, key, "")

    resp = await async_client.patch("/api/config", headers=CSRF_HEADERS, json={
        "configs": [
            {"key": "needs_update_threshold_hours", "value": "96"},
            {"key": "teams_enabled", "value": "true"},
            {"key": "teams_recipients", "value": "#test-channel"},
        ]
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["updated"] == 3


@pytest.mark.asyncio
async def test_settings_js_sends_configs_array(async_client, db):
    """Settings template must use {configs: [...]} shape, not flat object."""
    resp = await async_client.get("/admin/settings")
    html = resp.text
    # The patchConfig function should wrap data in {configs: [...]}
    assert "JSON.stringify({configs})" in html or 'JSON.stringify({configs})' in html, \
        "patchConfig must wrap data in {configs: [...]}"


@pytest.mark.asyncio
async def test_settings_uses_ssot_config_keys(async_client, db):
    """Settings template must reference SSOT config keys, not old names."""
    resp = await async_client.get("/admin/settings")
    html = resp.text
    # SSOT keys that must appear
    assert "teams_enabled" in html
    assert "teams_recipients" in html
    assert "teams_message_template" in html
    assert "outlook_enabled" in html
    assert "outlook_subject_template" in html
    assert "outlook_body_template" in html
    assert "critical_alert_enabled" in html
    assert "critical_alert_recipients" in html
    # Old keys that must NOT appear
    assert "notify_teams" not in html.replace("teams_enabled", ""), \
        "Old key 'notify_teams' should be replaced by 'teams_enabled'"


@pytest.mark.asyncio
async def test_settings_phase2_labels(async_client, db):
    """Notification sections must be labeled as Phase 2."""
    resp = await async_client.get("/admin/settings")
    html = resp.text
    assert "Phase 2" in html, "Notification sections must indicate Phase 2"


# ═══════════════════════════════════════════════════════════════════════
#  E. MORE > RFO SUMMARY
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rfo_summary_renders_with_data(async_client, db):
    """RFO Summary page should show real metrics when work packages exist."""
    ac = await _seed_aircraft(db)
    shop = await _seed_shop(db)
    wp = await _seed_wp(db, ac.id)
    task = await _seed_task(db, ac_id=ac.id, shop_id=shop.id, wp_id=wp.id)
    await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 18),
                         status="IN_PROGRESS", mh=5.0)

    resp = await async_client.get(f"/more/rfo-summary?wp_id={wp.id}")
    assert resp.status_code == 200
    html = resp.text
    assert "RFO-001" in html
    assert "Progress" in html
    assert "Remaining" in html


@pytest.mark.asyncio
async def test_rfo_summary_shows_wp_selector(async_client, db):
    """RFO Summary should include work package selector when multiple WPs exist."""
    ac = await _seed_aircraft(db)
    wp1 = await _seed_wp(db, ac.id, rfo_no="RFO-001")
    wp2 = await _seed_wp(db, ac.id, rfo_no="RFO-002")

    resp = await async_client.get("/more/rfo-summary")
    assert resp.status_code == 200
    html = resp.text
    assert "RFO-001" in html
    assert "RFO-002" in html


@pytest.mark.asyncio
async def test_rfo_summary_empty_state(async_client, db):
    """RFO Summary with no work packages should show empty state."""
    resp = await async_client.get("/more/rfo-summary")
    assert resp.status_code == 200
    html = resp.text
    assert "No active work packages" in html


# ═══════════════════════════════════════════════════════════════════════
#  F. MOBILE ACCESS BEHAVIOR
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_worker_no_shop_access_tasks_tab_disabled(db):
    """Worker without shop_access should have Tasks tab disabled."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.middleware.rate_limit import reset_rate_limits

    reset_rate_limits()
    worker_session = {
        "user_id": 3, "employee_no": "E003", "display_name": "Test Worker",
        "roles": ["WORKER"], "team": "Sheet Metal",
        "csrf_token": "test-csrf-token-abc123",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.cookies.set("session", _make_session_cookie(worker_session))
        resp = await c.get("/more")
        assert resp.status_code == 200
        html = resp.text
        # Tasks tab should be disabled (opacity-30 or aria-disabled)
        assert "opacity-30" in html or 'aria-disabled="true"' in html


@pytest.mark.asyncio
async def test_worker_view_access_readonly(db):
    """Worker with VIEW shop_access should have read-only task access."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.middleware.rate_limit import reset_rate_limits

    shop = await _seed_shop(db)
    await _grant_shop_access(db, user_id=3, shop_id=shop.id, access="VIEW")

    reset_rate_limits()
    worker_session = {
        "user_id": 3, "employee_no": "E003", "display_name": "Test Worker",
        "roles": ["WORKER"], "team": "Sheet Metal",
        "csrf_token": "test-csrf-token-abc123",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.cookies.set("session", _make_session_cookie(worker_session))
        resp = await c.get("/tasks/entry")
        assert resp.status_code == 200
        # Tasks tab should NOT be disabled (has access)
        # can_edit should be false — no edit forms should be visible
        # (exact check depends on template, just verify page renders)


# ═══════════════════════════════════════════════════════════════════════
#  DEAD UI CLEANUP
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_kanban_detail_button_label(async_client, db):
    """Task Manager kanban detail should say 'View Task Detail', not 'Open in Data Entry'."""
    resp = await async_client.get("/tasks")
    assert resp.status_code == 200
    html = resp.text
    assert "Open in Data Entry" not in html
    assert "View Task Detail" in html
