"""Neo4j access: fixed Cypher templates only (plan 13.1, section 5). No LLM-written Cypher."""

from typing import Any

from neo4j import GraphDatabase, Driver

from app.config import get_settings

_driver: Driver | None = None


def driver() -> Driver:
    global _driver
    if _driver is None:
        s = get_settings()
        _driver = GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))
    return _driver


# Window is in hours, anchored at the seed transaction timestamp.
FAN_OUT = """
MATCH (src:Account {id: $acct})-[t:TRANSFER]->(dst:Account)
WHERE t.ts >= datetime($start) AND t.ts <= datetime($end)
WITH src, collect(DISTINCT dst.id) AS targets, collect(t.txn_id) AS txns
WHERE size(targets) >= $min_targets
RETURN src.id AS source, targets, txns[0..50] AS txn_ids
LIMIT 50
"""

FAN_IN = """
MATCH (src:Account)-[t:TRANSFER]->(dst:Account {id: $acct})
WHERE t.ts >= datetime($start) AND t.ts <= datetime($end)
WITH dst, collect(DISTINCT src.id) AS sources, collect(t.txn_id) AS txns
WHERE size(sources) >= $min_sources
RETURN dst.id AS sink, sources, txns[0..50] AS txn_ids
LIMIT 50
"""

# Cycles of length 3 and 4 only (plan 16.7 allowed 5; capped at 4 for run time).
# Each hop keeps only the 50 largest edges in the time window, so the search stays bounded
# even for hub accounts. Variable-length *2..5 from a hub did not finish.
CYCLE = """
MATCH (a:Account {id: $acct})-[r1:TRANSFER]->(b:Account)
WHERE b.id <> a.id AND r1.ts >= datetime($start) AND r1.ts <= datetime($end)
WITH a, r1, b ORDER BY r1.amount DESC LIMIT 50
MATCH (b)-[r2:TRANSFER]->(c:Account)
WHERE c.id <> a.id AND c.id <> b.id AND r2.ts >= datetime($start) AND r2.ts <= datetime($end)
WITH a, r1, b, r2, c ORDER BY r2.amount DESC LIMIT 50
MATCH (c)-[r3:TRANSFER]->(a)
WHERE r3.ts >= datetime($start) AND r3.ts <= datetime($end)
RETURN [a.id, b.id, c.id] AS path, [r1.txn_id, r2.txn_id, r3.txn_id] AS txn_ids
LIMIT 50
UNION ALL
MATCH (a:Account {id: $acct})-[r1:TRANSFER]->(b:Account)
WHERE b.id <> a.id AND r1.ts >= datetime($start) AND r1.ts <= datetime($end)
WITH a, r1, b ORDER BY r1.amount DESC LIMIT 50
MATCH (b)-[r2:TRANSFER]->(c:Account)
WHERE c.id <> a.id AND c.id <> b.id AND r2.ts >= datetime($start) AND r2.ts <= datetime($end)
WITH a, r1, b, r2, c ORDER BY r2.amount DESC LIMIT 50
MATCH (c)-[r3:TRANSFER]->(d:Account)
WHERE d.id <> a.id AND d.id <> b.id AND d.id <> c.id AND r3.ts >= datetime($start) AND r3.ts <= datetime($end)
WITH a, r1, b, r2, c, r3, d ORDER BY r3.amount DESC LIMIT 50
MATCH (d)-[r4:TRANSFER]->(a)
WHERE r4.ts >= datetime($start) AND r4.ts <= datetime($end)
RETURN [a.id, b.id, c.id, d.id] AS path, [r1.txn_id, r2.txn_id, r3.txn_id, r4.txn_id] AS txn_ids
LIMIT 50
"""

# Neo4j aborts any single query after this many seconds, so a slow template fails the
# flow_tracer node (recorded in failures) instead of hanging the case.
QUERY_TIMEOUT_S = 10


def run_template(name: str, **params: Any) -> list[dict[str, Any]]:
    query = {"fan_out": FAN_OUT, "fan_in": FAN_IN, "cycle": CYCLE}[name]
    with driver().session() as session:
        return [dict(r) for r in session.run(query, timeout=QUERY_TIMEOUT_S, **params)]
