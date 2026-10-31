"""Start, run and resume case workflows. The graph runs in a background thread; the API returns at once.

Each run is one LangGraph thread. The first run uses the case ID as its thread (as before). A rerun after a failure
uses a new thread, "<case_id>:<attempt>", so the failed checkpoint is not reused.
"""

import logging
from typing import Any

from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Alert, Case
from app.orchestration.graph import get_graph
from app.services.audit import write_event
from app.services.cache import invalidate
from app.services.telemetry import timed

log = logging.getLogger(__name__)

# Hard stop on graph steps per run. The workflow is a DAG (no cycles), so a normal run
# takes about 9 steps. A higher count means a bug, so LangGraph raises instead of looping.
RECURSION_LIMIT = 20


def thread_id(case_id: int, attempt: int = 0) -> str:
    return str(case_id) if attempt == 0 else f"{case_id}:{attempt}"


def _attempt(case_id: int) -> int:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        case = db.get(Case, case_id)
        return int((case.state or {}).get("attempt", 0)) if case is not None else 0


def active_case_for(db: Session, account_id: str) -> Case | None:
    """The account's one active case, or None. Superseded duplicates are not returned."""
    return db.execute(
        select(Case).where(Case.account_id == account_id, Case.status != "superseded")
    ).scalars().first()


def start_case(db: Session, alert: Alert) -> Case:
    case = Case(alert_id=alert.id, account_id=alert.account_id, status="open",
                risk_score=alert.risk_score, state={})
    db.add(case)
    db.commit()
    write_event(db, case.id, "ingest_alert", "start", {"alert_id": alert.id})
    invalidate()
    return case


def run_case(case_id: int, alert_id: int, account_id: str, alert_ts: str, rules: list[str], risk: float,
             txn_ids: list[int], attempt: int = 0) -> None:
    """Background task entry point. Errors are logged and recorded as a failed case."""
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id(case_id, attempt)}, "recursion_limit": RECURSION_LIMIT}
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
        with timed("case_run", case_id=case_id, attempt=attempt, mode="start"):
            graph.invoke(initial, config)
    except Exception as exc:
        log.exception("case %s run failed", case_id)
        _mark_failed(case_id, str(exc))
    finally:
        invalidate()


def resume_case(case_id: int, decision: dict[str, Any]) -> None:
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id(case_id, _attempt(case_id))},
              "recursion_limit": RECURSION_LIMIT}
    try:
        with timed("case_run", case_id=case_id, mode="resume"):
            graph.invoke(Command(resume=decision), config)
    finally:
        invalidate()


def _mark_failed(case_id: int, error: str) -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        case = db.get(Case, case_id)
        if case is not None and case.status not in ("approved", "closed"):
            case.status = "failed"
            db.commit()
        write_event(db, case_id, "graph", "error", {"error": error[:500]})
    invalidate()
