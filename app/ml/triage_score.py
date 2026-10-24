"""Score account-days with the trained triage model and store the scores for the app (plan.md section 24).

Reason codes are the top features by XGBoost's own per-row contributions (pred_contribs, TreeSHAP).
That gives the same attributions as SHAP without an extra dependency.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.models import TriageScore
from app.ml.triage_features import FEATURES_FILE, MANIFEST_FILE
from app.ml.triage_train import predict

REASONS = 3
CHUNK = 500_000
SCORES_FILE = "scores.parquet"
DB_BATCH = 5_000


def reason_codes(booster: xgb.Booster, df: pd.DataFrame, features: list[str], top_n: int = REASONS) -> list[list[str]]:
    dm = xgb.DMatrix(df[features].to_numpy(np.float32), feature_names=features)
    contrib = booster.predict(dm, pred_contribs=True, iteration_range=(0, booster.best_iteration + 1))[:, :-1]
    order = np.argsort(-contrib, axis=1)[:, :top_n]
    picked = np.take_along_axis(contrib, order, axis=1)
    names = np.asarray(features)
    return [[str(names[j]) for j, v in zip(row_idx, row_val) if v > 0] for row_idx, row_val in zip(order, picked)]


def score_frame(booster: xgb.Booster, frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    parts = []
    for start in range(0, len(frame), CHUNK):
        part = frame.iloc[start:start + CHUNK]
        reasons = reason_codes(booster, part, features)
        parts.append(pd.DataFrame({
            "acct": part["acct"].to_numpy(),
            "t_date": pd.to_datetime(part["t_date"]).to_numpy(),
            "score": predict(booster, part, features),
            "reasons": [json.dumps(r) for r in reasons],
        }))
    return pd.concat(parts, ignore_index=True)


def write_triage_scores(engine: Engine, scores: pd.DataFrame, model_version: str) -> int:
    """Replaces the rows for this model_version. Returns the number of rows inserted."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM triage_scores WHERE model_version = :mv"), {"mv": model_version})
        for start in range(0, len(scores), DB_BATCH):
            part = scores.iloc[start:start + DB_BATCH]
            conn.execute(TriageScore.__table__.insert(), [
                {"account_id": str(a), "score_date": pd.Timestamp(d).date(), "score": float(s),
                 "reason_codes": json.loads(r), "model_version": model_version}
                for a, d, s, r in zip(part["acct"], part["t_date"], part["score"], part["reasons"])
            ])
    return len(scores)


def run_score(features_dir: Path, run_dir: Path, model_version: str, engine: Engine | None = None) -> dict[str, Any]:
    manifest = json.loads((features_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
    features: list[str] = manifest["features"]
    booster = xgb.Booster()
    booster.load_model(str(run_dir / "model.json"))
    frame = pd.read_parquet(features_dir / FEATURES_FILE, columns=["acct", "t_date", *features])
    scores = score_frame(booster, frame, features)
    scores.to_parquet(run_dir / SCORES_FILE, index=False)
    written = write_triage_scores(engine, scores, model_version) if engine is not None else 0
    return {"rows": int(len(scores)), "db_rows": written, "model_version": model_version}
