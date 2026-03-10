"""Branch 10 — scripts/create_views.py

SQL Server reporting views (CREATE OR ALTER VIEW).
SSOT §11 — Power BI star schema: fact + dimension views.

Usage:
    python scripts/create_views.py          # Execute against DATABASE_URL
    python scripts/create_views.py --dry    # Print SQL only (no DB execution)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# ── View SQL definitions (MSSQL / T-SQL) ──────────────────────────────

VIEWS: list[str] = []

# ====================================================================
# §11.1  Fact: OT Requests  (2-stage approval chain)
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_fact_ot_requests AS
SELECT
    otr.id                  AS ot_request_id,
    otr.user_id,
    u.employee_no,
    u.name                  AS employee_name,
    u.team,
    otr.submitted_by,
    sb.name                 AS submitted_by_name,
    sb.employee_no          AS submitted_by_employee_no,
    otr.[date]              AS ot_date,
    otr.start_time,
    otr.end_time,
    otr.requested_minutes,
    otr.reason_code,
    otr.status,
    otr.work_package_id,
    otr.shop_stream_id,
    wp.rfo_no,
    wp.title                AS work_package_title,
    ac.ac_reg,
    ac.airline,
    ss.shop_code,

    -- Stage 1 (SUPERVISOR endorse)
    ota1.approver_id        AS endorser_id,
    eu.name                 AS endorser_name,
    ota1.action             AS endorse_action,
    ota1.comment            AS endorse_comment,
    ota1.acted_at           AS endorsed_at,

    -- Stage 2 (ADMIN approve)
    ota2.approver_id        AS final_approver_id,
    au.name                 AS final_approver_name,
    ota2.action             AS approval_action,
    ota2.comment            AS approval_comment,
    ota2.acted_at           AS approved_at,

    -- Timestamps
    otr.created_at          AS submitted_at,

    -- Turnaround: submit → final approval (hours)
    CAST(DATEDIFF(SECOND, otr.created_at, ota2.acted_at) AS FLOAT) / 3600.0
                            AS turnaround_hours,

    -- Turnaround: submit → endorse (hours)
    CAST(DATEDIFF(SECOND, otr.created_at, ota1.acted_at) AS FLOAT) / 3600.0
                            AS endorse_turnaround_hours

FROM dbo.ot_requests otr
JOIN dbo.users u ON u.id = otr.user_id
LEFT JOIN dbo.users sb ON sb.id = otr.submitted_by

LEFT JOIN dbo.ot_approvals ota1
    ON ota1.ot_request_id = otr.id AND ota1.stage = 'ENDORSE'
LEFT JOIN dbo.ot_approvals ota2
    ON ota2.ot_request_id = otr.id AND ota2.stage = 'APPROVE'
LEFT JOIN dbo.users eu ON eu.id = ota1.approver_id
LEFT JOIN dbo.users au ON au.id = ota2.approver_id

LEFT JOIN dbo.work_packages wp ON wp.id = otr.work_package_id
LEFT JOIN dbo.aircraft ac ON ac.id = wp.aircraft_id
LEFT JOIN dbo.shop_streams ss ON ss.id = otr.shop_stream_id;
""")

# ====================================================================
# §11.2  Fact: Task Snapshots (active only — is_deleted = 0)
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_fact_task_snapshots AS
SELECT
    ts.id                   AS snapshot_id,
    ts.task_id,
    ts.meeting_date,
    ti.shop_id,
    s.code                  AS shop_code,
    s.name                  AS shop_name,
    ti.aircraft_id,
    ti.work_package_id,
    wp.rfo_no,
    wp.title                AS work_package_title,
    ti.assigned_supervisor_id,
    sup.name                AS assigned_supervisor_name,
    ti.assigned_worker_id,
    wu.name                 AS assigned_worker_name,
    ti.distributed_at,
    ti.planned_mh,
    ac.ac_reg,
    ac.airline,
    ti.task_text,
    ts.status,
    ts.mh_incurred_hours,
    CASE
        WHEN ti.planned_mh IS NULL THEN NULL
        ELSE CAST(ts.mh_incurred_hours AS DECIMAL(10,2))
             - CAST(ti.planned_mh AS DECIMAL(10,2))
    END                     AS mh_variance,
    LAG(ts.mh_incurred_hours) OVER (
        PARTITION BY ts.task_id ORDER BY ts.meeting_date
    )                       AS prev_mh_incurred_hours,
    ts.mh_incurred_hours - COALESCE(
        LAG(ts.mh_incurred_hours) OVER (
            PARTITION BY ts.task_id ORDER BY ts.meeting_date
        ), 0
    )                       AS weekly_mh_delta,
    ts.remarks,
    ts.critical_issue,
    ts.has_issue,
    ts.deadline_date,
    ts.correction_reason,
    ts.supervisor_updated_at,
    ts.last_updated_at,
    ts.last_updated_by,
    lu.name                 AS last_updated_by_name,
    ti.is_active            AS task_is_active
FROM dbo.task_snapshots ts
JOIN dbo.task_items ti ON ti.id = ts.task_id
JOIN dbo.shops s ON s.id = ti.shop_id
JOIN dbo.aircraft ac ON ac.id = ti.aircraft_id
LEFT JOIN dbo.work_packages wp ON wp.id = ti.work_package_id
LEFT JOIN dbo.users sup ON sup.id = ti.assigned_supervisor_id
LEFT JOIN dbo.users wu ON wu.id = ti.assigned_worker_id
LEFT JOIN dbo.users lu ON lu.id = ts.last_updated_by
WHERE ts.is_deleted = 0;
""")

# ====================================================================
# §11.2  Fact: Task Snapshots ALL (includes soft-deleted)
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_fact_task_snapshots_all AS
SELECT
    ts.id                   AS snapshot_id,
    ts.task_id,
    ts.meeting_date,
    ti.shop_id,
    s.code                  AS shop_code,
    s.name                  AS shop_name,
    ti.aircraft_id,
    ti.work_package_id,
    wp.rfo_no,
    wp.title                AS work_package_title,
    ti.assigned_supervisor_id,
    sup.name                AS assigned_supervisor_name,
    ti.assigned_worker_id,
    wu.name                 AS assigned_worker_name,
    ti.distributed_at,
    ti.planned_mh,
    ac.ac_reg,
    ac.airline,
    ti.task_text,
    ts.status,
    ts.mh_incurred_hours,
    CASE
        WHEN ti.planned_mh IS NULL THEN NULL
        ELSE CAST(ts.mh_incurred_hours AS DECIMAL(10,2))
             - CAST(ti.planned_mh AS DECIMAL(10,2))
    END                     AS mh_variance,
    LAG(ts.mh_incurred_hours) OVER (
        PARTITION BY ts.task_id ORDER BY ts.meeting_date
    )                       AS prev_mh_incurred_hours,
    ts.mh_incurred_hours - COALESCE(
        LAG(ts.mh_incurred_hours) OVER (
            PARTITION BY ts.task_id ORDER BY ts.meeting_date
        ), 0
    )                       AS weekly_mh_delta,
    ts.remarks,
    ts.critical_issue,
    ts.has_issue,
    ts.deadline_date,
    ts.correction_reason,
    ts.supervisor_updated_at,
    ts.is_deleted,
    ts.deleted_at,
    ts.deleted_by,
    ts.last_updated_at,
    ts.last_updated_by,
    lu.name                 AS last_updated_by_name,
    ti.is_active            AS task_is_active
FROM dbo.task_snapshots ts
JOIN dbo.task_items ti ON ti.id = ts.task_id
JOIN dbo.shops s ON s.id = ti.shop_id
JOIN dbo.aircraft ac ON ac.id = ti.aircraft_id
LEFT JOIN dbo.work_packages wp ON wp.id = ti.work_package_id
LEFT JOIN dbo.users sup ON sup.id = ti.assigned_supervisor_id
LEFT JOIN dbo.users wu ON wu.id = ti.assigned_worker_id
LEFT JOIN dbo.users lu ON lu.id = ts.last_updated_by;
""")

# ====================================================================
# §11.3  Dimension: Employee (with roles CSV)
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_dim_employee AS
SELECT
    u.id            AS employee_key,
    u.employee_no,
    u.name,
    u.team,
    u.is_active,
    STRING_AGG(r.name, ',') WITHIN GROUP (ORDER BY r.name) AS roles_csv
FROM dbo.users u
JOIN dbo.user_roles ur ON ur.user_id = u.id
JOIN dbo.roles r ON r.id = ur.role_id
GROUP BY u.id, u.employee_no, u.name, u.team, u.is_active;
""")

# ====================================================================
# §11.3  Dimension: Aircraft
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_dim_aircraft AS
SELECT id AS aircraft_key, ac_reg, airline, status
FROM dbo.aircraft;
""")

# ====================================================================
# §11.3  Dimension: Work Package (rfo_no included)
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_dim_work_package AS
SELECT
    wp.id           AS work_package_key,
    wp.aircraft_id,
    ac.ac_reg,
    wp.rfo_no,
    wp.title,
    wp.start_date,
    wp.end_date,
    wp.priority,
    wp.status
FROM dbo.work_packages wp
JOIN dbo.aircraft ac ON ac.id = wp.aircraft_id;
""")

# ====================================================================
# §11.3  Dimension: Shop Stream
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_dim_shop_stream AS
SELECT
    ss.id               AS shop_stream_key,
    ss.work_package_id,
    wp.title            AS work_package_title,
    ss.shop_code,
    ss.status
FROM dbo.shop_streams ss
JOIN dbo.work_packages wp ON wp.id = ss.work_package_id;
""")

# ====================================================================
# §11.3  Dimension: Shop
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_dim_shop AS
SELECT id AS shop_key, code, name
FROM dbo.shops;
""")

# ====================================================================
# §11.3  Dimension: Task Status (enum)
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_dim_task_status AS
SELECT 'NOT_STARTED' AS status
UNION ALL SELECT 'IN_PROGRESS'
UNION ALL SELECT 'WAITING'
UNION ALL SELECT 'COMPLETED';
""")

# ====================================================================
# §11.3  Dimension: OT Reason (enum)
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_dim_ot_reason AS
SELECT 'BACKLOG' AS reason_code
UNION ALL SELECT 'AOG'
UNION ALL SELECT 'SCHEDULE_PRESSURE'
UNION ALL SELECT 'MANPOWER_SHORTAGE'
UNION ALL SELECT 'OTHER';
""")

# ====================================================================
# §11.3  Dimension: Date (2026-01-01 → 2027-12-31, 730 days)
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_dim_date AS
WITH nums AS (
    SELECT TOP (730)
        ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS n
    FROM sys.all_objects a
    CROSS JOIN sys.all_objects b
),
dates AS (
    SELECT DATEADD(DAY, n, CAST('2026-01-01' AS DATE)) AS date_key
    FROM nums
)
SELECT
    date_key,
    DATEPART(YEAR, date_key)      AS [year],
    DATEPART(MONTH, date_key)     AS [month],
    DATEPART(DAY, date_key)       AS [day],
    DATENAME(WEEKDAY, date_key)   AS day_name,
    DATEPART(WEEKDAY, date_key)   AS day_of_week,
    DATEPART(ISO_WEEK, date_key)  AS week_number,
    DATENAME(MONTH, date_key)     AS month_name,
    DATEPART(QUARTER, date_key)   AS quarter
FROM dates;
""")

# ====================================================================
# §11.4  MiniPatch 12: OT by Reason (§7.4)
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_fact_ot_by_reason AS
SELECT
    FORMAT(otr.[date], 'yyyy-MM')                   AS [month],
    u.team,
    otr.reason_code,
    SUM(otr.requested_minutes) / 60.0               AS hours,
    CASE
        WHEN SUM(SUM(otr.requested_minutes)) OVER (
            PARTITION BY FORMAT(otr.[date], 'yyyy-MM'), u.team
        ) = 0 THEN 0
        ELSE CAST(SUM(otr.requested_minutes) AS FLOAT)
             / SUM(SUM(otr.requested_minutes)) OVER (
                 PARTITION BY FORMAT(otr.[date], 'yyyy-MM'), u.team
             ) * 100.0
    END                                             AS pct
FROM dbo.ot_requests otr
JOIN dbo.users u ON u.id = otr.user_id
WHERE otr.status IN ('APPROVED', 'ENDORSED', 'PENDING')
GROUP BY FORMAT(otr.[date], 'yyyy-MM'), u.team, otr.reason_code;
""")

# ====================================================================
# §11.4  MiniPatch 12: OT Weekly Trend
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_fact_ot_weekly AS
SELECT
    FORMAT(otr.[date], 'yyyy-MM')                   AS [month],
    DATEPART(ISO_WEEK, otr.[date])                  AS week_number,
    CONCAT('W', DATEPART(ISO_WEEK, otr.[date]))     AS label,
    SUM(otr.requested_minutes) / 60.0               AS hours
FROM dbo.ot_requests otr
WHERE otr.status IN ('APPROVED', 'ENDORSED', 'PENDING')
GROUP BY FORMAT(otr.[date], 'yyyy-MM'), DATEPART(ISO_WEEK, otr.[date]);
""")

# ====================================================================
# §11.4  MiniPatch 12: RFO Efficiency (§7.4 metrics)
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_rfo_efficiency AS
WITH task_agg AS (
    SELECT
        ti.work_package_id,
        SUM(ti.planned_mh)                                      AS planned_mh,
        SUM(ts.mh_incurred_hours)                                AS actual_mh,
        SUM(ts.mh_incurred_hours) - SUM(ISNULL(ti.planned_mh, 0))
                                                                 AS mh_variance,
        -- Waiting MH (tasks with status = WAITING)
        SUM(CASE WHEN ts.status = 'WAITING' THEN ts.mh_incurred_hours ELSE 0 END)
                                                                 AS waiting_mh,
        -- Completed count / total for FTC
        SUM(CASE WHEN ts.status = 'COMPLETED' THEN 1 ELSE 0 END)
                                                                 AS completed_count,
        COUNT(*)                                                 AS total_count,
        -- Blocker count (WAITING + has_issue)
        SUM(CASE WHEN ts.status = 'WAITING' AND ts.has_issue = 1 THEN 1 ELSE 0 END)
                                                                 AS blocker_count
    FROM dbo.task_items ti
    JOIN dbo.task_snapshots ts ON ts.task_id = ti.id
    WHERE ts.is_deleted = 0
      AND ti.is_active = 1
      -- Use latest meeting_date snapshot per task
      AND ts.meeting_date = (
          SELECT MAX(ts2.meeting_date)
          FROM dbo.task_snapshots ts2
          WHERE ts2.task_id = ti.id AND ts2.is_deleted = 0
      )
    GROUP BY ti.work_package_id
),
cycle AS (
    -- Avg cycle time: weeks from first IN_PROGRESS to COMPLETED
    SELECT
        ti.work_package_id,
        AVG(
            CAST(DATEDIFF(DAY, first_ip.first_ip_date, last_c.completed_date) AS FLOAT) / 7.0
        ) AS avg_cycle_time_weeks
    FROM dbo.task_items ti
    JOIN (
        SELECT task_id, MIN(meeting_date) AS first_ip_date
        FROM dbo.task_snapshots
        WHERE status = 'IN_PROGRESS' AND is_deleted = 0
        GROUP BY task_id
    ) first_ip ON first_ip.task_id = ti.id
    JOIN (
        SELECT task_id, MAX(meeting_date) AS completed_date
        FROM dbo.task_snapshots
        WHERE status = 'COMPLETED' AND is_deleted = 0
        GROUP BY task_id
    ) last_c ON last_c.task_id = ti.id
    WHERE last_c.completed_date >= first_ip.first_ip_date
    GROUP BY ti.work_package_id
),
ot_agg AS (
    SELECT
        otr.work_package_id,
        SUM(otr.requested_minutes) / 60.0 AS ot_hours
    FROM dbo.ot_requests otr
    WHERE otr.status IN ('APPROVED', 'ENDORSED', 'PENDING')
      AND otr.work_package_id IS NOT NULL
    GROUP BY otr.work_package_id
)
SELECT
    wp.id                   AS work_package_id,
    wp.rfo_no,
    ISNULL(ta.planned_mh, 0)   AS planned_mh,
    ISNULL(ta.actual_mh, 0)    AS actual_mh,
    ISNULL(ta.mh_variance, 0)  AS mh_variance,
    -- Productive Ratio = (Actual − Waiting) / Actual × 100
    CASE
        WHEN ISNULL(ta.actual_mh, 0) = 0 THEN 0
        ELSE (ta.actual_mh - ISNULL(ta.waiting_mh, 0))
             / ta.actual_mh * 100.0
    END                     AS productive_ratio,
    -- OT Ratio = OT Hours / Actual MH × 100
    CASE
        WHEN ISNULL(ta.actual_mh, 0) = 0 THEN 0
        ELSE ISNULL(oa.ot_hours, 0) / ta.actual_mh * 100.0
    END                     AS ot_ratio,
    -- First-Time Completion = completed / total × 100
    CASE
        WHEN ISNULL(ta.total_count, 0) = 0 THEN 0
        ELSE CAST(ISNULL(ta.completed_count, 0) AS FLOAT)
             / ta.total_count * 100.0
    END                     AS ftc_pct,
    ISNULL(cy.avg_cycle_time_weeks, 0)  AS avg_cycle_time_weeks,
    ISNULL(ta.blocker_count, 0)         AS blocker_count
FROM dbo.work_packages wp
LEFT JOIN task_agg ta ON ta.work_package_id = wp.id
LEFT JOIN cycle cy ON cy.work_package_id = wp.id
LEFT JOIN ot_agg oa ON oa.work_package_id = wp.id;
""")

# ====================================================================
# §11.4  MiniPatch 12: RFO Burndown (weekly cumulative / remaining)
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_rfo_burndown AS
SELECT
    ti.work_package_id,
    wp.rfo_no,
    ts.meeting_date                                      AS [week],
    SUM(ts.mh_incurred_hours)                            AS cumulative_mh,
    ISNULL(SUM(ti.planned_mh), 0) - SUM(ts.mh_incurred_hours)
                                                         AS remaining_mh
FROM dbo.task_snapshots ts
JOIN dbo.task_items ti ON ti.id = ts.task_id
JOIN dbo.work_packages wp ON wp.id = ti.work_package_id
WHERE ts.is_deleted = 0
  AND ti.is_active = 1
GROUP BY ti.work_package_id, wp.rfo_no, ts.meeting_date;
""")

# ====================================================================
# §11.4  MiniPatch 12: Task Distribution summary
# ====================================================================
VIEWS.append("""
CREATE OR ALTER VIEW dbo.vw_task_distribution AS
SELECT
    ti.work_package_id,
    wp.rfo_no,
    COUNT(*)                                                            AS total,
    SUM(CASE WHEN ti.assigned_supervisor_id IS NOT NULL THEN 1 ELSE 0 END)
                                                                        AS assigned_sup,
    SUM(CASE WHEN ti.assigned_worker_id IS NOT NULL THEN 1 ELSE 0 END)
                                                                        AS assigned_worker,
    SUM(CASE WHEN ti.assigned_supervisor_id IS NULL
              AND ti.assigned_worker_id IS NULL THEN 1 ELSE 0 END)
                                                                        AS unassigned,
    SUM(CASE WHEN ts.supervisor_updated_at IS NOT NULL THEN 1 ELSE 0 END)
                                                                        AS updated_count
FROM dbo.task_items ti
JOIN dbo.task_snapshots ts ON ts.task_id = ti.id
LEFT JOIN dbo.work_packages wp ON wp.id = ti.work_package_id
WHERE ts.is_deleted = 0
  AND ti.is_active = 1
  -- Latest snapshot per task
  AND ts.meeting_date = (
      SELECT MAX(ts2.meeting_date)
      FROM dbo.task_snapshots ts2
      WHERE ts2.task_id = ti.id AND ts2.is_deleted = 0
  )
GROUP BY ti.work_package_id, wp.rfo_no;
""")


# ── View name extraction helper ───────────────────────────────────────

def get_view_names() -> list[str]:
    """Extract ordered view names from VIEWS list."""
    import re
    names = []
    for sql in VIEWS:
        m = re.search(r'CREATE OR ALTER VIEW\s+dbo\.(\w+)', sql, re.IGNORECASE)
        if m:
            names.append(m.group(1))
    return names


# ── Execution ─────────────────────────────────────────────────────────

async def execute_views(dry_run: bool = False) -> None:
    """Execute all CREATE OR ALTER VIEW statements against DATABASE_URL."""
    view_names = get_view_names()
    print(f"[create_views] {len(VIEWS)} views to create/update:")
    for name in view_names:
        print(f"  - {name}")
    print()

    if dry_run:
        for sql in VIEWS:
            print(sql.strip())
            print("GO\n")
        print("[create_views] Dry run complete — no DB changes made.")
        return

    # Import here so --dry works without DB connection
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.config import settings

    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        for i, sql in enumerate(VIEWS):
            await conn.execute(text(sql))
            print(f"  [{i+1}/{len(VIEWS)}] {view_names[i]} ✓")

    await engine.dispose()
    print(f"\n[create_views] All {len(VIEWS)} views created/updated successfully.")


def main():
    parser = argparse.ArgumentParser(description="Create/update SQL Server reporting views")
    parser.add_argument("--dry", action="store_true", help="Print SQL only, no DB execution")
    args = parser.parse_args()
    asyncio.run(execute_views(dry_run=args.dry))


if __name__ == "__main__":
    main()
