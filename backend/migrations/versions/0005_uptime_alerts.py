"""Uptime alerts: per-monitor alert switch, reminder interval, alert delay; reminder tracking.

NULL reminder/delay on a monitor means "use the global default"
(UPTIME_REMINDER_MINUTES / UPTIME_ALERT_DELAY_MINUTES). Incidents already resolved
are marked as alerted (see upgrade).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("monitors", sa.Column(
        "alerts_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")))
    op.add_column("monitors", sa.Column("reminder_minutes", sa.Integer))
    op.add_column("monitors", sa.Column("alert_delay_minutes", sa.Integer))
    op.create_check_constraint(
        "ck_monitors_reminder_minutes", "monitors",
        "reminder_minutes IS NULL OR reminder_minutes IN (0, 15, 30, 60, 120)")
    op.create_check_constraint(
        "ck_monitors_alert_delay_minutes", "monitors",
        "alert_delay_minutes IS NULL OR alert_delay_minutes IN (0, 5, 10, 15, 30)")
    op.add_column("incidents", sa.Column("last_reminder_sent_at", sa.DateTime(timezone=True)))
    op.add_column("incidents", sa.Column(
        "reminder_count", sa.Integer, nullable=False, server_default="0"))
    op.create_index("ix_incidents_resolved", "incidents", ["resolved_at"])
    # Incidents closed before alerts existed count as already alerted, so the first
    # deploy doesn't email every past blip; open incidents still alert.
    op.execute(
        "UPDATE incidents SET down_alert_sent_at = resolved_at, "
        "recovery_alert_sent_at = resolved_at WHERE resolved_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_incidents_resolved", table_name="incidents")
    op.drop_column("incidents", "reminder_count")
    op.drop_column("incidents", "last_reminder_sent_at")
    op.drop_constraint("ck_monitors_alert_delay_minutes", "monitors")
    op.drop_constraint("ck_monitors_reminder_minutes", "monitors")
    op.drop_column("monitors", "alert_delay_minutes")
    op.drop_column("monitors", "reminder_minutes")
    op.drop_column("monitors", "alerts_enabled")
