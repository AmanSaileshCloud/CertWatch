"""PostgreSQL table definitions (SQLAlchemy Core).

The single source of truth for column names used by the Postgres adapters.
Schema changes ship as Alembic migrations in ``backend/migrations`` — keep the two
in step.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)

from ...core.uptime_models import (
    ALLOWED_INTERVAL_SECONDS,
    DEFAULT_REGION,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    AlertChannel,
    MonitorState,
    MonitorType,
)


def _in_list(column: str, values) -> str:
    quoted = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in values)
    return f"{column} IN ({quoted})"


def _tz() -> DateTime:
    return DateTime(timezone=True)


metadata = MetaData()

customers = Table(
    "customers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("contact_email", Text),
    Column("created_at", _tz(), nullable=False, server_default=func.now()),
)

monitor_groups = Table(
    "monitor_groups",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("customer_id", Integer, ForeignKey("customers.id", ondelete="CASCADE"),
           nullable=False),
    Column("name", Text, nullable=False),
    Column("created_at", _tz(), nullable=False, server_default=func.now()),
    UniqueConstraint("customer_id", "name", name="uq_monitor_groups_customer_name"),
)


def _group_column() -> Column:
    return Column("group_id", Integer, ForeignKey("monitor_groups.id", ondelete="SET NULL"))


domains = Table(
    "domains",
    metadata,
    Column("domain", Text, primary_key=True),
    Column("host", Text, nullable=False),
    Column("port", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", _tz(), nullable=False),
    Column("not_after", _tz()),
    Column("days_remaining", Integer),
    Column("last_checked_at", _tz()),
    Column("last_error", Text),
    Column("last_alert_threshold", Integer),
    Column("alerts_enabled", Boolean, nullable=False, server_default=text("true")),
    Column("source", Text, nullable=False, server_default="direct"),
    Column("agent_id", Text),
    Column("stale_alert_sent", Boolean, nullable=False, server_default=text("false")),
    Column("customer_id", Integer, ForeignKey("customers.id", ondelete="SET NULL")),
    _group_column(),
    Index("ix_domains_group", "group_id"),
)

monitors = Table(
    "monitors",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("customer_id", Integer, ForeignKey("customers.id", ondelete="SET NULL")),
    Column("name", Text, nullable=False),
    Column("type", Text, nullable=False, server_default=MonitorType.HTTP.value),
    Column("url", Text, nullable=False),
    Column("interval_seconds", Integer, nullable=False),
    Column("timeout_seconds", Integer, nullable=False, server_default="10"),
    Column("expected_status", SmallInteger, nullable=False, server_default="200"),
    Column("keyword", Text),
    Column("is_paused", Boolean, nullable=False, server_default=text("false")),
    Column("state", Text, nullable=False, server_default=MonitorState.UNKNOWN.value),
    Column("consecutive_failures", Integer, nullable=False, server_default="0"),
    Column("consecutive_successes", Integer, nullable=False, server_default="0"),
    Column("first_failure_at", _tz()),
    Column("next_check_at", _tz(), nullable=False, server_default=func.now()),
    Column("last_checked_at", _tz()),
    Column("last_status_code", SmallInteger),
    Column("last_response_ms", Integer),
    Column("last_error", Text),
    Column("created_at", _tz(), nullable=False, server_default=func.now()),
    Column("updated_at", _tz(), nullable=False, server_default=func.now()),
    _group_column(),
    CheckConstraint(_in_list("interval_seconds", ALLOWED_INTERVAL_SECONDS),
                    name="ck_monitors_interval"),
    CheckConstraint(
        f"timeout_seconds BETWEEN {MIN_TIMEOUT_SECONDS} AND {MAX_TIMEOUT_SECONDS}",
        name="ck_monitors_timeout",
    ),
    CheckConstraint("expected_status BETWEEN 100 AND 599", name="ck_monitors_expected_status"),
    CheckConstraint(_in_list("state", [s.value for s in MonitorState]), name="ck_monitors_state"),
    CheckConstraint(_in_list("type", [t.value for t in MonitorType]), name="ck_monitors_type"),
    Index("ix_monitors_due", "next_check_at", postgresql_where=text("NOT is_paused")),
    Index("ix_monitors_customer", "customer_id"),
    Index("ix_monitors_group", "group_id"),
)

check_results = Table(
    "check_results",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("monitor_id", Integer, ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False),
    Column("checked_at", _tz(), nullable=False),
    Column("region", Text, nullable=False, server_default=DEFAULT_REGION),
    Column("is_up", Boolean, nullable=False),
    Column("status_code", SmallInteger),
    Column("response_ms", Integer),
    Column("error", Text),
    Index("ix_check_results_monitor_time", "monitor_id", "checked_at"),
    Index("ix_check_results_time_brin", "checked_at", postgresql_using="brin"),
)

check_results_hourly = Table(
    "check_results_hourly",
    metadata,
    Column("monitor_id", Integer, ForeignKey("monitors.id", ondelete="CASCADE"),
           primary_key=True),
    Column("hour_start", _tz(), primary_key=True),
    Column("total_checks", Integer, nullable=False),
    Column("up_checks", Integer, nullable=False),
    Column("avg_response_ms", Float),
    Column("min_response_ms", Integer),
    Column("max_response_ms", Integer),
    Index("ix_check_results_hourly_hour", "hour_start"),
)

incidents = Table(
    "incidents",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("monitor_id", Integer, ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False),
    Column("started_at", _tz(), nullable=False),
    Column("resolved_at", _tz()),
    Column("duration_seconds", Integer),
    Column("reason", Text),
    Column("down_alert_sent_at", _tz()),
    Column("recovery_alert_sent_at", _tz()),
    Index("ix_incidents_monitor_started", "monitor_id", "started_at"),
    Index("ix_incidents_started", "started_at"),
    Index("ux_incidents_one_open_per_monitor", "monitor_id", unique=True,
          postgresql_where=text("resolved_at IS NULL")),
)

alert_rules = Table(
    "alert_rules",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("customer_id", Integer, ForeignKey("customers.id", ondelete="CASCADE")),
    Column("monitor_id", Integer, ForeignKey("monitors.id", ondelete="CASCADE")),
    Column("channel", Text, nullable=False),
    Column("target", Text, nullable=False),
    Column("on_down", Boolean, nullable=False, server_default=text("true")),
    Column("on_recovery", Boolean, nullable=False, server_default=text("true")),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", _tz(), nullable=False, server_default=func.now()),
    CheckConstraint(_in_list("channel", [c.value for c in AlertChannel]),
                    name="ck_alert_rules_channel"),
    CheckConstraint("customer_id IS NULL OR monitor_id IS NULL", name="ck_alert_rules_scope"),
)
