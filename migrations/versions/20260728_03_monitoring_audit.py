"""Monitoring request audit trail.

Revision ID: 20260728_03
Revises: 20260728_02
"""

from alembic import op
from core.database import Base
import core.models  # noqa: F401

revision = "20260728_03"
down_revision = "20260728_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["monitoring_requests"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.tables["monitoring_requests"].drop(bind=op.get_bind(), checkfirst=True)
