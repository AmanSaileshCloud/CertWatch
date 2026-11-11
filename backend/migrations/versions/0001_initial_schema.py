"""Initial PostgreSQL schema: SSL domains + customers + uptime monitoring.

Revision ID: 0001
Revises:
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("contact_email", sa.Text),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "domains",
        sa.Column("domain", sa.Text, primary_key=True),
        sa.Column("host", sa.Text, nullable=False),
        sa.Column("port", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("not_after", TZ),
        sa.Column("days_remaining", sa.Integer),
        sa.Column("last_checked_at", TZ),
        sa.Column("last_error", sa.Text),
        sa.Column("last_alert_threshold", sa.Integer),
        sa.Column("alerts_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.Text, nullable=False, server_default="direct"),
        sa.Column("agent_id", sa.Text),
        sa.Column("stale_alert_sent", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("customer_id", sa.Integer,
                  sa.ForeignKey("customers.id", ondelete="SET NULL")),
    )

    op.create_table(
        "monitors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("customer_id", sa.Integer,
                  sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("type", sa.Text, nullable=False, server_default="http"),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("interval_seconds", sa.Integer, nullable=False),
        sa.Column("timeout_seconds", sa.Integer, nullable=False, server_default="10"),
        sa.Column("expected_status", sa.SmallInteger, nullable=False, server_default="200"),
        sa.Column("keyword", sa.Text),
        sa.Column("is_paused", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("state", sa.Text, nullable=False, server_default="unknown"),
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("first_failure_at", TZ),
        sa.Column("next_check_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("last_checked_at", TZ),
        sa.Column("last_status_code", sa.SmallInteger),
        sa.Column("last_response_ms", sa.Integer),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("interval_seconds IN (60, 300, 600)", name="ck_monitors_interval"),
        sa.CheckConstraint("timeout_seconds BETWEEN 1 AND 60", name="ck_monitors_timeout"),
        sa.CheckConstraint("expected_status BETWEEN 100 AND 599",
                           name="ck_monitors_expected_status"),
        sa.CheckConstraint("state IN ('unknown', 'up', 'down')", name="ck_monitors_state"),
        sa.CheckConstraint("type IN ('http')", name="ck_monitors_type"),
    )
    op.create_index("ix_monitors_due", "monitors", ["next_check_at"],
                    postgresql_where=sa.text("NOT is_paused"))
    op.create_index("ix_monitors_customer", "monitors", ["customer_id"])

    op.create_table(
        "check_results",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("monitor_id", sa.Integer,
                  sa.ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("checked_at", TZ, nullable=False),
        sa.Column("region", sa.Text, nullable=False, server_default="default"),
        sa.Column("is_up", sa.Boolean, nullable=False),
        sa.Column("status_code", sa.SmallInteger),
        sa.Column("response_ms", sa.Integer),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_check_results_monitor_time", "check_results",
                    ["monitor_id", "checked_at"])
    op.create_index("ix_check_results_time_brin", "check_results", ["checked_at"],
                    postgresql_using="brin")

    op.create_table(
        "check_results_hourly",
        sa.Column("monitor_id", sa.Integer,
                  sa.ForeignKey("monitors.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("hour_start", TZ, primary_key=True),
        sa.Column("total_checks", sa.Integer, nullable=False),
        sa.Column("up_checks", sa.Integer, nullable=False),
        sa.Column("avg_response_ms", sa.Float),
        sa.Column("min_response_ms", sa.Integer),
        sa.Column("max_response_ms", sa.Integer),
    )
    op.create_index("ix_check_results_hourly_hour", "check_results_hourly", ["hour_start"])

    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("monitor_id", sa.Integer,
                  sa.ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("resolved_at", TZ),
        sa.Column("duration_seconds", sa.Integer),
        sa.Column("reason", sa.Text),
        sa.Column("down_alert_sent_at", TZ),
        sa.Column("recovery_alert_sent_at", TZ),
    )
    op.create_index("ix_incidents_monitor_started", "incidents", ["monitor_id", "started_at"])
    op.create_index("ix_incidents_started", "incidents", ["started_at"])
    op.create_index("ux_incidents_one_open_per_monitor", "incidents", ["monitor_id"],
                    unique=True, postgresql_where=sa.text("resolved_at IS NULL"))

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("customer_id", sa.Integer,
                  sa.ForeignKey("customers.id", ondelete="CASCADE")),
        sa.Column("monitor_id", sa.Integer,
                  sa.ForeignKey("monitors.id", ondelete="CASCADE")),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("target", sa.Text, nullable=False),
        sa.Column("on_down", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("on_recovery", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("channel IN ('email', 'sns')", name="ck_alert_rules_channel"),
        sa.CheckConstraint("customer_id IS NULL OR monitor_id IS NULL",
                           name="ck_alert_rules_scope"),
    )


def downgrade() -> None:
    for table in ("alert_rules", "incidents", "check_results_hourly", "check_results",
                  "monitors", "domains", "customers"):
        op.drop_table(table)
