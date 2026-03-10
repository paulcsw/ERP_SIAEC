"""Smoke test: Alembic migration 001 upgrade → downgrade → re-upgrade.

Requires a live MSSQL instance. Skipped when DATABASE_URL is not set.
Run: DATABASE_URL=mssql+aioodbc://... pytest tests/test_migration.py -v
"""
import os

import pytest

requires_db = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set — skip DB migration smoke test",
)


@pytest.fixture()
def alembic_config():
    from alembic.config import Config

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic", "alembic.ini"))
    return cfg


@requires_db
def test_upgrade_head(alembic_config):
    from alembic import command

    command.upgrade(alembic_config, "head")


@requires_db
def test_downgrade_base(alembic_config):
    from alembic import command

    command.downgrade(alembic_config, "base")


@requires_db
def test_re_upgrade_head(alembic_config):
    from alembic import command

    command.upgrade(alembic_config, "head")


def test_model_table_count():
    """Verify all 17 migration-001 tables are registered in ORM metadata."""
    from app.models import Base

    tables = Base.metadata.tables
    assert len(tables) >= 17, f"Expected >=17 tables, got {len(tables)}: {sorted(tables.keys())}"


def test_model_tables_match_migration():
    """Verify key tables from migration 001 exist in ORM metadata."""
    from app.models import Base

    expected = {
        "users", "roles", "user_roles",
        "aircraft", "work_packages", "shop_streams",
        "ot_requests", "ot_approvals",
        "audit_logs", "system_config",
        # Future tables
        "shift_templates", "shift_assignments", "attendance_events",
        "daily_assignments", "worklog_blocks",
        "time_ledger_daily", "ledger_allocations_daily",
    }
    actual = set(Base.metadata.tables.keys())
    missing = expected - actual
    assert not missing, f"Missing tables in ORM metadata: {missing}"
