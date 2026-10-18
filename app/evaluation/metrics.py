"""Evaluation vs IBM HI-Small labels (plan 16.8). Offline, no LLM.

Run: python -m app.evaluation.metrics --csv docs/HI-Small_Trans.csv --patterns docs/HI-Small_Patterns.txt
Prints a metrics table as JSON. Alert txn ids are capped at 200 per alert (ingest/alerts.py), so
coverage numbers are a lower bound.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

import pandas as pd

from app.ingest.alerts import Alert, build_alerts, load_csv

KEY_COLS = ("ts", "from_account", "to_account", "amount_received")


def parse_patterns(path: str) -> dict[tuple, str]:
    """Map (ts, from, to, amount) of each laundering member line to its pattern type."""
    keys: dict[tuple, str] = {}
    current: str | None = None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith("BEGIN LAUNDERING ATTEMPT"):
                current = line.split(" - ", 1)[1].split(":")[0].strip()
            elif line.startswith("END LAUNDERING ATTEMPT"):
                current = None
            elif current and line:
                f = line.split(",")
                # Timestamp,From Bank,Account,To Bank,Account,Amount Received,...
                keys[(f[0], f[2], f[4], float(f[5]))] = current
    return keys


def tag_patterns(df: pd.DataFrame, keys: dict[tuple, str]) -> pd.DataFrame:
    ts_str = df["ts"].dt.strftime("%Y/%m/%d %H:%M")
    k = list(zip(ts_str, df["from_account"], df["to_account"], df["amount_received"].astype(float)))
    out = df.copy()
    out["pattern"] = [keys.get(x) for x in k]
    return out


def evaluate(df: pd.DataFrame, alerts: list[Alert], threshold: float = 0.6) -> dict:
    labelled = set(df.loc[df["is_laundering"].astype(bool), "txn_id"].astype(int))
    base_rate = len(labelled) / len(df)

    def summarise(subset: list[Alert]) -> dict:
        pos = [a for a in subset if a.is_laundering]
        covered = set()
        for a in subset:
            covered.update(i for i in a.txn_ids if i in labelled)
        return {
            "alerts": len(subset),
            "positive_alerts": len(pos),
            "precision": round(len(pos) / len(subset), 5) if subset else 0.0,
            "recall_txn_coverage": round(len(covered) / len(labelled), 5) if labelled else 0.0,
            "laundering_txns_covered": len(covered),
        }

    opened = [a for a in alerts if a.risk_score >= threshold]
    result = {
        "rows": int(len(df)),
        "laundering_txns": len(labelled),
        "base_rate": round(base_rate, 6),
        "random_alert_precision_baseline": round(base_rate, 6),
        "all_alerts": summarise(alerts),
        f"opened_cases_score_ge_{threshold}": summarise(opened),
        "by_rule": {},
    }
    for rule in sorted({r for a in alerts for r in a.rules}):
        result["by_rule"][rule] = summarise([a for a in alerts if rule in a.rules])
    return result


def pattern_coverage(df: pd.DataFrame, alerts: list[Alert]) -> dict[str, dict[str, float]]:
    """Share of each IBM pattern's transactions that appear in some alert (lower bound)."""
    if "pattern" not in df or df["pattern"].isna().all():
        return {}
    covered: set[int] = set()
    for a in alerts:
        covered.update(a.txn_ids)
    totals: Counter[str] = Counter()
    hits: Counter[str] = Counter()
    for tid, pat in zip(df.loc[df["pattern"].notna(), "txn_id"], df.loc[df["pattern"].notna(), "pattern"]):
        totals[pat] += 1
        if int(tid) in covered:
            hits[pat] += 1
    return {p: {"txns": totals[p], "covered": hits[p], "share": round(hits[p] / totals[p], 4)} for p in totals}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--patterns", required=True)
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--from-day", type=int, default=None, help="first day of held-out window (1-based, from first timestamp)")
    ap.add_argument("--to-day", type=int, default=None, help="last day of held-out window (inclusive)")
    args = ap.parse_args()

    df = load_csv(args.csv)
    keys = parse_patterns(args.patterns)
    df = tag_patterns(df, keys)
    alerts = build_alerts(df)  # alerts use the full history, as in production
    if args.from_day is not None:
        start = df["ts"].min().normalize()
        lo = start + pd.Timedelta(days=args.from_day - 1)
        hi = start + pd.Timedelta(days=args.to_day or args.from_day) + pd.Timedelta(days=1)
        # Report only on the window: transactions and alerts whose window starts inside it.
        df = df[(df["ts"] >= lo) & (df["ts"] < hi)]
        alerts = [a for a in alerts if lo <= a.ts < hi]
    report = evaluate(df, alerts, args.threshold)
    report["window"] = {"from_day": args.from_day, "to_day": args.to_day}
    report["pattern_coverage"] = pattern_coverage(df, alerts)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["evaluate", "parse_patterns", "tag_patterns", "pattern_coverage"]
