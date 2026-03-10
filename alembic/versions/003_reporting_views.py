"""003 reporting views — Power BI star schema

SSOT: ERP_Integrated_SSOT_v2.0 MiniPatch 1-12b-fix2
Section: §11 Reporting

Creates all reporting views via scripts/create_views.py SQL definitions.
Downgrade drops all views.
"""
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Import view SQL from the canonical source
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from scripts.create_views import VIEWS

    for sql in VIEWS:
        op.execute(sql)


def downgrade() -> None:
    from scripts.create_views import get_view_names

    for name in reversed(get_view_names()):
        op.execute(f"DROP VIEW IF EXISTS dbo.{name};")
