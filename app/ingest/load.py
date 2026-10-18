"""Load HI-Small into Postgres and Neo4j, generate synthetic parties, plant sanctioned names.

Usage (from backend/, venv active):
    python -m app.ingest.load --csv docs/HI-Small_Trans.csv [--limit-plant 2000]

Steps (plan 16.1, 16.3, 16.10):
  1. Alerts from rules (ingest/alerts.py) -> alerts table, txn ids in raw_json.
  2. Transactions -> transactions table (psycopg COPY, batches).
  3. Parties: one per account, Faker names, DOB 18-90, seed 42. ~1% planted from OpenSanctions.
  4. Neo4j: Account nodes (MERGE) and TRANSFER edges in UNWIND batches of 10,000.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
from collections.abc import Iterator
from datetime import date, timedelta

import httpx
import pandas as pd
from faker import Faker
from sqlalchemy import text

from app.db.models import Alert, Base, Party, Transaction
from app.db.session import SessionLocal, engine
from app.ingest.alerts import build_alerts, load_csv

SEED = 42
PLANT_RATE = 0.01
NEO4J_BATCH = 10_000
SANCTIONS_ENTITIES_URL = "https://data.opensanctions.org/datasets/latest/sanctions/entities.ftm.json"


def _planted_names(limit: int) -> list[str]:
    """First `limit` Person captions from the OpenSanctions sanctions dataset (CC BY-NC 4.0)."""
    names: list[str] = []
    with httpx.stream("GET", SANCTIONS_ENTITIES_URL, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            ent = json.loads(line)
            if ent.get("schema") != "Person":
                continue
            cap = ent.get("caption")
            if cap:
                names.append(cap)
            if len(names) >= limit:
                break
    return names


def build_parties(accounts: list[str], planted_pool: list[str]) -> list[Party]:
    fake = Faker("en_US")
    Faker.seed(SEED)
    rng = random.Random(SEED)
    today = date(2022, 9, 1)
    planted_accounts = set(rng.sample(accounts, k=int(len(accounts) * PLANT_RATE))) if planted_pool else set()
    parties: list[Party] = []
    for acct in accounts:
        if acct in planted_accounts:
            name = rng.choice(planted_pool)
            planted = True
        else:
            name = fake.name()
            planted = False
        age_days = rng.randint(18 * 365, 90 * 365)
        dob = (today - timedelta(days=age_days)).isoformat()
        parties.append(Party(account_id=acct, name=name, dob=dob, country="US", planted=planted))
    return parties


def _copy_transactions(df: pd.DataFrame, chunk: int = 250_000) -> None:
    """Stream COPY in chunks so the whole CSV is never held in memory (plan 16.10)."""
    out = df[["txn_id", "ts", "from_bank", "from_account", "to_bank", "to_account",
              "amount_received", "receiving_currency", "amount_paid",
              "payment_currency", "payment_format", "is_laundering"]]
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("TRUNCATE transactions RESTART IDENTITY")
        copy_sql = ("COPY transactions (id, ts, from_bank, from_account, to_bank, to_account, "
                    "amount_received, receiving_currency, amount_paid, payment_currency, "
                    "payment_format, is_laundering) FROM STDIN WITH (FORMAT csv, NULL '')")
        with cur.copy(copy_sql) as cp:
            for start in range(0, len(out), chunk):
                buf = io.StringIO()
                w = csv.writer(buf)
                for row in out.iloc[start : start + chunk].itertuples(index=False):
                    vals = list(row)
                    vals[0] = int(vals[0])
                    vals[1] = vals[1].isoformat(sep=" ")
                    vals[-1] = "true" if vals[-1] else "false"
                    w.writerow(vals)
                cp.write(buf.getvalue())
                print(f"copied {min(start + chunk, len(out))}/{len(out)}", flush=True)
        raw.commit()
    finally:
        raw.close()


def _batches(items: list, n: int) -> Iterator[list]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _load_neo4j(df: pd.DataFrame, accounts: list[str]) -> None:
    from app.services.graph_store import driver

    with driver().session() as s:
        s.run("CREATE CONSTRAINT account_id IF NOT EXISTS FOR (a:Account) REQUIRE a.id IS UNIQUE").consume()
        for batch in _batches([{"id": a} for a in accounts], NEO4J_BATCH):
            s.run("UNWIND $rows AS r MERGE (:Account {id: r.id})", rows=batch).consume()
    with driver().session() as s:
        for start in range(0, len(df), NEO4J_BATCH):
            part = df.iloc[start : start + NEO4J_BATCH]
            rows = [
                {"txn_id": int(r.txn_id), "src": r.from_account, "dst": r.to_account,
                 "amount": float(r.amount_paid), "currency": r.receiving_currency,
                 "ts": r.ts.isoformat(), "laundering": bool(r.is_laundering)}
                for r in part.itertuples(index=False)
            ]
            s.run(
                """
                UNWIND $rows AS r
                MATCH (a:Account {id: r.src}), (b:Account {id: r.dst})
                CREATE (a)-[:TRANSFER {txn_id: r.txn_id, amount: r.amount, currency: r.currency,
                                       ts: datetime(r.ts), laundering: r.laundering}]->(b)
                """,
                rows=rows,
            ).consume()
            print(f"neo4j edges {min(start + NEO4J_BATCH, len(df))}/{len(df)}", flush=True)


def _write_alerts(df: pd.DataFrame) -> None:
    """Replace unreferenced alerts with grouped ones in ONE transaction, so a crash leaves the
    old table intact. Alerts that already have cases are kept (cases.alert_id links)."""
    alerts = build_alerts(df)
    print(f"alerts={len(alerts)}", flush=True)
    rows = [
        {
            "account_id": a.account_id, "amount": a.amount, "ts": a.ts.to_pydatetime(),
            "source_label": "HI-Small", "risk_score": a.risk_score, "rules": a.rules,
            "is_laundering": a.is_laundering,
            "raw_json": {
                "txn_ids": a.txn_ids,
                "rule_counts": a.rule_counts,
                "last_ts": a.last_ts.isoformat() if a.last_ts is not None else None,
                "window_count": sum(a.rule_counts.values()),
            },
        }
        for a in alerts
    ]
    with engine.begin() as conn:
        # Rule alerts only: model alerts (source='model') are owned by app.pipeline.run.
        conn.execute(text("DELETE FROM alerts WHERE source = 'rules' AND id NOT IN (SELECT alert_id FROM cases)"))
        for start in range(0, len(rows), 20_000):
            conn.execute(Alert.__table__.insert(), rows[start : start + 20_000])
    print("alerts written", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--limit-plant", type=int, default=2000)
    ap.add_argument("--skip-neo4j", action="store_true")
    ap.add_argument("--skip-alerts", action="store_true", help="alerts already loaded; reload txns and parties only")
    args = ap.parse_args()

    df = load_csv(args.csv)
    print(f"rows={len(df)} laundering={int(df.is_laundering.sum())}", flush=True)

    Base.metadata.create_all(engine)

    if not args.skip_alerts:
        _write_alerts(df)

    _copy_transactions(df)
    print("transactions loaded", flush=True)

    accounts = sorted(set(df["from_account"]).union(df["to_account"]))
    planted = _planted_names(args.limit_plant)
    parties = build_parties(accounts, planted)
    with SessionLocal() as db:
        db.execute(text("TRUNCATE parties RESTART IDENTITY"))
        db.commit()
        for batch in _batches(parties, 50_000):
            db.add_all(batch)
            db.flush()
        db.commit()
    print(f"parties={len(parties)} planted={sum(p.planted for p in parties)}", flush=True)

    if not args.skip_neo4j:
        _load_neo4j(df, accounts)
        print("neo4j loaded", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
