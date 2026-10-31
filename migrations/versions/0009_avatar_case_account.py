"""Profile pictures on users, one active case per account.

Duplicate cases for one account are resolved here, before the unique index is added. The earliest case per account
stays active. Each later one is marked superseded, never deleted, since it may hold decisions and case events
(case_events is append-only). Each change writes one case event, so the audit trail shows it.

Revision ID: 0009_avatar_case_account
Revises: 0008_assignments_notifications
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_avatar_case_account"
down_revision: str | None = "0008_assignments_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar", sa.LargeBinary(), nullable=True))
    op.add_column("users", sa.Column("avatar_type", sa.String(32), nullable=True))

    op.add_column("cases", sa.Column("account_id", sa.String(64), nullable=True))
    op.execute("UPDATE cases SET account_id = alerts.account_id FROM alerts WHERE alerts.id = cases.alert_id")

    conn = op.get_bind()
    keep = dict(conn.execute(sa.text(
        "SELECT account_id, min(id) FROM cases GROUP BY account_id"
    )).all())
    duplicates = conn.execute(sa.text(
        "SELECT id, account_id FROM cases WHERE status <> 'superseded' ORDER BY id"
    )).all()
    for case_id, account_id in duplicates:
        kept = keep[account_id]
        if case_id == kept:
            continue
        conn.execute(sa.text("UPDATE cases SET status = 'superseded' WHERE id = :id"), {"id": case_id})
        conn.execute(
            sa.text("INSERT INTO case_events (case_id, node, kind, payload_json) "
                    "VALUES (:case_id, 'migration', 'superseded', CAST(:payload AS JSON))"),
            {"case_id": case_id, "payload": json.dumps({"kept_case_id": kept, "account_id": account_id})},
        )

    op.alter_column("cases", "account_id", existing_type=sa.String(64), nullable=False)
    op.create_index(
        "uq_cases_account_active", "cases", ["account_id"], unique=True,
        postgresql_where=sa.text("status <> 'superseded'"),
    )


def downgrade() -> None:
    op.drop_index("uq_cases_account_active", table_name="cases")
    op.drop_column("cases", "account_id")
    op.drop_column("users", "avatar_type")
    op.drop_column("users", "avatar")
