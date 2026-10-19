"""Projects, project members and accounts, and QA evaluations (plan: projects).

Creates only the new tables. LangGraph checkpoint tables are managed by the checkpointer
(PostgresSaver.setup) and must not appear in this migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_projects_and_evaluations"
down_revision: str | None = "bbfdbc49e6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("auto_validate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])
    op.create_table(
        "project_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.UniqueConstraint("project_id", "account_id", name="uq_project_account"),
    )
    op.create_index("ix_project_accounts_project_id", "project_accounts", ["project_id"])
    op.create_table(
        "evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("qa_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("source", sa.String(8), nullable=False, server_default="qa"),
        sa.Column("status", sa.String(16), nullable=False, server_default="submitted"),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_evaluations_project_id", "evaluations", ["project_id"])
    op.create_index("ix_evaluations_account_id", "evaluations", ["account_id"])
    op.create_index("ix_evaluations_qa_user_id", "evaluations", ["qa_user_id"])


def downgrade() -> None:
    op.drop_table("evaluations")
    op.drop_table("project_accounts")
    op.drop_table("project_members")
    op.drop_table("projects")
