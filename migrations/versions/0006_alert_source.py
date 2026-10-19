"""Alerts carry their source: rule-based or model-based (plan.md section 22).

Existing rows are rule alerts, so source defaults to 'rules'. model_version is set only for
model alerts (the checkpoint name and threshold run).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_alert_source"
down_revision: str | None = "0005_drop_old_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column("source", sa.String(16), nullable=False, server_default="rules"),
    )
    op.add_column("alerts", sa.Column("model_version", sa.String(64), nullable=True))
    op.create_index(op.f("ix_alerts_source"), "alerts", ["source"], unique=False)


def downgrade() -> None:
    # Model alerts that already have a case are kept, so investigator work is not deleted.
    # After a downgrade those few rows lose their source label.
    op.execute("DELETE FROM alerts WHERE source = 'model' AND id NOT IN (SELECT alert_id FROM cases)")
    op.drop_index(op.f("ix_alerts_source"), table_name="alerts")
    op.drop_column("alerts", "model_version")
    op.drop_column("alerts", "source")
