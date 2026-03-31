"""Regression tests for UI/backend/SSOT alignment fixes.

Covers:
  A. Latest snapshot semantics in default views
  B. Dashboard RFO links / route correctness
  C. More tab logout flow
  D. Settings save payload / key alignment
  E. More > RFO Summary rendering
  F. Mobile access behavior
"""
import re
from datetime import date, datetime, time, timezone

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


async def _seed_wp(db_factory, aircraft_id, rfo_no="RFO-001", *, status="ACTIVE", title="Test WP"):
    from app.models.reference import WorkPackage
    async with db_factory() as s:
        wp = WorkPackage(
            aircraft_id=aircraft_id, rfo_no=rfo_no, title=title,
            start_date=date(2026, 3, 1), end_date=date(2026, 3, 31),
            status=status,
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


async def _seed_ot(db_factory, *, user_id, status="APPROVED", minutes=120, dt=None):
    from app.models.ot import OtRequest

    async with db_factory() as s:
        req = OtRequest(
            user_id=user_id,
            date=dt or date.today(),
            start_time=time(18, 0),
            end_time=time(20, 0),
            requested_minutes=minutes,
            reason_code="BACKLOG",
            status=status,
            created_at=NOW,
            updated_at=NOW,
        )
        s.add(req)
        await s.commit()
        await s.refresh(req)
        return req


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


async def _seed_supervisor(
    db_factory,
    *,
    name="Scoped Supervisor",
    employee_no="E900",
    team="Sheet Metal",
    shop_id=None,
    access="EDIT",
):
    from sqlalchemy import select
    from app.models.user import Role, User
    from app.models.user_shop_access import UserShopAccess

    async with db_factory() as s:
        sup_role = (await s.execute(select(Role).where(Role.name == "SUPERVISOR"))).scalar_one()
        user = User(
            employee_no=employee_no,
            name=name,
            email=f"{employee_no.lower()}@test.com",
            team=team,
            created_at=NOW,
            updated_at=NOW,
        )
        user.roles = [sup_role]
        s.add(user)
        await s.flush()
        if shop_id is not None:
            s.add(UserShopAccess(user_id=user.id, shop_id=shop_id, access=access, granted_by=1))
        await s.commit()
        await s.refresh(user)
        return user


def _extract_int_metric(html: str, pattern: str) -> int:
    match = re.search(pattern, html, re.DOTALL)
    assert match, f"Could not find metric with pattern: {pattern}"
    return int(match.group(1))


def _extract_float_metric(html: str, pattern: str) -> float:
    match = re.search(pattern, html, re.DOTALL)
    assert match, f"Could not find metric with pattern: {pattern}"
    return float(match.group(1))


def _extract_select_section(html: str, select_id: str) -> str:
    marker = f'<select id="{select_id}"'
    assert marker in html, f"Could not find select: {select_id}"
    return html.split(marker, 1)[1].split("</select>", 1)[0]


def _extract_opening_tag_by_id(html: str, element_id: str) -> str:
    marker = f'id="{element_id}"'
    idx = html.index(marker)
    start = html.rfind("<", 0, idx)
    end = html.index(">", idx)
    return html[start:end + 1]


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
async def test_dashboard_non_admin_ot_widgets_use_same_team_scope(sup_client, db):
    """Non-admin OT cards should scope by the user's shop aliases, not drop to zero on shop-code mismatch."""
    await _seed_shop(db, code="SMA", name="Sheet Metal")
    await _seed_shop(db, code="AFM", name="Airframe")
    await _seed_ot(db, user_id=2, status="PENDING", minutes=120)
    await _seed_ot(db, user_id=2, status="ENDORSED", minutes=180)
    await _seed_ot(db, user_id=4, status="PENDING", minutes=240)

    resp = await sup_client.get("/dashboard")
    assert resp.status_code == 200
    html = resp.text

    assert _extract_int_metric(
        html,
        r'OT Pending.*?<span class="text-2xl font-bold text-gold-500">(\d+)</span>',
    ) == 1
    assert _extract_int_metric(
        html,
        r'OT Endorsed.*?<span class="text-2xl font-bold text-st-purple">(\d+)</span>',
    ) == 1
    assert _extract_int_metric(
        html,
        r'text-lg font-bold text-st-yellow">(\d+)</div>PENDING',
    ) == 1
    assert _extract_int_metric(
        html,
        r'text-lg font-bold text-st-purple">(\d+)</div>ENDORSED',
    ) == 1
    assert _extract_float_metric(
        html,
        r'This month total</span><span class="font-semibold">([\d.]+)h</span>',
    ) == 5.0


@pytest.mark.asyncio
async def test_dashboard_admin_shop_filter_scopes_ot_widgets_by_shop_aliases(async_client, db):
    """Admin shop filter should include users stored with either the shop name or code."""
    sheet_metal = await _seed_shop(db, code="SMA", name="Sheet Metal")
    await _seed_shop(db, code="AFM", name="Airframe")
    await _seed_ot(db, user_id=2, status="PENDING", minutes=120)
    await _seed_ot(db, user_id=4, status="ENDORSED", minutes=240)

    resp = await async_client.get(f"/dashboard?shop_id={sheet_metal.id}")
    assert resp.status_code == 200
    html = resp.text

    assert _extract_int_metric(
        html,
        r'OT Pending.*?<span class="text-2xl font-bold text-gold-500">(\d+)</span>',
    ) == 1
    assert _extract_int_metric(
        html,
        r'OT Endorsed.*?<span class="text-2xl font-bold text-st-purple">(\d+)</span>',
    ) == 0
    assert _extract_int_metric(
        html,
        r'text-lg font-bold text-st-yellow">(\d+)</div>PENDING',
    ) == 1
    assert _extract_int_metric(
        html,
        r'text-lg font-bold text-st-purple">(\d+)</div>ENDORSED',
    ) == 0
    assert "Test Supervisor" in html
    assert "Other Team Worker" not in html


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
async def test_task_manager_honors_view_query(async_client, db):
    """Task Manager should render the requested view and preserve it in the form/JS state."""
    resp = await async_client.get("/tasks?view=kanban")
    assert resp.status_code == 200
    html = resp.text
    assert 'name="view" value="kanban"' in html
    assert 'let currentTaskView = "kanban";' in html
    assert 'id="task-view-table" class="hidden flex flex-col' in html
    assert 'id="task-view-kanban" class="flex-1 flex gap-3' in html


@pytest.mark.asyncio
async def test_task_manager_refresh_keeps_view_and_kanban_uses_stable_card_ref(async_client, db):
    """Filter/pagination refreshes should carry the current view, and kanban drop should keep a local card ref."""
    resp = await async_client.get("/tasks?view=rfo")
    assert resp.status_code == 200
    html = resp.text
    assert "data.set('view', currentTaskView);" in html
    assert "credentials: 'same-origin'" in html
    assert "const movedCard = draggedCard;" in html


@pytest.mark.asyncio
async def test_task_manager_defaults_to_latest_visible_meeting_date_for_supervisor(sup_client, db):
    """Task Manager should default to the latest meeting_date visible to the supervisor, not a hidden shop's newer week."""
    visible_shop = await _seed_shop(db, code="SMA", name="Sheet Metal")
    hidden_shop = await _seed_shop(db, code="AFM", name="Airframe")
    visible_ac = await _seed_aircraft(db, ac_reg="9V-VIS")
    hidden_ac = await _seed_aircraft(db, ac_reg="9V-HID")
    visible_wp = await _seed_wp(db, visible_ac.id, rfo_no="RFO-VIS")
    hidden_wp = await _seed_wp(db, hidden_ac.id, rfo_no="RFO-HID")
    visible_task = await _seed_task(db, ac_id=visible_ac.id, shop_id=visible_shop.id, wp_id=visible_wp.id, text="Visible scope task")
    hidden_task = await _seed_task(db, ac_id=hidden_ac.id, shop_id=hidden_shop.id, wp_id=hidden_wp.id, text="Hidden scope task")
    await _seed_snapshot(db, task_id=visible_task.id, meeting_date=date(2026, 3, 10), status="IN_PROGRESS", mh=5.0)
    await _seed_snapshot(db, task_id=hidden_task.id, meeting_date=date(2026, 3, 17), status="IN_PROGRESS", mh=7.0)
    await _grant_shop_access(db, user_id=2, shop_id=visible_shop.id, access="EDIT")

    resp = await sup_client.get("/tasks")
    assert resp.status_code == 200
    html = resp.text
    assert 'name="meeting_date" value="2026-03-10"' in html
    assert "Visible scope task" in html
    assert "Hidden scope task" not in html


@pytest.mark.asyncio
async def test_task_manager_non_admin_scopes_metadata_and_lookup_datasets(sup_client, db):
    """Supervisor Task Manager should hide inaccessible supervisor/RFO metadata and omit admin-only lookup datasets."""
    visible_shop = await _seed_shop(db, code="SMA", name="Sheet Metal")
    hidden_shop = await _seed_shop(db, code="AFM", name="Airframe")
    visible_ac = await _seed_aircraft(db, ac_reg="9V-SCP")
    hidden_ac = await _seed_aircraft(db, ac_reg="9V-HDN")
    visible_wp = await _seed_wp(db, visible_ac.id, rfo_no="RFO-SCP")
    hidden_wp = await _seed_wp(db, hidden_ac.id, rfo_no="RFO-HDN")
    visible_task = await _seed_task(db, ac_id=visible_ac.id, shop_id=visible_shop.id, wp_id=visible_wp.id, text="Scoped visible task")
    hidden_task = await _seed_task(db, ac_id=hidden_ac.id, shop_id=hidden_shop.id, wp_id=hidden_wp.id, text="Scoped hidden task")
    await _seed_snapshot(db, task_id=visible_task.id, meeting_date=date(2026, 3, 18), status="IN_PROGRESS", mh=5.0)
    await _seed_snapshot(db, task_id=hidden_task.id, meeting_date=date(2026, 3, 18), status="IN_PROGRESS", mh=3.0)
    await _grant_shop_access(db, user_id=2, shop_id=visible_shop.id, access="EDIT")
    await _seed_supervisor(
        db,
        name="Hidden Supervisor",
        employee_no="E901",
        team="Airframe",
        shop_id=hidden_shop.id,
    )

    resp = await sup_client.get("/tasks?meeting_date=2026-03-18")
    assert resp.status_code == 200
    html = resp.text
    supervisor_filter = _extract_select_section(html, "f-supervisor")
    rfo_filter = _extract_select_section(html, "f-rfo")

    assert "Test Supervisor" in supervisor_filter
    assert "Hidden Supervisor" not in html
    assert 'value="RFO-SCP"' in rfo_filter
    assert 'value="RFO-HDN"' not in rfo_filter
    assert "Scoped visible task" in html
    assert "Scoped hidden task" not in html
    assert "const AIRCRAFT_OPTIONS = [];" in html
    assert "const WORK_PACKAGE_OPTIONS = [];" in html


@pytest.mark.asyncio
async def test_task_manager_selected_shop_narrows_filter_metadata(sup_client, db):
    """Selecting a shop should narrow the supervisor and RFO filter options to that shop."""
    shop_one = await _seed_shop(db, code="SMA", name="Sheet Metal")
    shop_two = await _seed_shop(db, code="AFM", name="Airframe")
    ac_one = await _seed_aircraft(db, ac_reg="9V-SH1")
    ac_two = await _seed_aircraft(db, ac_reg="9V-SH2")
    wp_one = await _seed_wp(db, ac_one.id, rfo_no="RFO-SH1")
    wp_two = await _seed_wp(db, ac_two.id, rfo_no="RFO-SH2")
    task_one = await _seed_task(db, ac_id=ac_one.id, shop_id=shop_one.id, wp_id=wp_one.id, text="Shop one task")
    task_two = await _seed_task(db, ac_id=ac_two.id, shop_id=shop_two.id, wp_id=wp_two.id, text="Shop two task")
    await _seed_snapshot(db, task_id=task_one.id, meeting_date=date(2026, 3, 18), status="IN_PROGRESS", mh=5.0)
    await _seed_snapshot(db, task_id=task_two.id, meeting_date=date(2026, 3, 18), status="WAITING", mh=2.0)
    await _grant_shop_access(db, user_id=2, shop_id=shop_one.id, access="EDIT")
    await _grant_shop_access(db, user_id=2, shop_id=shop_two.id, access="EDIT")
    await _seed_supervisor(
        db,
        name="Shop Two Supervisor",
        employee_no="E902",
        team="Airframe",
        shop_id=shop_two.id,
    )

    resp = await sup_client.get(f"/tasks?meeting_date=2026-03-18&shop_id={shop_one.id}")
    assert resp.status_code == 200
    html = resp.text
    supervisor_filter = _extract_select_section(html, "f-supervisor")
    rfo_filter = _extract_select_section(html, "f-rfo")

    assert "Test Supervisor" in supervisor_filter
    assert "Shop Two Supervisor" not in supervisor_filter
    assert 'value="RFO-SH1"' in rfo_filter
    assert 'value="RFO-SH2"' not in rfo_filter


@pytest.mark.asyncio
async def test_task_manager_search_matches_ac_reg_and_rfo(async_client, db):
    """Task Manager search should match aircraft registration and RFO number as well as task text."""
    ac = await _seed_aircraft(db, ac_reg="9V-FIND")
    shop = await _seed_shop(db)
    wp = await _seed_wp(db, ac.id, rfo_no="RFO-FIND")
    task = await _seed_task(db, ac_id=ac.id, shop_id=shop.id, wp_id=wp.id, text="Findable task")
    await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 18), status="IN_PROGRESS", mh=5.0)

    resp_ac = await async_client.get("/tasks?meeting_date=2026-03-18&search=9V-FIND")
    assert resp_ac.status_code == 200
    assert "Findable task" in resp_ac.text

    resp_rfo = await async_client.get("/tasks?meeting_date=2026-03-18&search=RFO-FIND")
    assert resp_rfo.status_code == 200
    assert "Findable task" in resp_rfo.text
    assert "Search task, AC reg, RFO..." in resp_rfo.text


@pytest.mark.asyncio
async def test_task_manager_bulk_status_modal_uses_selected_status(async_client, db):
    """Task Manager bulk status action should use a selected target status instead of a hard-coded value."""
    resp = await async_client.get("/tasks")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="modal-bulk-status"' in html
    assert 'id="bulkStatusSelect"' in html
    assert "openBulkStatusModal()" in html
    assert "const nextStatus = document.getElementById('bulkStatusSelect').value;" in html
    assert "status: 'IN_PROGRESS'" not in html


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


@pytest.mark.asyncio
async def test_task_entry_edit_panel_renders_below_selected_card(async_client, db):
    """Desktop Data Entry should render quick update directly below the selected card."""
    ac = await _seed_aircraft(db)
    shop = await _seed_shop(db)
    task1 = await _seed_task(db, ac_id=ac.id, shop_id=shop.id, text="First task")
    task2 = await _seed_task(db, ac_id=ac.id, shop_id=shop.id, text="Second task")
    await _seed_snapshot(db, task_id=task1.id, meeting_date=date(2026, 3, 18),
                         status="IN_PROGRESS", mh=5.0)
    await _seed_snapshot(db, task_id=task2.id, meeting_date=date(2026, 3, 18),
                         status="WAITING", mh=2.0, has_issue=True)

    resp = await async_client.get(
        f"/tasks/entry?ac={ac.ac_reg}&meeting_date=2026-03-18&edit={task1.id}"
    )
    assert resp.status_code == 200
    html = resp.text
    card1_idx = html.index(f'id="task-card-{task1.id}"')
    panel_idx = html.index('id="editPanel"')
    card2_idx = html.index(f'id="task-card-{task2.id}"')
    assert card1_idx < panel_idx < card2_idx
    assert "function toggleDesktopTaskEditor" in html
    assert "Quick Update" in html


@pytest.mark.asyncio
async def test_task_entry_save_actions_update_locally_and_close_panel(async_client, db):
    """Desktop Data Entry save actions should avoid full refresh and close locally when requested."""
    ac = await _seed_aircraft(db)
    shop = await _seed_shop(db)
    task = await _seed_task(db, ac_id=ac.id, shop_id=shop.id, text="Save behavior task")
    await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 18),
                         status="IN_PROGRESS", mh=5.0)

    resp = await async_client.get(
        f"/tasks/entry?ac={ac.ac_reg}&meeting_date=2026-03-18&edit={task.id}"
    )
    assert resp.status_code == 200
    html = resp.text
    assert "Save &amp; Close" in html
    assert "function updateDesktopTaskCard(taskId, snapshotResult, workerName)" in html
    assert "credentials: 'same-origin'" in html
    assert "Partial Success" in html
    assert "closeDesktopEditor();" in html
    assert "refreshTaskEntry({}, { resetEdit: false });" in html
    assert "refreshTaskEntry({ edit: taskId });" not in html
    assert "refreshTaskEntry({ edit: nextTaskId });" not in html


@pytest.mark.asyncio
async def test_task_entry_create_form_keeps_selected_aircraft_context_without_visible_tasks(async_client, db):
    """Desktop Data Entry create form should resolve the selected aircraft from query params, not the first task row."""
    ac = await _seed_aircraft(db, ac_reg="9V-CTX")
    await _seed_shop(db, code="CTX", name="Context Shop")
    await _seed_wp(db, ac.id, rfo_no="RFO-CTX")

    resp = await async_client.get(f"/tasks/entry?ac={ac.ac_reg}&meeting_date=2026-03-18")
    assert resp.status_code == 200
    html = resp.text
    assert f"No tasks found for {ac.ac_reg}." in html
    assert 'name="shop_id"' in html
    assert 'name="work_package_id"' in html
    assert '<input type="hidden" name="shop_id"' not in html
    assert '<input type="hidden" name="work_package_id"' not in html
    assert "Select shop first" in html
    assert "showModal('modal-entry-task')" in html


@pytest.mark.asyncio
async def test_task_entry_worker_options_scope_to_editable_shop(sup_client, db):
    """Desktop Data Entry worker dropdowns should only show assignees with access to the editable shop."""
    ac = await _seed_aircraft(db, ac_reg="9V-WKR")
    shop = await _seed_shop(db, code="WKS", name="Worker Scope Shop")
    wp = await _seed_wp(db, ac.id, rfo_no="RFO-WKR")
    task = await _seed_task(db, ac_id=ac.id, shop_id=shop.id, wp_id=wp.id, text="Worker scope task")
    await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 18), status="IN_PROGRESS")
    await _grant_shop_access(db, user_id=2, shop_id=shop.id, access="EDIT")
    await _grant_shop_access(db, user_id=3, shop_id=shop.id, access="VIEW")

    resp = await sup_client.get(
        f"/tasks/entry?ac={ac.ac_reg}&meeting_date=2026-03-18&edit={task.id}"
    )
    assert resp.status_code == 200
    html = resp.text
    assert 'id="desktopQuickUpdateForm"' in html
    assert "Test Worker" in html
    assert "Other Team Worker" not in html


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


@pytest.mark.asyncio
async def test_settings_snapshot_ui_is_manual_only(async_client, db):
    """Settings snapshot section should no longer expose dead auto/day/time controls."""
    await _seed_config(db, "meeting_current_date", "2026-03-30")
    resp = await async_client.get("/admin/settings")
    html = resp.text
    assert 'id="adv-day"' not in html
    assert 'id="adv-time"' not in html
    assert 'id="auto-advance"' not in html
    assert "Automatic week advancement is not active yet." in html
    assert 'id="snap-week-display"></div>' in html


@pytest.mark.asyncio
async def test_settings_export_uses_saved_working_week(async_client, db):
    """Task export should use the saved meeting_current_date, not the live preview week."""
    await _seed_config(db, "meeting_current_date", "2026-03-24")
    resp = await async_client.get("/admin/settings")
    html = resp.text
    assert "Tasks CSV uses the saved working week." in html
    assert 'const savedMeetingDate = \'2026-03-24\'' in html
    assert "return savedMeetingDate;" in html
    assert "To export this previewed week, save configuration first." in html


@pytest.mark.asyncio
async def test_invalid_threshold_rejected_without_breaking_task_entry(async_client, db):
    """Invalid threshold should be rejected so task entry continues to render safely."""
    await _seed_config(db, "needs_update_threshold_hours", "72")

    patch_resp = await async_client.patch(
        "/api/config",
        headers=CSRF_HEADERS,
        json={"configs": [{"key": "needs_update_threshold_hours", "value": "abc"}]},
    )
    assert patch_resp.status_code == 422

    entry_resp = await async_client.get("/tasks/entry")
    assert entry_resp.status_code == 200


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


@pytest.mark.asyncio
async def test_rfo_summary_worker_forbidden_and_missing_wp_404(async_client, worker_client, db):
    """RFO Summary should return explicit 403/404 HTML states."""
    resp = await worker_client.get("/more/rfo-summary")
    assert resp.status_code == 403
    assert "Access denied" in resp.text

    missing = await async_client.get("/more/rfo-summary?wp_id=99999")
    assert missing.status_code == 404
    assert "RFO not found" in missing.text


@pytest.mark.asyncio
async def test_rfo_summary_historical_selection_is_preserved_and_task_cta_disabled_without_access(sup_client, db):
    """Historical direct links should stay selected, and the task CTA should not dead-link without shop access."""
    ac = await _seed_aircraft(db, ac_reg="9V-HSUM")
    active_wp = await _seed_wp(db, ac.id, rfo_no="RFO-ACTIVE", status="ACTIVE", title="Active WP")
    completed_wp = await _seed_wp(db, ac.id, rfo_no="RFO-DONE", status="COMPLETED", title="Completed WP")
    await _seed_wp(db, ac.id, rfo_no="RFO-HOLD", status="ON_HOLD", title="On Hold WP")

    resp = await sup_client.get(f"/more/rfo-summary?wp_id={completed_wp.id}")
    assert resp.status_code == 200
    html = resp.text
    assert "RFO-DONE" in html
    assert "RFO-ACTIVE" in html
    assert "Historical selection" in html
    assert "RFO-HOLD" not in html
    assert html.index("RFO-DONE") < html.index("RFO-ACTIVE")
    assert "Task surface access required" in html
    assert 'href="/tasks/entry"' not in html


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
async def _legacy_test_worker_view_access_readonly(worker_client, db):
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


@pytest.mark.asyncio
async def test_worker_view_access_readonly(worker_client, db):
    """Worker with VIEW shop_access should see read-only cards with detail links only."""
    ac = await _seed_aircraft(db, ac_reg="9V-RO")
    shop = await _seed_shop(db)
    task = await _seed_task(db, ac_id=ac.id, shop_id=shop.id, text="Read only task")
    await _seed_snapshot(db, task_id=task.id, meeting_date=date(2026, 3, 18), status="IN_PROGRESS")
    await _grant_shop_access(db, user_id=3, shop_id=shop.id, access="VIEW")

    resp = await worker_client.get(
        f"/tasks/entry?ac={ac.ac_reg}&meeting_date=2026-03-18&edit={task.id}"
    )
    assert resp.status_code == 200
    html = resp.text
    task_card = _extract_opening_tag_by_id(html, f"task-card-{task.id}")
    assert "Read only task" in html
    assert "View Task Detail" in html
    assert f'href="/tasks/{task.id}"' in html
    assert 'id="desktopQuickUpdateForm"' not in html
    assert 'id="modal-entry-task"' not in html
    assert "showModal('modal-entry-task')" not in html
    assert "onclick=" not in task_card


@pytest.mark.asyncio
async def test_task_entry_mixed_scope_only_editable_card_opens_editor(sup_client, db):
    """Desktop Data Entry should expose inline edit only for tasks in editable shops."""
    from sqlalchemy import select

    from app.models.task import TaskItem

    ac = await _seed_aircraft(db, ac_reg="9V-MIX")
    shop_edit = await _seed_shop(db, code="M1", name="Editable Shop")
    shop_readonly = await _seed_shop(db, code="M2", name="Assigned Shop")
    wp = await _seed_wp(db, ac.id, rfo_no="RFO-MIX")
    task_edit = await _seed_task(db, ac_id=ac.id, shop_id=shop_edit.id, wp_id=wp.id, text="Editable task")
    task_readonly = await _seed_task(db, ac_id=ac.id, shop_id=shop_readonly.id, wp_id=wp.id, text="Assigned read only task")
    await _seed_snapshot(db, task_id=task_edit.id, meeting_date=date(2026, 3, 18), status="IN_PROGRESS")
    await _seed_snapshot(db, task_id=task_readonly.id, meeting_date=date(2026, 3, 18), status="WAITING")
    await _grant_shop_access(db, user_id=2, shop_id=shop_edit.id, access="EDIT")

    async with db() as s:
        readonly_row = (await s.execute(select(TaskItem).where(TaskItem.id == task_readonly.id))).scalar_one()
        readonly_row.assigned_supervisor_id = 2
        await s.commit()

    resp = await sup_client.get(f"/tasks/entry?ac={ac.ac_reg}&meeting_date=2026-03-18")
    assert resp.status_code == 200
    html = resp.text
    editable_card = _extract_opening_tag_by_id(html, f"task-card-{task_edit.id}")
    readonly_card = _extract_opening_tag_by_id(html, f"task-card-{task_readonly.id}")
    assert "Editable task" in html
    assert "Assigned read only task" in html
    assert "onclick=" in editable_card
    assert "toggleDesktopTaskEditor" in editable_card
    assert "onclick=" not in readonly_card
    assert f'href="/tasks/{task_readonly.id}"' in html
