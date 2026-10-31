from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import Text, cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session, require_role
from app.db.models import Alert, Case, CaseEvent, SarDraft, Transaction, User
from app.services import cases as case_service
from app.services.access import case_scope, get_visible_case
from app.services.audit import write_event
from app.services.cache import get_or_compute, invalidate
from app.services.dashboard import dashboard_payload, trends_payload
from app.services.notify import notify, notify_admins

router = APIRouter(prefix="/api", tags=["cases"])

_admin = require_role("admin")


class AlertOut(BaseModel):
    id: int
    account_id: str
    amount: float
    ts: str
    risk_score: float
    rules: list[str]


class CaseOut(BaseModel):
    id: int
    alert_id: int
    status: str
    risk_score: float
    assignee_id: int | None = None


def _case_out(c: Case) -> CaseOut:
    return CaseOut(id=c.id, alert_id=c.alert_id, status=c.status, risk_score=c.risk_score, assignee_id=c.assignee_id)


class DecisionIn(BaseModel):
    action: str = Field(pattern="^(approve|close)$")
    reason: str = Field(min_length=20, max_length=4000)
    typologies: list[str] = Field(default_factory=list)
    reviewed: bool

    model_config = {"extra": "forbid"}


class AssignIn(BaseModel):
    user_id: int


class AlertPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[AlertOut]


# Sort orders for the alert queue. The id tie-break keeps pages stable.
ALERT_SORTS = {
    "score_desc": (Alert.risk_score.desc(), Alert.id),
    "score_asc": (Alert.risk_score.asc(), Alert.id),
    "ts_desc": (Alert.ts.desc(), Alert.id),
    "ts_asc": (Alert.ts.asc(), Alert.id),
    "amount_desc": (Alert.amount.desc(), Alert.id),
}


@router.get("/alerts", response_model=AlertPage)
def list_alerts(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
    min_score: float = Query(0.0, ge=0.0, le=1.0),
    rule: str | None = Query(None, max_length=64),
    account: str | None = Query(None, max_length=64, description="account id prefix"),
    sort: str = Query("score_desc", pattern="^(score_desc|score_asc|ts_desc|ts_asc|amount_desc)$"),
    _: User = Depends(_admin),
    db: Session = Depends(db_session),
) -> AlertPage:
    """Paginated alert queue (admin only). One row per (account, rule). Default order: highest score first."""
    q = select(Alert).where(Alert.risk_score >= min_score)
    if rule:
        q = q.where(cast(Alert.rules, Text).like(f'%"{rule}"%'))
    if account:
        q = q.where(Alert.account_id.startswith(account, autoescape=True))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.order_by(*ALERT_SORTS[sort]).offset(offset).limit(limit)).scalars()
    items = [
        AlertOut(id=a.id, account_id=a.account_id, amount=float(a.amount), ts=a.ts.isoformat(),
                 risk_score=a.risk_score, rules=a.rules or [])
        for a in rows
    ]
    return AlertPage(total=total, offset=offset, limit=limit, items=items)


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(db_session)) -> dict[str, Any]:
    """Aggregates for the home screen. Admin sees the totals; QA sees their assigned cases only. Cached until the
    next change (services/cache.py)."""
    return get_or_compute(f"dashboard:{user.role}:{user.id}", lambda: dashboard_payload(db, user))


@router.get("/dashboard/trends")
def dashboard_trends(
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(current_user),
    db: Session = Depends(db_session),
) -> dict[str, Any]:
    """Daily series for the dashboard charts (see services/dashboard.py)."""
    return get_or_compute(f"trends:{user.role}:{user.id}:{days}", lambda: trends_payload(db, user, days))


@router.post("/alerts/{alert_id}/cases", response_model=CaseOut, status_code=status.HTTP_202_ACCEPTED)
def open_case(
    alert_id: int,
    background: BackgroundTasks,
    response: Response,
    _: User = Depends(_admin),
    db: Session = Depends(db_session),
) -> CaseOut:
    """Opens a case for an alert (admin only). An account has one active case: if it already has one, that case is
    returned (200) and nothing new starts. Otherwise a case is created and its workflow runs in the background (202)."""
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    existing = case_service.active_case_for(db, alert.account_id)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _case_out(existing)
    try:
        case = case_service.start_case(db, alert)
    except IntegrityError:
        # A concurrent request opened the account's case first; the unique index refused a second one.
        db.rollback()
        existing = case_service.active_case_for(db, alert.account_id)
        if existing is None:
            raise
        response.status_code = status.HTTP_200_OK
        return _case_out(existing)
    txn_ids = [int(i) for i in (alert.raw_json or {}).get("txn_ids", [])]
    background.add_task(
        case_service.run_case, case.id, alert.id, alert.account_id, alert.ts.isoformat(),
        list(alert.rules or []), alert.risk_score, txn_ids,
    )
    return _case_out(case)


class CasePage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[CaseOut]


@router.get("/cases", response_model=CasePage)
def list_cases(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
    status_filter: str | None = Query(None, alias="status", max_length=32),
    user: User = Depends(current_user),
    db: Session = Depends(db_session),
) -> CasePage:
    """Paginated case list, newest first. QA sees only the cases assigned to them. Optional status filter."""
    q = select(Case)
    scope = case_scope(user)
    if scope is not None:
        q = q.where(scope)
    if status_filter:
        q = q.where(Case.status == status_filter)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.order_by(Case.id.desc()).offset(offset).limit(limit)).scalars()
    return CasePage(total=total, offset=offset, limit=limit, items=[_case_out(c) for c in rows])


@router.get("/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)) -> CaseOut:
    return _case_out(get_visible_case(db, user, case_id))


@router.get("/cases/{case_id}/events")
def case_events(case_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    get_visible_case(db, user, case_id)
    rows = db.execute(select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.id)).scalars()
    return [
        {"node": e.node, "kind": e.kind, "payload": e.payload_json, "created_at": e.created_at.isoformat()}
        for e in rows
    ]


@router.get("/cases/{case_id}/sar")
def get_sar(case_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)) -> dict[str, Any]:
    get_visible_case(db, user, case_id)
    sar = db.execute(
        select(SarDraft).where(SarDraft.case_id == case_id).order_by(SarDraft.version.desc())
    ).scalars().first()
    if sar is None:
        raise HTTPException(status_code=404, detail="no SAR draft yet")
    return {"version": sar.version, "body": sar.body_redacted}


@router.get("/cases/{case_id}/graph")
def case_graph(
    case_id: int,
    max_edges: int = Query(50, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(db_session),
) -> dict[str, Any]:
    """Cytoscape elements for the case account. Parallel transfers between the same pair are
    merged into one edge (count, total). Only the top `max_edges` pairs by total are returned."""
    case = get_visible_case(db, user, case_id)
    alert = db.get(Alert, case.alert_id)
    txn_ids = [int(i) for i in (alert.raw_json or {}).get("txn_ids", [])]
    rows = db.execute(select(Transaction).where(Transaction.id.in_(txn_ids))).scalars() if txn_ids else []
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for t in rows:
        key = (t.from_account, t.to_account)
        p = pairs.setdefault(key, {"count": 0, "total": 0.0, "laundering": False})
        p["count"] += 1
        p["total"] += float(t.amount_paid)
        p["laundering"] = p["laundering"] or bool(t.is_laundering)
    top = sorted(pairs.items(), key=lambda kv: kv[1]["total"], reverse=True)[:max_edges]
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for (src, dst), p in top:
        for acct in (src, dst):
            nodes.setdefault(acct, {"data": {"id": acct, "label": acct, "focus": acct == alert.account_id}})
        edges.append({"data": {"id": f"{src}->{dst}", "source": src, "target": dst,
                               "label": f"{p['count']} txn / {p['total']:.0f}", "laundering": p["laundering"]}})
    return {
        "elements": {"nodes": list(nodes.values()), "edges": edges},
        "truncated": len(pairs) > max_edges,
        "pairs_total": len(pairs),
    }


@router.post("/cases/{case_id}/decision", response_model=CaseOut)
def decide(
    case_id: int,
    body: DecisionIn,
    user: User = Depends(current_user),
    db: Session = Depends(db_session),
) -> CaseOut:
    case = get_visible_case(db, user, case_id)
    if case.status != "awaiting_review":
        raise HTTPException(status_code=409, detail=f"case is {case.status}, not awaiting_review")
    if not body.reviewed:
        raise HTTPException(status_code=422, detail="reviewed must be true")
    case_service.resume_case(
        case_id,
        {"action": body.action, "reason": body.reason, "typologies": body.typologies, "actor_user_id": user.id},
    )
    db.refresh(case)
    if user.role == "qa":
        notify_admins(db, "review_completed", f"A reviewer completed a review of case {case.id}.",
                      link=f"/cases/{case.id}")
    return _case_out(case)


@router.post("/cases/{case_id}/assign", response_model=CaseOut)
def assign_case(
    case_id: int,
    body: AssignIn,
    _: User = Depends(_admin),
    db: Session = Depends(db_session),
) -> CaseOut:
    """Assigns a case to a QA user (admin only). The QA user gets a notification and sees the case."""
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    target = db.get(User, body.user_id)
    if target is None or target.role != "qa" or not target.is_active:
        raise HTTPException(status_code=422, detail="assignee must be an active QA user")
    case.assignee_id = target.id
    db.commit()
    write_event(db, case.id, "assignment", "assigned", {"user_id": target.id})
    notify(db, target.id, "case_assigned", f"Case {case.id} was assigned to you.", link=f"/cases/{case.id}")
    return _case_out(case)


@router.post("/cases/{case_id}/rerun", response_model=CaseOut, status_code=status.HTTP_202_ACCEPTED)
def rerun_case(
    case_id: int,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(db_session),
) -> CaseOut:
    """Reruns the workflow of a failed case, on a new LangGraph thread. The events of the failed run are kept."""
    case = get_visible_case(db, user, case_id)
    if case.status != "failed":
        raise HTTPException(status_code=409, detail=f"case is {case.status}; only failed cases can be rerun")
    alert = db.get(Alert, case.alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    attempt = int((case.state or {}).get("attempt", 0)) + 1
    case.state = {**(case.state or {}), "attempt": attempt}
    case.status = "open"
    db.commit()
    write_event(db, case.id, "workflow", "rerun", {"attempt": attempt, "by_user_id": user.id})
    invalidate()
    txn_ids = [int(i) for i in (alert.raw_json or {}).get("txn_ids", [])]
    background.add_task(
        case_service.run_case, case.id, alert.id, alert.account_id, alert.ts.isoformat(),
        list(alert.rules or []), alert.risk_score, txn_ids, attempt,
    )
    return _case_out(case)
