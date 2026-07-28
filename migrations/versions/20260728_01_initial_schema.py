"""Initial durable Aegis schema.

Revision ID: 20260728_01
Revises:
"""

from alembic import op

from core.database import Base
import core.models  # noqa: F401


revision = "20260728_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())

