"""Enforce append-only case_events at the database level (plan 9).

Any UPDATE or DELETE on case_events raises an error, so the audit trail cannot be edited
by the app or by accident. TRUNCATE is revoked from PUBLIC as well.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_case_events_append_only"
down_revision: str | None = "8668079294f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION case_events_block_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'case_events is append-only: % is not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER case_events_no_update_delete
        BEFORE UPDATE OR DELETE ON case_events
        FOR EACH ROW EXECUTE FUNCTION case_events_block_change();
        """
    )
    op.execute(
        """
        CREATE TRIGGER case_events_no_truncate
        BEFORE TRUNCATE ON case_events
        FOR EACH STATEMENT EXECUTE FUNCTION case_events_block_change();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS case_events_no_truncate ON case_events")
    op.execute("DROP TRIGGER IF EXISTS case_events_no_update_delete ON case_events")
    op.execute("DROP FUNCTION IF EXISTS case_events_block_change()")
