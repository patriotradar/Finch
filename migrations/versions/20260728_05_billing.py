"""Billing events and invoices.

Revision ID: 20260728_05
Revises: 20260728_04
"""

from alembic import op
from core.database import Base
import core.models  # noqa: F401

revision = "20260728_05"
down_revision = "20260728_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in ("payment_events", "invoices"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("invoices", "payment_events"):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
