"""XGBoost triage scores per account and day (plan.md section 24).

Rows are written by app/ml/triage_score.py, one set per model_version. The triage node reads them
only when TRIAGE_MODEL=xgb. Rule alerts and model alerts (source='model') are not touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_triage_scores"
down_revision: str | None = "0006_alert_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "triage_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("score_date", sa.Date(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
    )
    op.create_index("ix_triage_scores_account_date", "triage_scores", ["account_id", "score_date"])
    op.create_index(op.f("ix_triage_scores_model_version"), "triage_scores", ["model_version"])


def downgrade() -> None:
    op.drop_index(op.f("ix_triage_scores_model_version"), table_name="triage_scores")
    op.drop_index("ix_triage_scores_account_date", table_name="triage_scores")
    op.drop_table("triage_scores")
