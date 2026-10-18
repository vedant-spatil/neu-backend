"""Compare Multi-GNN scores with rule flags on the same transactions (IBM test days 8 to 17).

Inputs (from docs/eval/):
  model_val_scores.csv   EdgeID, score, y, seed-averaged   (threshold selection only)
  model_test_scores.csv  EdgeID, score, y, seed-averaged   (evaluation)
  rules_scores.csv       EdgeID, day, rule_score, flagged, y (rules, same test rows)

Threshold for the model is chosen on validation (max F1). It is applied unchanged to test.
Rules use their own flag (rule_score >= 0.6). Precision at K uses K = number of rule-flagged transactions.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve

from app.config import get_settings


def pr_metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"flagged": tp + fp, "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 5), "recall": round(recall, 5), "f1": round(f1, 5)}


def precision_at_k(y: np.ndarray, score: np.ndarray, k: int) -> float:
    order = np.argsort(-score, kind="stable")[:k]
    return round(float(y[order].mean()), 5)


def best_f1_threshold(y: np.ndarray, score: np.ndarray) -> float:
    p, r, t = precision_recall_curve(y, score)
    f1 = 2 * p[:-1] * r[:-1] / np.maximum(p[:-1] + r[:-1], 1e-12)
    return float(t[int(np.argmax(f1))])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="defaults to settings.eval_dir")
    ap.add_argument("--prefix", default="model", help="score files are <prefix>_val_scores.csv / <prefix>_test_scores.csv")
    args = ap.parse_args()
    d = Path(args.dir) if args.dir else get_settings().eval_dir

    val = pd.read_csv(d / f"{args.prefix}_val_scores.csv")
    test = pd.read_csv(d / f"{args.prefix}_test_scores.csv")
    rules = pd.read_csv(d / "rules_scores.csv")

    df = test.merge(rules[["EdgeID", "rule_score", "flagged", "y"]], on="EdgeID", suffixes=("", "_rules"))
    assert len(df) == len(test) == len(rules), "row sets differ"
    assert (df["y"] == df["y_rules"]).all(), "labels differ between model and rules files"
    y = df["y"].to_numpy()
    model_score = df["score"].to_numpy()
    rule_score = df["rule_score"].to_numpy()
    rule_flag = df["flagged"].to_numpy()
    k = int(rule_flag.sum())

    thr = best_f1_threshold(val["y"].to_numpy(), val["score"].to_numpy())
    model_flag = (model_score >= thr).astype(int)

    result = {
        "test_rows": int(len(df)),
        "test_laundering": int(y.sum()),
        "base_rate": round(float(y.mean()), 6),
        "k_rules_flagged": k,
        "model_threshold_from_val": round(thr, 6),
        "model": {
            **pr_metrics(y, model_flag),
            "pr_auc": round(float(average_precision_score(y, model_score)), 5),
            "precision_at_k": precision_at_k(y, model_score, k),
        },
        "rules": {
            **pr_metrics(y, rule_flag),
            "pr_auc": round(float(average_precision_score(y, rule_score)), 5),
            "precision_at_k": precision_at_k(y, rule_score, k),
        },
    }
    print(json.dumps(result, indent=2))
    (d / f"comparison_{args.prefix}_test.json").write_text(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
