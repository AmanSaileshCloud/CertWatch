"""Record why an incident closed: recovered, paused or edited.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("close_reason", sa.Text))
    op.execute("UPDATE incidents SET close_reason = 'recovered' WHERE resolved_at IS NOT NULL")
    op.create_check_constraint(
        "ck_incidents_close_reason", "incidents",
        "close_reason IS NULL OR close_reason IN ('recovered', 'paused', 'edited')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_incidents_close_reason", "incidents")
    op.drop_column("incidents", "close_reason")
