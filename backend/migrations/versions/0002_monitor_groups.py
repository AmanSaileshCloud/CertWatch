"""Monitor groups: Customer -> Group -> SSL domains + website monitors.

A group (e.g. a project like "Acme E-commerce") belongs to one customer. Each SSL
domain and each website monitor sits in at most one group. Deleting a group only
ungroups its members; deleting a customer removes its groups.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

MEMBER_TABLES = ("domains", "monitors")


def upgrade() -> None:
    op.create_table(
        "monitor_groups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("customer_id", sa.Integer,
                  sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("customer_id", "name", name="uq_monitor_groups_customer_name"),
    )
    for table in MEMBER_TABLES:
        op.add_column(table, sa.Column(
            "group_id", sa.Integer, sa.ForeignKey("monitor_groups.id", ondelete="SET NULL")))
        op.create_index(f"ix_{table}_group", table, ["group_id"])


def downgrade() -> None:
    for table in MEMBER_TABLES:
        op.drop_index(f"ix_{table}_group", table_name=table)
        op.drop_column(table, "group_id")
    op.drop_table("monitor_groups")
