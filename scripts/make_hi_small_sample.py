"""Writes the longest run of whole days from the start of HI-Small whose Neo4j graph slice fits the Aura Free limits.

Usage (from backend/): mainvenv/bin/python scripts/make_hi_small_sample.py --csv docs/HI-Small_Trans.csv --out docs/HI-Small_sample.csv

The Neo4j graph is the rule alerts' two-hop neighbourhood, limited to the top counterparties per account and hop
(app/services/sync.py, two_hop_slice). Its size is measured here by running the same rules and slice code on each
candidate window. The window's size is searched by bisection, so each candidate is run through the rules once.
"""

from __future__ import annotations

import argparse

import pandas as pd

from app.ingest.alerts import build_alerts
from app.ingest.mapping import auto_map, load_mapped, read_header
from app.services.sync import two_hop_slice

MAX_NODES = 190_000  # Aura Free allows 200,000, with a margin
MAX_RELS = 380_000  # Aura Free allows 400,000, with a margin
TS_FORMAT = "%Y/%m/%d %H:%M"


def graph_size(df: pd.DataFrame) -> tuple[int, int]:
    """Nodes and relationships the sync would load into Neo4j for this set of transfers."""
    flagged = {alert.account_id for alert in build_alerts(df)}
    edges, nodes = two_hop_slice(df, flagged)
    return len(nodes), len(edges)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    header = read_header(args.csv)
    mapping = auto_map(header)
    df = load_mapped(args.csv, mapping)
    start = df["ts"].min().normalize()
    total_days = int((df["ts"].max() - start).days) + 1
    print(f"rows={len(df)} start={start} days={total_days}", flush=True)

    best = None
    lo, hi = 1, total_days
    while lo <= hi:
        mid = (lo + hi) // 2
        cutoff = start + pd.Timedelta(days=mid)
        window = df[df["ts"] < cutoff]
        nodes, rels = graph_size(window)
        fits = nodes <= MAX_NODES and rels <= MAX_RELS
        print(f"days={mid} rows={len(window)} nodes={nodes} rels={rels} fits={fits}", flush=True)
        if fits:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        raise SystemExit("not even one day fits the Neo4j limits")

    cutoff = start + pd.Timedelta(days=best)
    raw = pd.read_csv(args.csv, dtype=str, keep_default_na=False)
    ts = pd.to_datetime(raw[mapping["timestamp"]], format=TS_FORMAT)
    sample = raw[ts < cutoff]
    sample.to_csv(args.out, index=False)
    print(f"wrote {len(sample)} rows for the first {best} day(s) to {args.out}", flush=True)


if __name__ == "__main__":
    main()
