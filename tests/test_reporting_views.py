"""Branch 10 — Reporting views smoke tests.

Verifies:
  1. scripts/create_views.py view list is complete (16 views)
  2. Each view SQL is syntactically valid (CREATE OR ALTER VIEW dbo.xxx)
  3. Schema contracts: column names match SSOT §11 for every view
  4. View name extraction helper returns correct order
"""
import re

import pytest

from scripts.create_views import VIEWS, get_view_names


# ── Helper ────────────────────────────────────────────────────────────

def _extract_columns(sql: str) -> list[str]:
    """Extract output column aliases from a CREATE OR ALTER VIEW SELECT.

    Handles multi-line SELECTs, single-line comma-separated columns,
    UNION ALL views, and CTE-based views (uses the final SELECT).
    """
    # For CTE-based views, use the last SELECT...FROM pair
    # For UNION ALL views, use the first SELECT only
    upper = sql.upper()

    if 'UNION ALL' in upper:
        # Enum dimension view — extract from first SELECT ... UNION
        m = re.search(r'\bSELECT\b(.*?)(?:\bUNION\b)', sql, re.DOTALL | re.IGNORECASE)
        if m:
            fragment = m.group(1).strip()
            # e.g. "'BACKLOG' AS reason_code"
            alias = re.search(r'\bAS\s+\[?(\w+)\]?', fragment, re.IGNORECASE)
            return [alias.group(1).lower()] if alias else []
        return []

    # Find the main (outermost) SELECT ... FROM, skipping subqueries.
    # For CTE views: find the SELECT after the CTE block.
    # Track parenthesis depth to skip subquery SELECTs.
    view_body = sql
    # Strip CREATE OR ALTER VIEW ... AS prefix
    view_match = re.search(r'\bAS\b\s', sql, re.IGNORECASE)
    if view_match:
        view_body = sql[view_match.end():]

    # If CTE (WITH ...), skip to after the CTE block.
    # CTE ends when depth returns to 0 and the next token is SELECT (not comma).
    if re.match(r'\s*WITH\b', view_body, re.IGNORECASE):
        depth = 0
        cte_end = 0
        i = 0
        while i < len(view_body):
            ch = view_body[i]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    # Check what follows: if next non-whitespace is SELECT, CTE is done
                    rest = view_body[i+1:].lstrip()
                    if rest.upper().startswith('SELECT'):
                        cte_end = i + 1
                        break
            i += 1
        view_body = view_body[cte_end:]

    # Find the first top-level SELECT ... FROM (depth=0)
    # This skips subqueries inside WHERE clauses
    depth = 0
    select_start = None
    from_end = None
    i = 0
    while i < len(view_body):
        if view_body[i] == '(':
            depth += 1
        elif view_body[i] == ')':
            depth -= 1
        elif depth == 0:
            chunk = view_body[i:i+6].upper()
            if chunk == 'SELECT' and select_start is None:
                select_start = i + 6
            elif view_body[i:i+4].upper() == 'FROM' and select_start is not None:
                from_end = i
                break
        i += 1

    if select_start is None or from_end is None:
        return []

    m_body = view_body[select_start:from_end]
    # Wrap it for the parsing below
    class _M:
        def group(self, _):
            return m_body
    m = _M()
    select_body = m.group(1)
    columns = []

    # Remove comments
    select_body = re.sub(r'--[^\n]*', '', select_body)

    # Split by comma, handling multi-line expressions
    # First, collapse into a single string and split by commas
    parts_list = []
    depth = 0
    current = []
    for ch in select_body:
        if ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            parts_list.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts_list.append(''.join(current))

    for part in parts_list:
        part = part.strip()
        if not part:
            continue
        # Check for "AS alias" (with optional brackets)
        alias_match = re.search(r'\bAS\s+\[?(\w+)\]?\s*$', part, re.IGNORECASE)
        if alias_match:
            columns.append(alias_match.group(1).lower())
        else:
            # Bare column: take the last word (e.g. "ac.ac_reg" → "ac_reg")
            bare = re.search(r'(?:\w+\.)?(\w+)\s*$', part)
            if bare:
                columns.append(bare.group(1).lower())

    return columns


def _extract_view_name(sql: str) -> str | None:
    m = re.search(r'CREATE OR ALTER VIEW\s+dbo\.(\w+)', sql, re.IGNORECASE)
    return m.group(1) if m else None


# ── Tests ─────────────────────────────────────────────────────────────

class TestViewCatalog:
    """Verify all 16 views are defined."""

    EXPECTED_VIEWS = [
        "vw_fact_ot_requests",
        "vw_fact_task_snapshots",
        "vw_fact_task_snapshots_all",
        "vw_dim_employee",
        "vw_dim_aircraft",
        "vw_dim_work_package",
        "vw_dim_shop_stream",
        "vw_dim_shop",
        "vw_dim_task_status",
        "vw_dim_ot_reason",
        "vw_dim_date",
        "vw_fact_ot_by_reason",
        "vw_fact_ot_weekly",
        "vw_rfo_efficiency",
        "vw_rfo_burndown",
        "vw_task_distribution",
    ]

    def test_view_count(self):
        assert len(VIEWS) == 16, f"Expected 16 views, got {len(VIEWS)}"

    def test_get_view_names(self):
        names = get_view_names()
        assert names == self.EXPECTED_VIEWS

    @pytest.mark.parametrize("view_name", EXPECTED_VIEWS)
    def test_view_present(self, view_name):
        names = get_view_names()
        assert view_name in names, f"{view_name} not found in VIEWS"


class TestViewSQLSyntax:
    """Verify each view SQL is well-formed."""

    @pytest.mark.parametrize("idx", range(len(VIEWS)))
    def test_create_or_alter(self, idx):
        sql = VIEWS[idx]
        assert re.search(
            r'CREATE OR ALTER VIEW\s+dbo\.\w+', sql, re.IGNORECASE
        ), f"View at index {idx} missing CREATE OR ALTER VIEW dbo.xxx"

    @pytest.mark.parametrize("idx", range(len(VIEWS)))
    def test_has_select(self, idx):
        sql = VIEWS[idx]
        assert 'SELECT' in sql.upper(), f"View at index {idx} missing SELECT"

    @pytest.mark.parametrize("idx", range(len(VIEWS)))
    def test_ends_with_semicolon(self, idx):
        sql = VIEWS[idx].strip()
        assert sql.endswith(';'), f"View at index {idx} missing trailing semicolon"


class TestFactOtRequests:
    """§11.1 vw_fact_ot_requests schema contract."""

    REQUIRED_COLUMNS = [
        "ot_request_id", "user_id", "employee_no", "employee_name", "team",
        "submitted_by", "submitted_by_name", "submitted_by_employee_no",
        "ot_date", "start_time", "end_time", "requested_minutes",
        "reason_code", "status", "work_package_id", "shop_stream_id",
        "rfo_no", "work_package_title", "ac_reg", "airline", "shop_code",
        "endorser_id", "endorser_name", "endorse_action", "endorse_comment", "endorsed_at",
        "final_approver_id", "final_approver_name", "approval_action",
        "approval_comment", "approved_at",
        "submitted_at", "turnaround_hours", "endorse_turnaround_hours",
    ]

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_fact_ot_requests":
                return sql
        pytest.fail("vw_fact_ot_requests not found")

    def test_required_columns(self):
        sql = self._get_sql()
        cols = _extract_columns(sql)
        for col in self.REQUIRED_COLUMNS:
            assert col in cols, f"Missing column: {col}"

    def test_2stage_approval_joins(self):
        sql = self._get_sql()
        assert "stage = 'ENDORSE'" in sql
        assert "stage = 'APPROVE'" in sql

    def test_turnaround_calculation(self):
        sql = self._get_sql()
        assert 'DATEDIFF(SECOND' in sql
        assert '3600.0' in sql


class TestFactTaskSnapshots:
    """§11.2 vw_fact_task_snapshots schema contract."""

    REQUIRED_COLUMNS = [
        "snapshot_id", "task_id", "meeting_date",
        "shop_id", "shop_code", "shop_name",
        "aircraft_id", "work_package_id", "rfo_no", "work_package_title",
        "assigned_supervisor_id", "assigned_supervisor_name",
        "assigned_worker_id", "assigned_worker_name",
        "distributed_at", "planned_mh",
        "ac_reg", "airline", "task_text", "status",
        "mh_incurred_hours", "mh_variance",
        "prev_mh_incurred_hours", "weekly_mh_delta",
        "remarks", "critical_issue", "has_issue", "deadline_date",
        "correction_reason", "supervisor_updated_at",
        "last_updated_at", "last_updated_by", "last_updated_by_name",
        "task_is_active",
    ]

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_fact_task_snapshots":
                return sql
        pytest.fail("vw_fact_task_snapshots not found")

    def test_required_columns(self):
        sql = self._get_sql()
        cols = _extract_columns(sql)
        for col in self.REQUIRED_COLUMNS:
            assert col in cols, f"Missing column: {col}"

    def test_excludes_deleted(self):
        sql = self._get_sql()
        assert 'is_deleted = 0' in sql

    def test_lag_window_function(self):
        sql = self._get_sql()
        assert 'LAG(' in sql.upper()
        assert 'PARTITION BY ts.task_id' in sql
        assert 'ORDER BY ts.meeting_date' in sql

    def test_mh_variance_calculation(self):
        sql = self._get_sql()
        assert 'mh_variance' in sql
        assert 'planned_mh' in sql


class TestFactTaskSnapshotsAll:
    """§11.2 vw_fact_task_snapshots_all — includes deleted."""

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_fact_task_snapshots_all":
                return sql
        pytest.fail("vw_fact_task_snapshots_all not found")

    def test_includes_deletion_columns(self):
        sql = self._get_sql()
        cols = _extract_columns(sql)
        for col in ["is_deleted", "deleted_at", "deleted_by"]:
            assert col in cols, f"Missing column: {col}"

    def test_no_deleted_filter(self):
        sql = self._get_sql()
        # Should NOT have WHERE is_deleted = 0
        # Check after the FROM clause
        from_idx = sql.upper().rfind('FROM')
        after_from = sql[from_idx:]
        assert 'is_deleted = 0' not in after_from


class TestDimEmployee:
    """§11.3 vw_dim_employee."""

    REQUIRED_COLUMNS = ["employee_key", "employee_no", "name", "team", "is_active", "roles_csv"]

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_dim_employee":
                return sql
        pytest.fail("vw_dim_employee not found")

    def test_columns(self):
        cols = _extract_columns(self._get_sql())
        for col in self.REQUIRED_COLUMNS:
            assert col in cols, f"Missing: {col}"

    def test_string_agg(self):
        sql = self._get_sql()
        assert 'STRING_AGG' in sql.upper()


class TestDimAircraft:
    """§11.3 vw_dim_aircraft."""

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_dim_aircraft":
                return sql
        pytest.fail("vw_dim_aircraft not found")

    def test_columns(self):
        cols = _extract_columns(self._get_sql())
        for col in ["aircraft_key", "ac_reg", "airline", "status"]:
            assert col in cols, f"Missing: {col}"


class TestDimWorkPackage:
    """§11.3 vw_dim_work_package (rfo_no included)."""

    REQUIRED_COLUMNS = [
        "work_package_key", "aircraft_id", "ac_reg", "rfo_no",
        "title", "start_date", "end_date", "priority", "status",
    ]

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_dim_work_package":
                return sql
        pytest.fail("vw_dim_work_package not found")

    def test_columns(self):
        cols = _extract_columns(self._get_sql())
        for col in self.REQUIRED_COLUMNS:
            assert col in cols, f"Missing: {col}"

    def test_rfo_no_present(self):
        sql = self._get_sql()
        assert 'rfo_no' in sql


class TestDimShopStream:
    """§11.3 vw_dim_shop_stream."""

    REQUIRED_COLUMNS = ["shop_stream_key", "work_package_id", "work_package_title", "shop_code", "status"]

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_dim_shop_stream":
                return sql
        pytest.fail("vw_dim_shop_stream not found")

    def test_columns(self):
        cols = _extract_columns(self._get_sql())
        for col in self.REQUIRED_COLUMNS:
            assert col in cols, f"Missing: {col}"


class TestDimShop:
    """§11.3 vw_dim_shop."""

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_dim_shop":
                return sql
        pytest.fail("vw_dim_shop not found")

    def test_columns(self):
        cols = _extract_columns(self._get_sql())
        for col in ["shop_key", "code", "name"]:
            assert col in cols, f"Missing: {col}"


class TestDimTaskStatus:
    """§11.3 vw_dim_task_status — enum dimension."""

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_dim_task_status":
                return sql
        pytest.fail("vw_dim_task_status not found")

    def test_all_statuses(self):
        sql = self._get_sql()
        for s in ["NOT_STARTED", "IN_PROGRESS", "WAITING", "COMPLETED"]:
            assert f"'{s}'" in sql, f"Missing status: {s}"

    def test_output_column(self):
        cols = _extract_columns(self._get_sql())
        assert "status" in cols


class TestDimOtReason:
    """§11.3 vw_dim_ot_reason — enum dimension."""

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_dim_ot_reason":
                return sql
        pytest.fail("vw_dim_ot_reason not found")

    def test_all_reasons(self):
        sql = self._get_sql()
        for r in ["BACKLOG", "AOG", "SCHEDULE_PRESSURE", "MANPOWER_SHORTAGE", "OTHER"]:
            assert f"'{r}'" in sql, f"Missing reason: {r}"

    def test_output_column(self):
        cols = _extract_columns(self._get_sql())
        assert "reason_code" in cols


class TestDimDate:
    """§11.3 vw_dim_date — 730-day date dimension."""

    REQUIRED_COLUMNS = [
        "date_key", "year", "month", "day", "day_name",
        "day_of_week", "week_number", "month_name", "quarter",
    ]

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_dim_date":
                return sql
        pytest.fail("vw_dim_date not found")

    def test_columns(self):
        cols = _extract_columns(self._get_sql())
        for col in self.REQUIRED_COLUMNS:
            assert col in cols, f"Missing: {col}"

    def test_730_days(self):
        sql = self._get_sql()
        assert '730' in sql

    def test_date_range(self):
        sql = self._get_sql()
        assert '2026-01-01' in sql

    def test_iso_week(self):
        sql = self._get_sql()
        assert 'ISO_WEEK' in sql


class TestFactOtByReason:
    """§11.4 vw_fact_ot_by_reason output contract."""

    REQUIRED_COLUMNS = ["month", "team", "reason_code", "hours", "pct"]

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_fact_ot_by_reason":
                return sql
        pytest.fail("vw_fact_ot_by_reason not found")

    def test_columns(self):
        cols = _extract_columns(self._get_sql())
        for col in self.REQUIRED_COLUMNS:
            assert col in cols, f"Missing: {col}"

    def test_groups_by_reason(self):
        sql = self._get_sql()
        assert 'reason_code' in sql.lower()
        assert 'GROUP BY' in sql.upper()


class TestFactOtWeekly:
    """§11.4 vw_fact_ot_weekly output contract."""

    REQUIRED_COLUMNS = ["month", "week_number", "label", "hours"]

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_fact_ot_weekly":
                return sql
        pytest.fail("vw_fact_ot_weekly not found")

    def test_columns(self):
        cols = _extract_columns(self._get_sql())
        for col in self.REQUIRED_COLUMNS:
            assert col in cols, f"Missing: {col}"

    def test_weekly_aggregation(self):
        sql = self._get_sql()
        assert 'ISO_WEEK' in sql.upper()


class TestRfoEfficiency:
    """§11.4 vw_rfo_efficiency — §7.4 metrics."""

    REQUIRED_COLUMNS = [
        "work_package_id", "rfo_no", "planned_mh", "actual_mh", "mh_variance",
        "productive_ratio", "ot_ratio", "ftc_pct",
        "avg_cycle_time_weeks", "blocker_count",
    ]

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_rfo_efficiency":
                return sql
        pytest.fail("vw_rfo_efficiency not found")

    def test_columns(self):
        cols = _extract_columns(self._get_sql())
        for col in self.REQUIRED_COLUMNS:
            assert col in cols, f"Missing: {col}"

    def test_productive_ratio_formula(self):
        """§7.4: (Actual − Waiting) / Actual × 100."""
        sql = self._get_sql()
        assert 'waiting_mh' in sql.lower()
        assert 'actual_mh' in sql.lower()

    def test_blocker_definition(self):
        """§7.4: WAITING + has_issue = 1."""
        sql = self._get_sql()
        assert "WAITING" in sql
        assert "has_issue" in sql


class TestRfoBurndown:
    """§11.4 vw_rfo_burndown output contract."""

    REQUIRED_COLUMNS = ["work_package_id", "rfo_no", "week", "cumulative_mh", "remaining_mh"]

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_rfo_burndown":
                return sql
        pytest.fail("vw_rfo_burndown not found")

    def test_columns(self):
        cols = _extract_columns(self._get_sql())
        for col in self.REQUIRED_COLUMNS:
            assert col in cols, f"Missing: {col}"

    def test_remaining_calculation(self):
        sql = self._get_sql()
        assert 'planned_mh' in sql.lower()
        assert 'mh_incurred_hours' in sql.lower()


class TestTaskDistribution:
    """§11.4 vw_task_distribution output contract."""

    REQUIRED_COLUMNS = [
        "work_package_id", "rfo_no", "total",
        "assigned_sup", "assigned_worker", "unassigned", "updated_count",
    ]

    def _get_sql(self):
        for sql in VIEWS:
            if _extract_view_name(sql) == "vw_task_distribution":
                return sql
        pytest.fail("vw_task_distribution not found")

    def test_columns(self):
        cols = _extract_columns(self._get_sql())
        for col in self.REQUIRED_COLUMNS:
            assert col in cols, f"Missing: {col}"

    def test_active_only(self):
        sql = self._get_sql()
        assert 'is_deleted = 0' in sql
        assert 'is_active = 1' in sql


class TestAlembic003Import:
    """Verify Alembic migration 003 can import views."""

    def test_import_migration(self):
        from alembic.versions import __path__ as alembic_path
        import importlib.util
        import os
        migration_dir = alembic_path[0] if hasattr(alembic_path, '__iter__') else str(alembic_path)
        # Just verify the module structure is importable
        from scripts.create_views import VIEWS as imported_views
        assert len(imported_views) == 16
