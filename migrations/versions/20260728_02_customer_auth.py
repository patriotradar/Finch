"""Persistent customer verification and sessions.

Revision ID: 20260728_02
Revises: 20260728_01
"""

from alembic import op

from core.database import Base
import core.models  # noqa: F401


revision = "20260728_02"
down_revision = "20260728_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in ("account_tokens", "customer_sessions"):
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in ("customer_sessions", "account_tokens"):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
