"""Export per-transaction rule scores in the Multi-GNN row order, for comparison with model scores.

Writes one row per transaction in the day range:
  EdgeID      row position in formatted_transactions.csv (same stable sort by Timestamp as build_formatted)
  day         0-based day, same split arithmetic as IBM data_loading (timestamp // 86400 with the +10 s offset)
  rule_score  max risk_score of any alert that lists this transaction (0 if none)
  flagged     1 if rule_score >= threshold

Alert txn_ids are capped at 200 per alert, so flagged/rule_score are a lower bound on rule coverage.

Usage (from backend/):
  .venv/bin/python -m app.evaluation.export_rule_flags --csv docs/HI-Small_Trans.csv \
      --out docs/eval/rules_scores.csv --min-day 8 --max-day 17
"""

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from app.ingest.alerts import build_alerts, load_csv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-day", type=int, default=8, help="first 0-based day (IBM test starts at 8)")
    ap.add_argument("--max-day", type=int, default=17, help="last 0-based day inclusive (IBM test ends at 17)")
    ap.add_argument("--threshold", type=float, default=0.6)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)-5.5s] %(message)s")

    df = load_csv(args.csv)  # adds txn_id = 1-based row number
    logging.info(f"rows={len(df)} laundering={int(df['is_laundering'].sum())}")

    alerts = build_alerts(df)
    logging.info(f"alerts={len(alerts)}")

    n = len(df)
    rule_score = np.zeros(n)
    for a in alerts:
        if not a.txn_ids:
            continue
        idx = np.asarray(a.txn_ids, dtype=np.int64) - 1
        rule_score[idx] = np.maximum(rule_score[idx], a.risk_score)

    # Same arithmetic as build_formatted: seconds from first midnight + 10, stable sort by that key.
    secs = (df["ts"] - df["ts"].min().normalize()).dt.total_seconds().to_numpy() + 10
    order = np.argsort(secs, kind="stable")
    edge_id = np.empty(n, dtype=np.int64)
    edge_id[order] = np.arange(n)
    day = (secs // 86400).astype(np.int64)

    out = pd.DataFrame({
        "EdgeID": edge_id,
        "day": day,
        "rule_score": rule_score,
        "flagged": (rule_score >= args.threshold).astype(int),
        "y": df["is_laundering"].astype(int).to_numpy(),
    })
    out = out[(out["day"] >= args.min_day) & (out["day"] <= args.max_day)].sort_values("EdgeID")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    logging.info(f"days {args.min_day}-{args.max_day}: rows={len(out)} laundering={int(out.y.sum())} "
                 f"flagged={int(out.flagged.sum())} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
