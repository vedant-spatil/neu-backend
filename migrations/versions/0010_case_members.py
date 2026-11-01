"""Cases hold several alerts and accounts: case_alerts and case_accounts (plan: case grouping, step 1).

An alert, or an account, is in at most one active case. `active` is false when the case is superseded. A partial
unique index cannot check the case's status, so the flag is kept on each link row and maintained by the app.

Existing cases are backfilled: each keeps its one alert and its one account, and is active unless it is superseded.

Revision ID: 0010_case_members
Revises: 0009_avatar_case_account
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_case_members"
down_revision: str | None = "0009_avatar_case_account"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("alert_id", sa.Integer(), sa.ForeignKey("alerts.id"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_case_alerts_case_id", "case_alerts", ["case_id"])
    op.create_index("ix_case_alerts_alert_id", "case_alerts", ["alert_id"])

    op.create_table(
        "case_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="subject"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_case_accounts_case_id", "case_accounts", ["case_id"])
    op.create_index("ix_case_accounts_account_id", "case_accounts", ["account_id"])

    # Backfill: one link per existing case, active unless the case is superseded.
    op.execute("INSERT INTO case_alerts (case_id, alert_id, active) SELECT id, alert_id, status <> 'superseded' FROM cases")
    op.execute(
        "INSERT INTO case_accounts (case_id, account_id, role, active) "
        "SELECT id, account_id, 'subject', status <> 'superseded' FROM cases"
    )

    op.create_index("uq_case_alerts_alert_active", "case_alerts", ["alert_id"], unique=True,
                    postgresql_where=sa.text("active"))
    op.create_index("uq_case_accounts_account_active", "case_accounts", ["account_id"], unique=True,
                    postgresql_where=sa.text("active"))


def downgrade() -> None:
    op.drop_index("uq_case_accounts_account_active", table_name="case_accounts")
    op.drop_index("uq_case_alerts_alert_active", table_name="case_alerts")
    op.drop_index("ix_case_accounts_account_id", table_name="case_accounts")
    op.drop_index("ix_case_accounts_case_id", table_name="case_accounts")
    op.drop_table("case_accounts")
    op.drop_index("ix_case_alerts_alert_id", table_name="case_alerts")
    op.drop_index("ix_case_alerts_case_id", table_name="case_alerts")
    op.drop_table("case_alerts")
