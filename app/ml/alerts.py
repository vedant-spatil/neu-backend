"""Model alerts: flagged edges -> rows in the `alerts` table with source='model' (plan.md section 22).

One alert per flagged edge. The account is the sender, which is what the rule alerts key on.
Pure row building is separate from the DB write so it can be tested without Postgres.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.models import Alert

SOURCE = "model"
MODEL_RULE = "MODEL"
DATASET_LABEL = "HI-Small"
TS_FORMAT = "%Y/%m/%d %H:%M"


def build_alert_rows(scores: pd.DataFrame, edge_map: pd.DataFrame, model_version: str,
                     threshold: float, split: str) -> list[dict]:
    """scores: EdgeID, y, score for one split. edge_map: EdgeID, from_account, to_account, amount_paid, timestamp."""
    flagged = scores[scores["score"] >= threshold]
    if flagged.empty:
        return []
    joined = flagged.merge(edge_map, on="EdgeID", how="left", validate="one_to_one")
    if joined["from_account"].isna().any():
        missing = joined.loc[joined["from_account"].isna(), "EdgeID"].head(5).tolist()
        raise ValueError(f"scored edges missing from edge_map (first few): {missing}")
    rows = []
    for rec in joined.itertuples(index=False):
        rows.append({
            "account_id": str(rec.from_account),
            "amount": float(rec.amount_paid),
            "ts": datetime.strptime(str(rec.timestamp), TS_FORMAT),
            "source_label": DATASET_LABEL,
            "risk_score": float(rec.score),
            "rules": [MODEL_RULE],
            "is_laundering": bool(rec.y == 1),
            "raw_json": {
                "edge_id": int(rec.EdgeID),
                "split": split,
                "to_account": str(rec.to_account),
                "raw_row": int(rec.raw_row),
                "score": float(rec.score),
                "threshold": threshold,
                "model_version": model_version,
            },
            "source": SOURCE,
            "model_version": model_version,
        })
    return rows


def write_model_alerts(engine: Engine, rows: list[dict], model_version: str) -> int:
    """Replaces the model alerts for this model_version. Model alerts with a case are kept (cases.alert_id FK).

    Rule alerts are never touched. Returns the number of rows inserted.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM alerts WHERE source = :source AND model_version = :mv "
            "AND id NOT IN (SELECT alert_id FROM cases WHERE alert_id IS NOT NULL)"
        ), {"source": SOURCE, "mv": model_version})
        for start in range(0, len(rows), 5_000):
            conn.execute(Alert.__table__.insert(), rows[start:start + 5_000])
    return len(rows)
