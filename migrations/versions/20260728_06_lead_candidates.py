"""Durable sourced lead candidates.

Revision ID: 20260728_06
Revises: 20260728_05
"""

from alembic import op
from core.database import Base
import core.models  # noqa: F401

revision = "20260728_06"
down_revision = "20260728_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["lead_candidates"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.tables["lead_candidates"].drop(bind=op.get_bind(), checkfirst=True)
