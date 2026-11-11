"""Worker heartbeats: when each background worker last finished a cycle, so
/health/ready (and the alarm watching it) notices a worker that has stopped.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("beat_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
