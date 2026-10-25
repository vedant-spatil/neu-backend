"""Case assignment and notifications (roles plan, groups B and C).

Revision ID: 0008_assignments_notifications
Revises: 0007_triage_scores
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_assignments_notifications"
down_revision: str | None = "0007_triage_scores"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("assignee_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))
    op.create_index("ix_cases_assignee_id", "cases", ["assignee_id"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("message", sa.String(300), nullable=False),
        sa.Column("link", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_cases_assignee_id", table_name="cases")
    op.drop_column("cases", "assignee_id")
