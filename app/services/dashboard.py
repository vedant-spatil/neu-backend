"""Dashboard numbers (roles plan, groups B and E). Admin sees the totals. QA sees only the cases assigned to them, and
the alert figures stay admin-only. Both the dashboard endpoint and the live stream use these functions."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, and_, cast, func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Alert, Case, Decision, User
from app.services.access import case_scope


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


def _threshold() -> float:
    return get_settings().triage_threshold


def dashboard_payload(db: Session, user: User) -> dict[str, Any]:
    scope = case_scope(user)
    case_q = select(Case.status, func.count())
    if scope is not None:
        case_q = case_q.where(scope)
    case_status = dict(db.execute(case_q.group_by(Case.status)).all())
    decision_q = select(Decision.action, func.count())
    if scope is not None:
        decision_q = decision_q.join(Case, Case.id == Decision.case_id).where(scope)
    decisions_by_action = dict(db.execute(decision_q.group_by(Decision.action)).all())
    payload: dict[str, Any] = {
        "cases_by_status": case_status,
        "decisions_by_action": decisions_by_action,
        "failed_cases": case_status.get("failed", 0),
        "alerts_above_threshold_by_rule": {},
        "top_accounts_by_alerts": [],
        "score_histogram": {},
    }
    if user.role != "admin":
        return payload

    alert_rules: dict[str, int] = {}
    for (rules,) in db.execute(select(Alert.rules).where(Alert.risk_score >= _threshold())).all():
        for r in rules or []:
            alert_rules[r] = alert_rules.get(r, 0) + 1
    payload["alerts_above_threshold_by_rule"] = alert_rules
    top = (
        select(Alert.account_id.label("account_id"), func.count().label("alerts"))
        .where(Alert.risk_score >= _threshold())
        .group_by(Alert.account_id).order_by(func.count().desc()).limit(10).subquery()
    )
    rows = db.execute(
        select(top.c.account_id, top.c.alerts, Case.id, Case.status)
        .outerjoin(Case, and_(Case.account_id == top.c.account_id, Case.status != "superseded"))
        .order_by(top.c.alerts.desc())
    ).all()
    payload["top_accounts_by_alerts"] = [
        {"account_id": a, "alerts": n, "case_id": cid, "case_status": cstatus}
        for a, n, cid, cstatus in rows
    ]
    buckets = dict(
        db.execute(select(func.floor(Alert.risk_score * 10), func.count()).group_by(text("1"))).all()
    )
    payload["score_histogram"] = {f"{int(k) / 10:.1f}": v for k, v in buckets.items()}
    return payload


def trends_payload(db: Session, user: User, days: int) -> dict[str, Any]:
    """Daily series for the charts. alerts_by_day groups alerts by their transaction day (the data's clock) and is
    admin-only. activity_by_day groups cases and decisions by creation day (the app's clock), limited to the user's
    cases."""
    scope = case_scope(user)
    alerts: list[tuple[date, int]] = []
    if user.role == "admin":
        threshold = _threshold()
        alert_day = cast(Alert.ts, Date)
        last = db.execute(select(func.max(alert_day)).where(Alert.risk_score >= threshold)).scalar()
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
    cases_q = select(case_day, func.count()).where(Case.created_at >= since)
    if scope is not None:
        cases_q = cases_q.where(scope)
    cases = db.execute(cases_q.group_by(case_day)).all()
    decision_day = cast(Decision.created_at, Date)
    decisions_q = select(decision_day, Decision.action, func.count()).where(Decision.created_at >= since)
    if scope is not None:
        decisions_q = decisions_q.join(Case, Case.id == Decision.case_id).where(scope)
    decisions = db.execute(decisions_q.group_by(decision_day, Decision.action)).all()
    return {
        "alerts_by_day": [{"day": d.isoformat(), "alerts": n} for d, n in alerts],
        "activity_by_day": merge_activity(list(cases), list(decisions)),
    }
