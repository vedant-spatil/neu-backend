"""Case-workflow telemetry for the admin observability page (plan Part 1B).

Reads the JSON lines that app.services.telemetry writes, through CloudWatch Logs Insights. Needs
observability_log_group; without it the summary reports that observability is not configured. Results are cached for
60 seconds per process (a single API worker, as in services/cache.py).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_settings

log = logging.getLogger(__name__)

CACHE_TTL_S = 60.0
POLL_TIMEOUT_S = 10.0
WINDOWS_H = (24, 168)
_cache: dict[int, tuple[float, dict[str, Any]]] = {}

# Logs Insights queries, one per panel. Each returns a small result set.
QUERIES = {
    "runs": 'filter event = "case_run" | stats count(*) as runs, pct(duration_ms, 95) as p95',
    "case_errors": 'filter event = "case_run" and status = "error" | stats count(*) as errors',
    "nodes": 'filter event = "node" | stats count(*) as count, pct(duration_ms, 95) as p95 by node '
             '| sort count desc | limit 50',
    "node_errors": 'filter event = "node" and status = "error" | stats count(*) as errors by node | limit 50',
    "error_types": 'filter status = "error" | stats count(*) as count by error_type | sort count desc | limit 20',
    "recent": 'filter event = "case_run" | sort @timestamp desc | limit 25 '
              '| fields @timestamp, case_id, attempt, status, duration_ms',
}


class ObservabilityError(Exception):
    """The logs could not be read. The message is generic; the cause is logged without its text."""


def rows_to_dicts(results: list[list[dict[str, str]]]) -> list[dict[str, str]]:
    """Logs Insights returns each row as a list of {field, value} cells. This gives one dict per row."""
    return [{cell["field"]: cell["value"] for cell in row} for row in results]


def to_number(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _count(value: str | None) -> int:
    number = to_number(value)
    return int(number) if number is not None else 0


def build_summary(results: dict[str, list[dict[str, str]]], hours: int) -> dict[str, Any]:
    """Shapes the query rows into the page's response. Pure, so it is tested without AWS."""
    runs = results.get("runs", [{}])[0] if results.get("runs") else {}
    errors = results.get("case_errors", [{}])[0] if results.get("case_errors") else {}
    node_errors = {r.get("node", ""): _count(r.get("errors")) for r in results.get("node_errors", [])}
    return {
        "enabled": True,
        "window_hours": hours,
        "case_runs": _count(runs.get("runs")),
        "errors": _count(errors.get("errors")),
        "p95_ms": to_number(runs.get("p95")),
        "nodes": [
            {
                "node": r.get("node", ""),
                "count": _count(r.get("count")),
                "p95_ms": to_number(r.get("p95")),
                "errors": node_errors.get(r.get("node", ""), 0),
            }
            for r in results.get("nodes", [])
        ],
        "error_types": [
            {"error_type": r.get("error_type") or "unknown", "count": _count(r.get("count"))}
            for r in results.get("error_types", [])
        ],
        "recent": [
            {
                "time": r.get("@timestamp", ""),
                "case_id": _count(r.get("case_id")) if r.get("case_id") else None,
                "attempt": _count(r.get("attempt")),
                "status": r.get("status", ""),
                "duration_ms": to_number(r.get("duration_ms")),
            }
            for r in results.get("recent", [])
        ],
    }


def _run_queries(client: Any, group: str, start: int, end: int) -> dict[str, list[dict[str, str]]]:
    """Starts every panel's query, then polls until each completes or the timeout passes."""
    pending = {
        name: client.start_query(logGroupName=group, startTime=start, endTime=end, queryString=q, limit=1000)["queryId"]
        for name, q in QUERIES.items()
    }
    done: dict[str, list[dict[str, str]]] = {}
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while pending:
        for name, query_id in list(pending.items()):
            res = client.get_query_results(queryId=query_id)
            if res["status"] == "Complete":
                done[name] = rows_to_dicts(res.get("results", []))
                del pending[name]
            elif res["status"] in ("Failed", "Cancelled", "Timeout", "Unknown"):
                raise ObservabilityError(f"query {name} ended with {res['status']}")
        if pending:
            if time.monotonic() > deadline:
                raise ObservabilityError("log queries did not finish in time")
            time.sleep(0.5)
    return done


def summary(hours: int) -> dict[str, Any]:
    settings = get_settings()
    if not settings.observability_log_group:
        return {"enabled": False}
    hours = hours if hours in WINDOWS_H else WINDOWS_H[0]
    now = time.time()
    cached = _cache.get(hours)
    if cached is not None and now - cached[0] < CACHE_TTL_S:
        return cached[1]
    region = settings.observability_region or settings.bedrock_region
    client = boto3.client("logs", region_name=region)
    try:
        raw = _run_queries(client, settings.observability_log_group, int(now - hours * 3600), int(now))
    except (BotoCoreError, ClientError, ObservabilityError) as exc:
        # Log the class only: the message can carry provider detail that does not belong in app logs.
        log.warning("observability query failed: %s", type(exc).__name__)
        raise ObservabilityError("could not read the logs") from exc
    result = build_summary(raw, hours)
    _cache[hours] = (now, result)
    return result
