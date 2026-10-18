"""Threshold metrics for model scores. Pure pandas/sklearn, no torch."""

from __future__ import annotations

import pandas as pd
from sklearn.metrics import average_precision_score


def threshold_metrics(df: pd.DataFrame, threshold: float) -> dict:
    """df needs columns y (0/1) and score. Flagged means score >= threshold."""
    y = df["y"].to_numpy()
    score = df["score"].to_numpy()
    flagged = score >= threshold
    tp = int((flagged & (y == 1)).sum())
    fp = int((flagged & (y == 0)).sum())
    fn = int((~flagged & (y == 1)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "edges": int(len(df)),
        "laundering": int(y.sum()),
        "threshold": threshold,
        "flagged": int(flagged.sum()),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 5),
        "recall": round(recall, 5),
        "f1": round(f1, 5),
        "pr_auc": round(float(average_precision_score(y, score)), 5),
    }
