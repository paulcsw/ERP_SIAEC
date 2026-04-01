"""004 auto week advancement config defaults

Adds missing config keys for automatic working-week advancement and
normalizes the legacy meeting_auto_advance seed.
"""
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    IF EXISTS (
        SELECT 1
        FROM system_config
        WHERE [key] = 'meeting_auto_advance' AND value = 'every_monday'
    )
    BEGIN
        UPDATE system_config
        SET value = 'manual'
        WHERE [key] = 'meeting_auto_advance' AND value = 'every_monday'
    END
    """)
    op.execute("""
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE [key] = 'meeting_auto_advance')
    BEGIN
        INSERT INTO system_config ([key], value) VALUES ('meeting_auto_advance', 'manual')
    END
    """)
    op.execute("""
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE [key] = 'snapshot_advance_day')
    BEGIN
        INSERT INTO system_config ([key], value) VALUES ('snapshot_advance_day', 'monday')
    END
    """)
    op.execute("""
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE [key] = 'snapshot_advance_time')
    BEGIN
        INSERT INTO system_config ([key], value) VALUES ('snapshot_advance_time', '00:00')
    END
    """)


def downgrade() -> None:
    op.execute("DELETE FROM system_config WHERE [key] IN ('snapshot_advance_day', 'snapshot_advance_time')")
    op.execute("""
    UPDATE system_config
    SET value = 'every_monday'
    WHERE [key] = 'meeting_auto_advance' AND value = 'manual'
    """)
