"""Rebuild grouped alerts (one per account and rule) from the source CSV.

Keeps every alert referenced by a case, so cases.alert_id links never break.
Usage: python -m app.ingest.rebuild_alerts --csv docs/HI-Small_Trans.csv
Progress lines print as the run goes (elapsed seconds and peak memory).
"""

import argparse
import resource
import time

from sqlalchemy import text

from app.db.session import SessionLocal
from app.ingest.alerts import load_csv
from app.ingest.load import _write_alerts


def _log(stage: str, t0: float) -> None:
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"[rebuild] {stage} elapsed={time.perf_counter() - t0:.0f}s peak_rss={peak_mb:.0f}MB", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()
    t0 = time.perf_counter()
    print(f"[rebuild] start csv={args.csv}", flush=True)

    df = load_csv(args.csv)
    _log(f"csv loaded rows={len(df)}", t0)

    _write_alerts(df)
    _log("alerts written", t0)

    with SessionLocal() as db:
        kept = db.execute(text("SELECT count(*) FROM alerts")).scalar_one()
    _log(f"done alerts now={kept}", t0)
    print(f"alerts now={kept}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
