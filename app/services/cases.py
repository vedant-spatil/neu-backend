"""Start and resume case runs. The graph runs in a background thread; the API returns at once."""

import logging
from typing import Any

from langgraph.types import Command
from sqlalchemy.orm import Session

from app.db.models import Alert, Case
from app.orchestration.graph import get_graph
from app.services.audit import write_event

log = logging.getLogger(__name__)

# Hard stop on graph steps per run. The workflow is a DAG (no cycles), so a normal run
# takes about 9 steps. A higher count means a bug, so LangGraph raises instead of looping.
RECURSION_LIMIT = 20


def start_case(db: Session, alert: Alert) -> Case:
    case = Case(alert_id=alert.id, status="open", risk_score=alert.risk_score, state={})
    db.add(case)
    db.commit()
    write_event(db, case.id, "ingest_alert", "start", {"alert_id": alert.id})
    return case


def run_case(case_id: int, alert_id: int, account_id: str, alert_ts: str, rules: list[str], risk: float, txn_ids: list[int]) -> None:
    """Background task entry point. Errors are logged and recorded as a failed case."""
    graph = get_graph()
    config = {"configurable": {"thread_id": str(case_id)}, "recursion_limit": RECURSION_LIMIT}
    initial: dict[str, Any] = {
        "case_id": case_id,
        "alert_id": alert_id,
        "account_id": account_id,
        "alert_ts": alert_ts,
        "alert_rules": rules,
        "risk_score": risk,
        "txn_ids": txn_ids,
        "failures": [],
        "status": "open",
    }
    try:
        graph.invoke(initial, config)
    except Exception as exc:  # noqa: BLE001
        log.exception("case %s run failed", case_id)
        _mark_failed(case_id, str(exc))


def resume_case(case_id: int, decision: dict[str, Any]) -> None:
    graph = get_graph()
    config = {"configurable": {"thread_id": str(case_id)}, "recursion_limit": RECURSION_LIMIT}
    graph.invoke(Command(resume=decision), config)


def _mark_failed(case_id: int, error: str) -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        case = db.get(Case, case_id)
        if case is not None and case.status not in ("approved", "closed"):
            case.status = "failed"
            db.commit()
        write_event(db, case_id, "graph", "error", {"error": error[:500]})
