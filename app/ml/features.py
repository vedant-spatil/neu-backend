"""Feature preparation for the IBM Multi-GNN model on HI-Small (plan.md section 22).

Pure pandas/numpy, no torch. Steps, in order:
  1. check_inputs     raw CSV exists and has rows; returns its own counts
  2. build_formatted  raw CSV -> formatted_transactions.csv (IBM column layout) + edge_map.csv
  3. validate         formatted file matches the raw counts, sorted by Timestamp
  4. write_manifest   IBM's automatic day split (same search as IBM data_loading.get_data)

edge_map.csv links each model EdgeID back to the raw transaction (raw_row, accounts, amount, time).
The alerts step reads it, so no model output needs the database to map edges.
"""

from __future__ import annotations

import itertools
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

SPLIT_PER = [0.6, 0.2, 0.2]  # same targets as IBM data_loading.get_data
FORMATTED = "formatted_transactions.csv"
EDGE_MAP = "edge_map.csv"


def check_inputs(raw_csv: str | Path) -> dict:
    """Reads the raw CSV and returns its own counts. Nothing is hardcoded: the formatted file is checked against these."""
    raw_csv = Path(raw_csv)
    if not raw_csv.exists():
        raise FileNotFoundError(f"missing raw CSV: {raw_csv}")
    df = pd.read_csv(raw_csv, usecols=["Is Laundering"])
    if len(df) == 0:
        raise ValueError(f"raw CSV has no rows: {raw_csv}")
    return {"rows": len(df), "laundering": int(df["Is Laundering"].sum())}


def build_formatted(raw_csv: str | Path, out_dir: str | Path) -> Path:
    """HI-Small raw CSV -> <out_dir>/formatted_transactions.csv and <out_dir>/edge_map.csv."""
    raw_csv, out_dir = Path(raw_csv), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(raw_csv)
    logging.info(f"raw rows={len(df)}")

    ts = pd.to_datetime(df["Timestamp"], format="%Y/%m/%d %H:%M")
    timestamp = (ts - ts.min().normalize()).dt.total_seconds() + 10  # same offset as format_kaggle_files.py
    timestamp = timestamp.astype(np.int64)

    n = len(df)
    from_key = df["From Bank"].astype(str) + "_" + df["Account"].astype(str)
    to_key = df["To Bank"].astype(str) + "_" + df["Account.1"].astype(str)
    node_codes, _ = pd.factorize(pd.concat([from_key, to_key], ignore_index=True))
    from_id, to_id = node_codes[:n], node_codes[n:]

    cur_codes, _ = pd.factorize(pd.concat([df["Payment Currency"], df["Receiving Currency"]], ignore_index=True))
    fmt_codes, _ = pd.factorize(df["Payment Format"])

    # Both tables use the same stable sort key, so row i of edge_map matches row i of the formatted file.
    out = pd.DataFrame({
        "from_id": from_id,
        "to_id": to_id,
        "Timestamp": timestamp,
        "Amount Sent": df["Amount Paid"],
        "Sent Currency": cur_codes[:n],
        "Amount Received": df["Amount Received"],
        "Received Currency": cur_codes[n:],
        "Payment Format": fmt_codes,
        "Is Laundering": df["Is Laundering"],
    })
    edge_map = pd.DataFrame({
        "raw_row": np.arange(n),
        "from_account": from_key.to_numpy(),
        "to_account": to_key.to_numpy(),
        "amount_paid": df["Amount Paid"].to_numpy(),
        "payment_format": df["Payment Format"].to_numpy(),
        "timestamp": df["Timestamp"].to_numpy(),
        "is_laundering": df["Is Laundering"].to_numpy(),
        "Timestamp": timestamp.to_numpy(),
    })
    order = edge_map["Timestamp"].to_numpy().argsort(kind="stable")
    out = out.iloc[order].reset_index(drop=True)
    out.insert(0, "EdgeID", np.arange(len(out)))
    edge_map = edge_map.iloc[order].drop(columns="Timestamp").reset_index(drop=True)
    edge_map.insert(0, "EdgeID", np.arange(len(edge_map)))

    path = out_dir / FORMATTED
    out.to_csv(path, index=False)
    edge_map.to_csv(out_dir / EDGE_MAP, index=False)
    logging.info(f"formatted rows={len(out)} laundering={int(out['Is Laundering'].sum())} "
                 f"nodes={int(max(out.from_id.max(), out.to_id.max()) + 1)} -> {path}")
    return path


def validate(formatted: str | Path, raw_stats: dict) -> dict:
    df = pd.read_csv(formatted, usecols=["from_id", "to_id", "Timestamp", "Is Laundering"])
    rows = len(df)
    laundering = int(df["Is Laundering"].sum())
    nodes = int(max(df["from_id"].max(), df["to_id"].max()) + 1)
    if rows != raw_stats["rows"]:
        raise ValueError(f"formatted rows {rows} != raw rows {raw_stats['rows']}")
    if laundering != raw_stats["laundering"]:
        raise ValueError(f"formatted laundering {laundering} != raw laundering {raw_stats['laundering']}")
    if not df["Timestamp"].is_monotonic_increasing:
        raise ValueError("formatted file is not sorted by Timestamp")
    return {"rows": rows, "laundering": laundering, "nodes": nodes,
            "timestamp_min": float(df["Timestamp"].min()), "timestamp_max": float(df["Timestamp"].max())}


def ibm_split(daily_totals: np.ndarray) -> tuple[int, int]:
    """Same search as IBM data_loading.get_data: returns (i, j) day indices that cut train/val/test."""
    d_ts = np.asarray(daily_totals, dtype=float)
    best, best_score = None, None
    for i, j in itertools.combinations(range(len(d_ts)), 2):
        if j < i:
            continue
        totals = [d_ts[:i].sum(), d_ts[i:j].sum(), d_ts[j:].sum()]
        total = np.sum(totals)
        props = [v / total for v in totals]
        score = max(abs(v - t) / t for v, t in zip(props, SPLIT_PER))
        if best_score is None or score < best_score:
            best, best_score = (i, j), score
    return best


def write_manifest(formatted: str | Path, out_path: str | Path) -> dict:
    df = pd.read_csv(formatted, usecols=["Timestamp", "Is Laundering"])
    ts = df["Timestamp"].to_numpy(dtype=np.float64)
    ts = ts - ts.min()  # IBM subtracts the minimum before taking days
    n_days = int(ts.max() / 86400 + 1)
    day = (ts // 86400).astype(np.int64)
    y = df["Is Laundering"].to_numpy()
    daily_totals = np.bincount(day, minlength=n_days)
    i, j = ibm_split(daily_totals)
    split_days = {"train": list(range(0, i)), "val": list(range(i, j)), "test": list(range(j, n_days))}
    stats = {}
    for name, days in split_days.items():
        mask = np.isin(day, days)
        stats[name] = {"days": days, "edges": int(mask.sum()), "laundering": int(y[mask].sum()),
                       "laundering_rate": float(y[mask].mean()) if mask.any() else 0.0}
    manifest = {"formatted": str(formatted), "n_days": n_days, "split_cut_days": [i, j],
                "splits": stats, "split_targets": SPLIT_PER}
    out_path = Path(out_path)
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
