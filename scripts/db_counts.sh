#!/usr/bin/env bash
# Row counts in Postgres (Supabase) and Neo4j (Aura) after a sync, to compare with the expected numbers. Run on the EC2 host.
# The counts run inside the API container, which already has the database settings from .env (APP_DATABASE_URL and
# APP_NEO4J_URI). This script does not read .env itself.
#
# Usage:   scripts/db_counts.sh
# Expected after the 1-day sample (HI-Small_sample.csv):  transactions 1114921, Neo4j nodes 105135, Neo4j edges 357565
# Expected after the full HI-Small load:                  transactions 5078345 (the Neo4j slice is smaller; see sync.py)

set -euo pipefail

COMPOSE="docker compose -f docker-compose.prod.yml"

$COMPOSE exec -T api python - <<'PY'
from sqlalchemy import text

from app.db.session import engine
from app.services.graph_store import driver

QUERIES = [
    ("transactions", "select count(*) from transactions"),
    ("parties", "select count(*) from parties"),
    ("alerts_rules", "select count(*) from alerts where source = 'rules'"),
    ("cases", "select count(*) from cases"),
    ("imports_busy", "select count(*) from imports where status in ('validating','loading','triaging')"),
]

print("--- Postgres (Supabase)")
with engine.connect() as conn:
    for label, sql in QUERIES:
        print(f"{label}\t{conn.execute(text(sql)).scalar()}")

print("--- Neo4j (Aura)")
with driver().session() as s:
    nodes = s.run("MATCH (a:Account) RETURN count(a) AS n").single()["n"]
    edges = s.run("MATCH ()-[r:TRANSFER]->() RETURN count(r) AS n").single()["n"]
print(f"accounts\t{nodes}")
print(f"edges\t{edges}")
PY
