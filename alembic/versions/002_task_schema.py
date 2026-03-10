"""002 task schema – shops + user_shop_access + task_items + task_snapshots

SSOT: ERP_Integrated_SSOT_v2.0 MiniPatch 1-12b-fix2
Section: §5.3 Task Manager Tables
"""
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── §5.3.1 shops ──────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE shops (
        id         BIGINT IDENTITY(1,1) PRIMARY KEY,
        code       NVARCHAR(50)  NOT NULL UNIQUE,
        name       NVARCHAR(200) NOT NULL,
        created_at DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
        updated_at DATETIMEOFFSET NULL,
        created_by BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION
    )
    """)

    # ── §5.3.2 user_shop_access ───────────────────────────────────────
    op.execute("""
    CREATE TABLE user_shop_access (
        id         BIGINT IDENTITY(1,1) PRIMARY KEY,
        user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
        shop_id    BIGINT NOT NULL REFERENCES shops(id) ON DELETE NO ACTION,
        access     NVARCHAR(20) NOT NULL
                  CHECK (access IN ('VIEW','EDIT','MANAGE')),
        granted_at DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
        granted_by BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
        CONSTRAINT uq_user_shop UNIQUE (user_id, shop_id)
    )
    """)

    # ── §5.3.3 task_items ─────────────────────────────────────────────
    op.execute("""
    CREATE TABLE task_items (
        id                     BIGINT IDENTITY(1,1) PRIMARY KEY,
        aircraft_id            BIGINT NOT NULL REFERENCES aircraft(id) ON DELETE NO ACTION,
        shop_id                BIGINT NOT NULL REFERENCES shops(id) ON DELETE NO ACTION,
        work_package_id        BIGINT NULL REFERENCES work_packages(id) ON DELETE NO ACTION,
        assigned_supervisor_id BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
        assigned_worker_id     BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
        distributed_at         DATETIMEOFFSET NULL,
        planned_mh             DECIMAL(8,2) NULL,
        task_text              NVARCHAR(MAX) NOT NULL,
        is_active              BIT NOT NULL DEFAULT 1,
        deactivated_at         DATETIMEOFFSET NULL,
        deactivated_by         BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
        created_at             DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
        created_by             BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION
    )
    """)
    op.execute("CREATE INDEX idx_taskitem_shop ON task_items(shop_id)")
    op.execute("CREATE INDEX idx_taskitem_aircraft ON task_items(aircraft_id)")
    op.execute("CREATE INDEX idx_taskitem_active ON task_items(is_active)")
    op.execute(
        "CREATE INDEX idx_taskitem_wp ON task_items(work_package_id) "
        "WHERE work_package_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_task_items_supervisor ON task_items(assigned_supervisor_id) "
        "WHERE assigned_supervisor_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_task_items_worker ON task_items(assigned_worker_id) "
        "WHERE assigned_worker_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_task_items_distributed ON task_items(distributed_at) "
        "WHERE distributed_at IS NOT NULL"
    )

    # ── §5.3.4 task_snapshots ─────────────────────────────────────────
    op.execute("""
    CREATE TABLE task_snapshots (
        id                    BIGINT IDENTITY(1,1) PRIMARY KEY,
        task_id               BIGINT NOT NULL REFERENCES task_items(id) ON DELETE CASCADE,
        meeting_date          DATE NOT NULL,
        status                NVARCHAR(20) NOT NULL DEFAULT 'NOT_STARTED'
                             CHECK (status IN ('NOT_STARTED','IN_PROGRESS','WAITING','COMPLETED')),
        mh_incurred_hours     NUMERIC(10,2) NOT NULL DEFAULT 0,
        remarks               NVARCHAR(MAX) NULL,
        critical_issue        NVARCHAR(MAX) NULL,
        has_issue             BIT NOT NULL DEFAULT 0,
        deadline_date         DATE NULL,
        correction_reason     NVARCHAR(MAX) NULL,
        is_deleted            BIT NOT NULL DEFAULT 0,
        deleted_at            DATETIMEOFFSET NULL,
        deleted_by            BIGINT NULL REFERENCES users(id) ON DELETE NO ACTION,
        version               INT NOT NULL DEFAULT 1,
        supervisor_updated_at DATETIMEOFFSET NULL,
        last_updated_at       DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
        last_updated_by       BIGINT NOT NULL REFERENCES users(id) ON DELETE NO ACTION,
        created_at            DATETIMEOFFSET NOT NULL DEFAULT GETUTCDATE(),
        CONSTRAINT uq_task_meeting UNIQUE (task_id, meeting_date)
    )
    """)
    op.execute(
        "CREATE INDEX idx_snap_meeting_deleted ON task_snapshots(meeting_date, is_deleted)"
    )
    op.execute("CREATE INDEX idx_snap_task ON task_snapshots(task_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS task_snapshots")
    op.execute("DROP TABLE IF EXISTS task_items")
    op.execute("DROP TABLE IF EXISTS user_shop_access")
    op.execute("DROP TABLE IF EXISTS shops")
