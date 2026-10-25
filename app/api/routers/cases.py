from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Date, Text, cast, func, select, text
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.config import get_settings
from app.db.models import Alert, Case, CaseEvent, Decision, SarDraft, Transaction, User
from app.services import cases as case_service

router = APIRouter(prefix="/api", tags=["cases"])


def _threshold() -> float:
    return get_settings().triage_threshold


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


class DecisionIn(BaseModel):
    action: str = Field(pattern="^(approve|close)$")
    reason: str = Field(min_length=20, max_length=4000)
    typologies: list[str] = Field(default_factory=list)
    reviewed: bool

    model_config = {"extra": "forbid"}


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
    _: User = Depends(current_user),
    db: Session = Depends(db_session),
) -> AlertPage:
    """Paginated alert queue. One row per (account, rule). Default order: highest score first."""
    q = select(Alert).where(Alert.risk_score >= min_score)
    if rule:
        q = q.where(cast(Alert.rules, Text).like(f'%"{rule}"%'))
    if account:
        q = q.where(Alert.account_id.startswith(account, autoescape=True))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(
        q.order_by(*ALERT_SORTS[sort]).offset(offset).limit(limit)
    ).scalars()
    items = [
        AlertOut(id=a.id, account_id=a.account_id, amount=float(a.amount), ts=a.ts.isoformat(),
                 risk_score=a.risk_score, rules=a.rules or [])
        for a in rows
    ]
    return AlertPage(total=total, offset=offset, limit=limit, items=items)


@router.get("/dashboard")
def dashboard(_: User = Depends(current_user), db: Session = Depends(db_session)) -> dict[str, Any]:
    """Aggregates for the home screen. Each query is one indexed or grouped scan."""
    case_status = dict(db.execute(select(Case.status, func.count()).group_by(Case.status)).all())
    alert_rules = {}
    for (rules,) in db.execute(select(Alert.rules).where(Alert.risk_score >= _threshold())).all():
        for r in rules or []:
            alert_rules[r] = alert_rules.get(r, 0) + 1
    top_accounts = [
        {"account_id": a, "alerts": n}
        for a, n in db.execute(
            select(Alert.account_id, func.count()).where(Alert.risk_score >= _threshold())
            .group_by(Alert.account_id).order_by(func.count().desc()).limit(10)
        ).all()
    ]
    buckets = dict(
        db.execute(
            select(func.floor(Alert.risk_score * 10), func.count()).group_by(text("1"))
        ).all()
    )
    decisions_by_action = dict(
        db.execute(select(Decision.action, func.count()).group_by(Decision.action)).all()
    )
    return {
        "cases_by_status": case_status,
        "decisions_by_action": decisions_by_action,
        "alerts_above_threshold_by_rule": alert_rules,
        "top_accounts_by_alerts": top_accounts,
        "score_histogram": {f"{int(k) / 10:.1f}": v for k, v in buckets.items()},
        "failed_cases": case_status.get("failed", 0),
    }


def merge_activity(cases: list[tuple[date, int]], decisions: list[tuple[date, str, int]]) -> list[dict[str, Any]]:
    """One row per day: cases opened, and decisions split into approved and closed. Days are sorted oldest first."""
    rows: dict[date, dict[str, Any]] = {}

    def row(day: date) -> dict[str, Any]:
        return rows.setdefault(day, {"cases_opened": 0, "approved": 0, "closed": 0})

    for day, n in cases:
        row(day)["cases_opened"] += n
    for day, action, n in decisions:
        row(day)["approved" if action == "approve" else "closed"] += n
    return [{"day": day.isoformat(), **values} for day, values in sorted(rows.items())]


@router.get("/dashboard/trends")
def dashboard_trends(
    days: int = Query(30, ge=1, le=365),
    _: User = Depends(current_user),
    db: Session = Depends(db_session),
) -> dict[str, Any]:
    """Daily series for the dashboard charts.

    alerts_by_day groups alerts by the day of their transactions (alerts.ts, the data's clock), counting the last
    `days` days of alerts. activity_by_day groups cases and decisions by when they were created (the app's clock),
    over the last `days` days."""
    threshold = _threshold()
    alert_day = cast(Alert.ts, Date)
    last = db.execute(select(func.max(alert_day)).where(Alert.risk_score >= threshold)).scalar()
    alerts: list[tuple[date, int]] = []
    if last is not None:
        start = last - timedelta(days=days - 1)
        alerts = [
            (d, n) for d, n in db.execute(
                select(alert_day, func.count())
                .where(Alert.risk_score >= threshold, alert_day >= start)
                .group_by(alert_day).order_by(alert_day)
            ).all()
        ]
    since = datetime.now(UTC) - timedelta(days=days)
    case_day = cast(Case.created_at, Date)
    decision_day = cast(Decision.created_at, Date)
    cases = db.execute(
        select(case_day, func.count()).where(Case.created_at >= since).group_by(case_day)
    ).all()
    decisions = db.execute(
        select(decision_day, Decision.action, func.count())
        .where(Decision.created_at >= since).group_by(decision_day, Decision.action)
    ).all()
    return {
        "alerts_by_day": [{"day": d.isoformat(), "alerts": n} for d, n in alerts],
        "activity_by_day": merge_activity(list(cases), list(decisions)),
    }


@router.post("/alerts/{alert_id}/cases", response_model=CaseOut, status_code=status.HTTP_202_ACCEPTED)
def open_case(
    alert_id: int,
    background: BackgroundTasks,
    _: User = Depends(current_user),
    db: Session = Depends(db_session),
) -> CaseOut:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    case = case_service.start_case(db, alert)
    txn_ids = [int(i) for i in (alert.raw_json or {}).get("txn_ids", [])]
    background.add_task(
        case_service.run_case, case.id, alert.id, alert.account_id, alert.ts.isoformat(),
        list(alert.rules or []), alert.risk_score, txn_ids,
    )
    return CaseOut(id=case.id, alert_id=alert.id, status=case.status, risk_score=case.risk_score)


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
    _: User = Depends(current_user),
    db: Session = Depends(db_session),
) -> CasePage:
    """Paginated case list, newest first. Optional status filter (open, awaiting_review, approved, closed, failed, superseded)."""
    q = select(Case)
    if status_filter:
        q = q.where(Case.status == status_filter)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.order_by(Case.id.desc()).offset(offset).limit(limit)).scalars()
    items = [CaseOut(id=c.id, alert_id=c.alert_id, status=c.status, risk_score=c.risk_score) for c in rows]
    return CasePage(total=total, offset=offset, limit=limit, items=items)


@router.get("/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: int, _: User = Depends(current_user), db: Session = Depends(db_session)) -> CaseOut:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    return CaseOut(id=case.id, alert_id=case.alert_id, status=case.status, risk_score=case.risk_score)


@router.get("/cases/{case_id}/events")
def case_events(case_id: int, _: User = Depends(current_user), db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    rows = db.execute(select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.id)).scalars()
    return [
        {"node": e.node, "kind": e.kind, "payload": e.payload_json, "created_at": e.created_at.isoformat()}
        for e in rows
    ]


@router.get("/cases/{case_id}/sar")
def get_sar(case_id: int, _: User = Depends(current_user), db: Session = Depends(db_session)) -> dict[str, Any]:
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
    _: User = Depends(current_user),
    db: Session = Depends(db_session),
) -> dict[str, Any]:
    """Cytoscape elements for the case account. Parallel transfers between the same pair are
    merged into one edge (count, total). Only the top `max_edges` pairs by total are returned."""
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
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
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status != "awaiting_review":
        raise HTTPException(status_code=409, detail=f"case is {case.status}, not awaiting_review")
    if not body.reviewed:
        raise HTTPException(status_code=422, detail="reviewed must be true")
    case_service.resume_case(
        case_id,
        {"action": body.action, "reason": body.reason, "typologies": body.typologies, "actor_user_id": user.id},
    )
    db.refresh(case)
    return CaseOut(id=case.id, alert_id=case.alert_id, status=case.status, risk_score=case.risk_score)

