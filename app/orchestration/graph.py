"""LangGraph workflow per case (plan section 4).

triage -> (low risk: close) | load_counterparties -> [flow_tracer | screening | typology]
       -> join -> report_writer -> await_human (interrupt) -> finalize
"""

from collections.abc import Callable
from typing import Any

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents import agents
from app.config import get_settings
from app.db.models import Case, Decision
from app.db.session import SessionLocal
from app.orchestration.state import CaseState
from app.services.audit import write_event


def _node(fn: Callable[[Session, dict[str, Any]], dict[str, Any]]) -> Callable[[CaseState], dict[str, Any]]:
    """Each node gets its own DB session, so parallel branches never share one."""

    def run(state: CaseState) -> dict[str, Any]:
        with SessionLocal() as db:
            return fn(db, dict(state))

    run.__name__ = fn.__name__
    return run


def _join(state: CaseState) -> dict[str, Any]:
    return {}


def _await_human(state: CaseState) -> dict[str, Any]:
    review = {
        "case_id": state["case_id"],
        "sar_draft": state.get("sar_draft"),
        "typologies": state.get("typologies", []),
        "screening_hits": state.get("screening_hits", []),
        "failures": state.get("failures", []),
    }
    with SessionLocal() as db:
        case = db.get(Case, state["case_id"])
        if case is not None:
            case.status = "awaiting_review"
            db.commit()
    decision = interrupt(review)  # resumed with Command(resume=decision_dict)
    return {"decision": decision, "status": "awaiting_review"}


def _finalize(state: CaseState) -> dict[str, Any]:
    decision = state.get("decision") or {}
    action = decision.get("action", "close")
    status = "approved" if action == "approve" else "closed"
    with SessionLocal() as db:
        case = db.get(Case, state["case_id"])
        if case is not None:
            case.status = status
            case.closed_at = func.now()
            db.add(
                Decision(
                    case_id=state["case_id"],
                    action=action,
                    reason=decision.get("reason", ""),
                    typologies=decision.get("typologies", []),
                    actor_user_id=int(decision["actor_user_id"]),
                )
            )
            db.commit()
        write_event(db, state["case_id"], "finalize", "decision", {"action": action})
    return {"status": status}


def _close_low(state: CaseState) -> dict[str, Any]:
    with SessionLocal() as db:
        case = db.get(Case, state["case_id"])
        if case is not None:
            case.status = "closed"
            db.commit()
    return {"status": "closed_low_risk"}


def _after_triage(state: CaseState) -> str:
    return "close_low" if state.get("status") == "closed_low_risk" else "load_counterparties"


def build_graph(checkpointer: Any):
    g = StateGraph(CaseState)
    g.add_node("triage", _node(agents.triage))
    g.add_node("close_low", _close_low)
    g.add_node("load_counterparties", _node(agents.load_counterparties))
    g.add_node("flow_tracer", _node(agents.flow_tracer))
    g.add_node("screening", _node(agents.screening))
    g.add_node("typology", _node(agents.typology))
    g.add_node("join", _join)
    g.add_node("report_writer", _node(agents.report_writer))
    g.add_node("await_human", _await_human)
    g.add_node("finalize", _finalize)

    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", _after_triage, {"close_low": "close_low", "load_counterparties": "load_counterparties"})
    g.add_edge("close_low", END)
    for branch in ("flow_tracer", "screening", "typology"):
        g.add_edge("load_counterparties", branch)
        g.add_edge(branch, "join")
    g.add_edge("join", "report_writer")
    g.add_edge("report_writer", "await_human")
    g.add_edge("await_human", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


_compiled: Any = None


def get_graph() -> Any:
    """Compiled graph with a long-lived Postgres checkpointer (pending cases survive restarts)."""
    global _compiled
    if _compiled is None:
        conn_str = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
        conn = psycopg.connect(conn_str, autocommit=True, prepare_threshold=0, row_factory=dict_row)
        saver = PostgresSaver(conn)
        saver.setup()
        _compiled = build_graph(saver)
    return _compiled


__all__ = ["build_graph", "get_graph", "Command"]
