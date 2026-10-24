#!/usr/bin/env bash
# Row counts in Postgres and Neo4j after a sync, to compare with the expected numbers. Run on the EC2 host.
#
# Usage:   export NEO4J_PASSWORD=...   (the value from the host's .env; the script does not read .env)
#          scripts/db_counts.sh
# Expected after the sample (1/12 of HI-Small):  transactions 423195, parties 251902, Neo4j accounts 251902,
#                                                Neo4j edges 423195
# Expected after the full HI-Small load:         transactions 5078345, parties 515080, Neo4j edges 5078345
#                                                (the dev load also gave 81455 rule alerts; a sync may differ
#                                                because cases keep their alerts)

set -euo pipefail

echo "--- Postgres"
docker exec aml-prod-postgres-1 psql -U aml -d aml -tAc "
  select 'transactions', count(*) from transactions
  union all select 'parties', count(*) from parties
  union all select 'alerts_rules', count(*) from alerts where source = 'rules'
  union all select 'cases', count(*) from cases
  union all select 'imports_busy', count(*) from imports where status in ('validating','loading','triaging')
"

echo "--- Neo4j"
docker exec aml-prod-neo4j-1 cypher-shell -u neo4j -p "${NEO4J_PASSWORD:?export NEO4J_PASSWORD first}" \
  --format plain "MATCH (a:Account) RETURN 'accounts' AS what, count(a) AS n UNION ALL MATCH ()-[r:TRANSFER]->() RETURN 'edges' AS what, count(r) AS n;"
