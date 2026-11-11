"""App settings editable from the UI (alert recipients, email delivery), plus a
unique index so the same alert recipient can't be stored twice for one scope.

Secrets (the SES SMTP password) are stored encrypted with SETTINGS_ENCRYPTION_KEY,
which lives in the environment, never in the database.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_by", sa.Text),
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_alert_rules_scope_target ON alert_rules "
        "(coalesce(customer_id, 0), coalesce(monitor_id, 0), lower(target))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ux_alert_rules_scope_target")
    op.drop_table("app_settings")
