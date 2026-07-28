"""Asset snapshots and customer alerts.

Revision ID: 20260728_04
Revises: 20260728_03
"""

from alembic import op
from core.database import Base
import core.models  # noqa: F401

revision = "20260728_04"
down_revision = "20260728_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in ("asset_snapshots", "customer_alerts"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("customer_alerts", "asset_snapshots"):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
