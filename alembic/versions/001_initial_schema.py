"""001 initial schema – core + OT + audit + system_config + future tables

SSOT: ERP_Integrated_SSOT_v2.0 MiniPatch 1-12b-fix2
Sections: §5.1 Enums (CHECK), §5.2 Core Tables, §5.4 Future Tables (DDL only)
"""
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── §5.2 Core Tables ─────────────────────────────────────────────

    # users
    op.execute("""
    CREATE TABLE users (
        id           BIGINT IDENTITY(1,1) PRIMARY KEY,
        employee_no  NVARCHAR(20)  NOT NULL,
        name         NVARCHAR(100) NOT NULL,
        email        NVARCHAR(255) NULL,
        team         NVARCHAR(50)  NULL,
        is_active    BIT           NOT NULL DEFAULT 1,
        azure_oid    NVARCHAR(128) NULL,
        created_at   DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
        updated_at   DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),

        CONSTRAINT uq_users_employee_no UNIQUE (employee_no)
    )
    """)
    op.execute(
        "CREATE UNIQUE INDEX uq_users_email_not_null "
        "ON users(email) WHERE email IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_users_azure_oid_not_null "
        "ON users(azure_oid) WHERE azure_oid IS NOT NULL"
    )
    op.execute("CREATE INDEX idx_users_team ON users(team)")

    # roles & seed
    op.execute("""
    CREATE TABLE roles (
        id   INT IDENTITY(1,1) PRIMARY KEY,
        name NVARCHAR(20) NOT NULL UNIQUE
             CHECK (name IN ('WORKER', 'SUPERVISOR', 'ADMIN'))
    )
    """)
    op.execute(
        "INSERT INTO roles (name) VALUES ('WORKER'), ('SUPERVISOR'), ('ADMIN')"
    )

    # user_roles
    op.execute("""
    CREATE TABLE user_roles (
        user_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
        role_id  INT    NOT NULL REFERENCES roles(id) ON DELETE NO ACTION,
        CONSTRAINT pk_user_roles PRIMARY KEY (user_id, role_id)
    )
    """)

    # aircraft
    op.execute("""
    CREATE TABLE aircraft (
        id         BIGINT IDENTITY(1,1) PRIMARY KEY,
        ac_reg     NVARCHAR(20) NOT NULL UNIQUE,
        airline    NVARCHAR(100) NULL,
        status     NVARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                  CHECK (status IN ('ACTIVE', 'COMPLETED', 'ON_HOLD', 'CANCELLED')),
        created_at DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
    )
    """)

    # work_packages
    op.execute("""
    CREATE TABLE work_packages (
        id          BIGINT IDENTITY(1,1) PRIMARY KEY,
        aircraft_id BIGINT NOT NULL REFERENCES aircraft(id) ON DELETE NO ACTION,
        rfo_no      NVARCHAR(50) NULL,
        title       NVARCHAR(200) NOT NULL,
        start_date  DATE NULL,
        end_date    DATE NULL,
        priority    SMALLINT NULL DEFAULT 0,
        status      NVARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                   CHECK (status IN ('ACTIVE', 'COMPLETED', 'ON_HOLD', 'CANCELLED')),
        created_at  DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
    )
    """)
    op.execute(
        "CREATE UNIQUE INDEX uq_wp_rfo_no_not_null "
        "ON work_packages(rfo_no) WHERE rfo_no IS NOT NULL"
    )
    op.execute("CREATE INDEX idx_wp_aircraft ON work_packages(aircraft_id)")
    op.execute("CREATE INDEX idx_wp_status ON work_packages(status)")

    # shop_streams
    op.execute("""
    CREATE TABLE shop_streams (
        id              BIGINT IDENTITY(1,1) PRIMARY KEY,
        work_package_id BIGINT NOT NULL REFERENCES work_packages(id) ON DELETE NO ACTION,
        shop_code       NVARCHAR(20) NOT NULL,
        status          NVARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                       CHECK (status IN ('ACTIVE', 'COMPLETED', 'ON_HOLD', 'CANCELLED')),
        created_at      DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
        CONSTRAINT uq_shop_stream UNIQUE (work_package_id, shop_code)
    )
    """)
    op.execute("CREATE INDEX idx_ss_wp ON shop_streams(work_package_id)")

    # ot_requests
    op.execute("""
    CREATE TABLE ot_requests (
        id                BIGINT IDENTITY(1,1) PRIMARY KEY,
        user_id           BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
        submitted_by      BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
        work_package_id   BIGINT NULL REFERENCES work_packages(id) ON DELETE NO ACTION,
        shop_stream_id    BIGINT NULL REFERENCES shop_streams(id) ON DELETE NO ACTION,
        [date]            DATE NOT NULL,
        start_time        TIME NOT NULL,
        end_time          TIME NOT NULL,
        requested_minutes INT NOT NULL CHECK (requested_minutes > 0),
        reason_code       NVARCHAR(30) NOT NULL DEFAULT 'OTHER'
                         CHECK (reason_code IN ('BACKLOG','AOG','SCHEDULE_PRESSURE','MANPOWER_SHORTAGE','OTHER')),
        reason_text       NVARCHAR(MAX) NULL,
        status            NVARCHAR(20) NOT NULL DEFAULT 'PENDING'
                         CHECK (status IN ('PENDING','ENDORSED','APPROVED','REJECTED','CANCELLED')),
        created_at        DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
        updated_at        DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),

        CONSTRAINT chk_ot_time CHECK (end_time > start_time)
    )
    """)
    op.execute("CREATE INDEX idx_ot_user_date ON ot_requests(user_id, [date])")
    op.execute("CREATE INDEX idx_ot_status ON ot_requests(status)")
    op.execute("CREATE INDEX idx_ot_date ON ot_requests([date])")
    op.execute(
        "CREATE INDEX idx_ot_submitted_by "
        "ON ot_requests(submitted_by) WHERE submitted_by IS NOT NULL"
    )

    # ot_approvals
    op.execute("""
    CREATE TABLE ot_approvals (
        id              BIGINT IDENTITY(1,1) PRIMARY KEY,
        ot_request_id   BIGINT NOT NULL REFERENCES ot_requests(id) ON DELETE NO ACTION,
        approver_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
        stage           NVARCHAR(20) NOT NULL
                       CHECK (stage IN ('ENDORSE','APPROVE')),
        action          NVARCHAR(20) NOT NULL
                       CHECK (action IN ('APPROVE','REJECT')),
        comment         NVARCHAR(MAX) NULL,
        acted_at        DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
    )
    """)
    op.execute("CREATE INDEX idx_ota_request ON ot_approvals(ot_request_id)")
    op.execute("CREATE INDEX idx_ota_approver ON ot_approvals(approver_id)")
    op.execute("CREATE INDEX idx_ota_stage ON ot_approvals(stage)")

    # audit_logs
    op.execute("""
    CREATE TABLE audit_logs (
        id          BIGINT IDENTITY(1,1) PRIMARY KEY,
        actor_id    BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
        entity_type NVARCHAR(50) NOT NULL,
        entity_id   BIGINT NOT NULL,
        action      NVARCHAR(20) NOT NULL,
        before_json NVARCHAR(MAX) NULL,
        after_json  NVARCHAR(MAX) NULL,
        created_at  DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
    )
    """)
    op.execute(
        "CREATE INDEX idx_audit_entity ON audit_logs(entity_type, entity_id)"
    )
    op.execute("CREATE INDEX idx_audit_actor ON audit_logs(actor_id)")
    op.execute("CREATE INDEX idx_audit_created ON audit_logs(created_at)")

    # system_config
    op.execute("""
    CREATE TABLE system_config (
        id         BIGINT IDENTITY(1,1) PRIMARY KEY,
        [key]      NVARCHAR(100) NOT NULL,
        value      NVARCHAR(MAX) NOT NULL DEFAULT '',
        updated_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
        updated_at DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
        CONSTRAINT uq_system_config_key UNIQUE ([key])
    )
    """)

    # system_config seed (MVP defaults)
    op.execute("""
    INSERT INTO system_config ([key], value) VALUES
      ('meeting_current_date',        '2026-02-26'),
      ('meeting_auto_advance',        'every_monday'),
      ('needs_update_threshold_hours', '72'),
      ('teams_enabled',               'true'),
      ('teams_recipients',            '#cis-sheet-metal'),
      ('teams_message_template',      'Weekly Summary — {shop} · Week {week}: {task_count} tasks, {issues} issues flagged.'),
      ('outlook_enabled',             'false'),
      ('outlook_recipients',          ''),
      ('outlook_subject_template',    '[CIS ERP] OT Approval Reminder — {date}'),
      ('outlook_body_template',       'You have {pending_count} pending OT requests awaiting approval for {shop}. Please review at your earliest convenience.'),
      ('critical_alert_enabled',      'true'),
      ('critical_alert_recipients',   '#cis-alerts')
    """)

    # ── §5.4 Future Tables (DDL only — Phase 2/3) ────────────────────

    # Phase 2: Attendance
    op.execute("""
    CREATE TABLE shift_templates (
        id                 INT IDENTITY(1,1) PRIMARY KEY,
        code               NVARCHAR(20) NOT NULL UNIQUE,
        start_time         TIME NOT NULL,
        end_time           TIME NOT NULL,
        paid_break_minutes INT NOT NULL DEFAULT 60
    )
    """)

    op.execute("""
    CREATE TABLE shift_assignments (
        id                BIGINT IDENTITY(1,1) PRIMARY KEY,
        user_id           BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
        shift_template_id INT NOT NULL REFERENCES shift_templates(id) ON DELETE NO ACTION,
        [date]            DATE NOT NULL,
        source            NVARCHAR(20) NULL DEFAULT 'IMPORT',
        CONSTRAINT uq_shift_assignment UNIQUE (user_id, [date])
    )
    """)

    op.execute("""
    CREATE TABLE attendance_events (
        id          BIGINT IDENTITY(1,1) PRIMARY KEY,
        user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
        [date]      DATE NOT NULL,
        [type]      NVARCHAR(30) NOT NULL,
        start_time  TIME NULL,
        end_time    TIME NULL,
        minutes_delta INT NULL,
        note        NVARCHAR(MAX) NULL,
        approved_by BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
        created_at  DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
    )
    """)

    # Phase 3: MH Tracking
    op.execute("""
    CREATE TABLE daily_assignments (
        id             BIGINT IDENTITY(1,1) PRIMARY KEY,
        user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
        shop_stream_id BIGINT NOT NULL REFERENCES shop_streams(id) ON DELETE NO ACTION,
        [date]         DATE NOT NULL,
        planned_minutes INT NOT NULL CHECK (planned_minutes >= 0),
        note           NVARCHAR(MAX) NULL,
        assigned_by    BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
        created_at     DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
    )
    """)

    op.execute("""
    CREATE TABLE worklog_blocks (
        id             BIGINT IDENTITY(1,1) PRIMARY KEY,
        user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
        shop_stream_id BIGINT NOT NULL REFERENCES shop_streams(id) ON DELETE NO ACTION,
        [date]         DATE NOT NULL,
        started_at     DATETIMEOFFSET NOT NULL,
        ended_at       DATETIMEOFFSET NOT NULL,
        minutes        INT NOT NULL CHECK (minutes > 0),
        created_at     DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE()
    )
    """)

    op.execute("""
    CREATE TABLE time_ledger_daily (
        id                    BIGINT IDENTITY(1,1) PRIMARY KEY,
        user_id               BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
        [date]                DATE NOT NULL,
        capacity_minutes      INT NULL,
        planned_minutes_total INT NULL,
        actual_minutes_total  INT NULL,
        regular_minutes       INT NULL,
        ot_minutes_actual     INT NULL,
        ot_minutes_approved   INT NULL,
        cost_regular          NUMERIC(12,2) NULL,
        cost_ot               NUMERIC(12,2) NULL,
        calc_version          INT NOT NULL DEFAULT 1,
        calculated_at         DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
        CONSTRAINT uq_ledger_daily UNIQUE (user_id, [date])
    )
    """)

    op.execute("""
    CREATE TABLE ledger_allocations_daily (
        id                        BIGINT IDENTITY(1,1) PRIMARY KEY,
        user_id                   BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
        shop_stream_id            BIGINT NOT NULL REFERENCES shop_streams(id) ON DELETE NO ACTION,
        [date]                    DATE NOT NULL,
        allocated_regular_minutes INT NULL,
        allocated_ot_minutes      INT NULL,
        source                    NVARCHAR(20) NULL DEFAULT 'PLANNED'
    )
    """)


def downgrade() -> None:
    # Future tables (reverse dependency order)
    op.execute("DROP TABLE IF EXISTS ledger_allocations_daily")
    op.execute("DROP TABLE IF EXISTS time_ledger_daily")
    op.execute("DROP TABLE IF EXISTS worklog_blocks")
    op.execute("DROP TABLE IF EXISTS daily_assignments")
    op.execute("DROP TABLE IF EXISTS attendance_events")
    op.execute("DROP TABLE IF EXISTS shift_assignments")
    op.execute("DROP TABLE IF EXISTS shift_templates")

    # Core tables (reverse dependency order)
    op.execute("DROP TABLE IF EXISTS system_config")
    op.execute("DROP TABLE IF EXISTS audit_logs")
    op.execute("DROP TABLE IF EXISTS ot_approvals")
    op.execute("DROP TABLE IF EXISTS ot_requests")
    op.execute("DROP TABLE IF EXISTS shop_streams")
    op.execute("DROP TABLE IF EXISTS work_packages")
    op.execute("DROP TABLE IF EXISTS aircraft")
    op.execute("DROP TABLE IF EXISTS user_roles")
    op.execute("DROP TABLE IF EXISTS roles")
    op.execute("DROP TABLE IF EXISTS users")
