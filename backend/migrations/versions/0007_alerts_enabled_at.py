"""Remember when a monitor's alerts were last switched back on, so outages that
ended while alerts were off never trigger late DOWN / RECOVERED emails.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("monitors", sa.Column("alerts_enabled_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("monitors", "alerts_enabled_at")
