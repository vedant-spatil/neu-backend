"""Builds cases from grouped alerts (plan: case grouping). Runs after every sync and from the admin's "Build cases"
button. services/grouping.py holds the rules. This module reads the database, applies the rules and writes the audit
trail.

A build is idempotent: a second build over the same alerts adds nothing and writes no events. At most
`max_cases_per_run` new cases start per build (each one runs the LLM workflow). The rest wait for the next build.

Memory: the build reads alert columns without raw_json, and reads transfers in batches of accounts. Account names are
mapped to small integers for the grouping, so the transfer table stays compact.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import NamedTuple

import numpy as np
import pandas as pd
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Alert, Case, CaseAccount, CaseAlert, Transaction
from app.services import grouping as g
from app.services.audit import write_event
from app.services.cache import invalidate
from app.services.cases import run_case

log = logging.getLogger(__name__)
_build_lock = threading.Lock()
TRANSFER_BATCH = 5_000  # accounts per transfer query
IN_BATCH = 20_000  # ids per IN (...) clause


class BuildBusy(RuntimeError):
    """Another build (a sync or a manual build) is running."""


class AlertRow(NamedTuple):
    """The alert columns a build needs. raw_json is left out: it is read only for a case's primary alert."""

    id: int
    account_id: str
    risk_score: float
    ts: object  # datetime
    rules: list
    source: str


@dataclass(frozen=True)
class Job:
    """A new case's workflow. The fields are the arguments of services.cases.run_case."""

    case_id: int
    alert_id: int
    account_id: str
    alert_ts: str
    rules: tuple[str, ...]
    risk: float
    txn_ids: tuple[int, ...]


@dataclass
class BuildResult:
    created: int = 0
    added: int = 0
    merged: int = 0
    deferred: int = 0
    unchanged: int = 0
    queued: int = 0
    jobs: list[Job] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {"created": self.created, "added": self.added, "merged": self.merged, "deferred": self.deferred,
                "unchanged": self.unchanged, "queued": self.queued}


def build_cases(db: Session, threshold: float | None = None, max_new: int | None = None) -> BuildResult:
    """Groups the alerts at or above the threshold into cases. Raises BuildBusy if another build is running.
    The caller starts the workflows in `result.jobs` (start_jobs)."""
    settings = get_settings()
    threshold = settings.triage_threshold if threshold is None else threshold
    max_new = settings.max_cases_per_run if max_new is None else max_new
    if not _build_lock.acquire(blocking=False):
        raise BuildBusy("a case build is already running")
    try:
        result = _build(db, threshold, max_new, settings.model_alerts_per_account)
    finally:
        _build_lock.release()
    invalidate()
    return result


def cap_model_alerts(rows: list[AlertRow], cap: int) -> tuple[list[AlertRow], dict[str, int]]:
    """Keeps every rule alert, and the `cap` highest-scoring model alerts per account. Returns the kept rows and how many
    model alerts were left out, per account. Model alerts are per edge, so one account can have many."""
    kept = [r for r in rows if r.source != "model"]
    models: dict[str, list[AlertRow]] = {}
    for r in rows:
        if r.source == "model":
            models.setdefault(r.account_id, []).append(r)
    left_out: dict[str, int] = {}
    for acct, rs in models.items():
        rs.sort(key=lambda r: (-r.risk_score, r.id))
        kept.extend(rs[:cap])
        if len(rs) > cap:
            left_out[acct] = len(rs) - cap
    return kept, left_out


class _Codes:
    """Maps account names to small integers, and back."""

    def __init__(self) -> None:
        self._code: dict[str, int] = {}
        self.names: list[str] = []

    def code(self, name: str) -> int:
        existing = self._code.get(name)
        if existing is None:
            existing = len(self.names)
            self._code[name] = existing
            self.names.append(name)
        return existing


def _build(db: Session, threshold: float, max_new: int, model_cap: int) -> BuildResult:
    rows = [AlertRow(*r) for r in db.execute(
        select(Alert.id, Alert.account_id, Alert.risk_score, Alert.ts, Alert.rules, Alert.source)
        .where(Alert.source.in_(("rules", "model")), Alert.risk_score >= threshold)
    ).all()]
    kept, left_out = cap_model_alerts(rows, model_cap)
    by_account: dict[str, list[AlertRow]] = {}
    for r in kept:
        by_account.setdefault(r.account_id, []).append(r)
    result = BuildResult()
    if not by_account:
        return result
    scores = {acct: max(r.risk_score for r in rs) for acct, rs in by_account.items()}
    links = _linked_pairs(db, set(scores))
    active = _active_cases(db, set(scores))
    for comp in g.components(scores, links):
        plan = g.plan_component(comp.accounts, active, scores)
        if plan.kind == g.PLAN_DEFER:
            result.deferred += 1
            log.info("case build: %s (%s)", plan.reason, ", ".join(comp.accounts))
        elif plan.kind == g.PLAN_MERGE:
            if _merge_cases(db, plan, by_account, active):
                result.merged += 1
            else:
                result.unchanged += 1
        elif plan.kind == g.PLAN_NEW:
            if result.created >= max_new:
                result.queued += 1
                continue
            job = _create_case(db, plan, by_account, comp.best_score, left_out)
            if job is None:
                result.unchanged += 1
            else:
                result.created += 1
                result.jobs.append(job)
        elif plan.kind == g.PLAN_ADD:
            if _grow_case(db, plan, by_account, active, left_out):
                result.added += 1
            else:
                result.unchanged += 1
        else:
            result.unchanged += 1
    return result


def _linked_pairs(db: Session, accounts: set[str]) -> set[tuple[str, str]]:
    """Accounts that share enough counterparties in time (grouping.linked_pairs), as names. Transfers are read in
    batches of accounts and mapped to integer codes first, so the frame stays small."""
    codes = _Codes()
    for name in sorted(accounts):
        codes.code(name)
    frames: list[pd.DataFrame] = []
    names = sorted(accounts)
    for start in range(0, len(names), TRANSFER_BATCH):
        batch = names[start : start + TRANSFER_BATCH]
        rows = db.execute(
            select(Transaction.from_account, Transaction.to_account, Transaction.ts)
            .where(or_(Transaction.from_account.in_(batch), Transaction.to_account.in_(batch)))
        ).all()
        frames.append(pd.DataFrame({
            "from_account": np.fromiter((codes.code(r[0]) for r in rows), dtype=np.int32, count=len(rows)),
            "to_account": np.fromiter((codes.code(r[1]) for r in rows), dtype=np.int32, count=len(rows)),
            "ts": pd.to_datetime([r[2] for r in rows]),
        }))
    if not frames:
        return set()
    transfers = pd.concat(frames, ignore_index=True)
    del frames
    pairs = g.linked_pairs(transfers, {codes.code(a) for a in accounts})
    return {(codes.names[a], codes.names[b]) for a, b in pairs}


def _active_cases(db: Session, accounts: set[str]) -> dict[str, tuple[int, str]]:
    """Each account's active case as (case_id, status). An account with no active link is missing from the result."""
    names = sorted(accounts)
    out: dict[str, tuple[int, str]] = {}
    for start in range(0, len(names), IN_BATCH):
        rows = db.execute(
            select(CaseAccount.account_id, Case.id, Case.status)
            .join(Case, Case.id == CaseAccount.case_id)
            .where(CaseAccount.active.is_(True), Case.status != "superseded",
                   CaseAccount.account_id.in_(names[start : start + IN_BATCH]))
        ).all()
        out.update({acct: (case_id, status) for acct, case_id, status in rows})
    return out


def _linked_alert_ids(db: Session, alert_ids: list[int]) -> set[int]:
    """Which of these alerts an active case already holds."""
    linked: set[int] = set()
    for start in range(0, len(alert_ids), IN_BATCH):
        linked.update(db.execute(
            select(CaseAlert.alert_id).where(CaseAlert.active.is_(True),
                                             CaseAlert.alert_id.in_(alert_ids[start : start + IN_BATCH]))
        ).scalars())
    return linked


def _create_case(db: Session, plan: g.Plan, by_account: dict[str, list[AlertRow]], best_score: float,
                 left_out: dict[str, int]) -> Job | None:
    """Opens one case for the component's free accounts. Its primary alert is the highest-scoring one. Returns None if
    a concurrent change took one of the accounts first (the unique indexes refused the links)."""
    alerts = [r for acct in plan.accounts for r in by_account[acct]]
    primary = max(alerts, key=lambda r: (r.risk_score, -r.id))
    case = Case(alert_id=primary.id, account_id=primary.account_id, status="open", risk_score=best_score, state={})
    db.add(case)
    try:
        db.flush()
        db.add_all(CaseAccount(case_id=case.id, account_id=acct, role="subject", active=True) for acct in plan.accounts)
        db.add_all(CaseAlert(case_id=case.id, alert_id=r.id, active=True) for r in alerts)
        write_event(db, case.id, "grouping", "created", {
            "accounts": list(plan.accounts),
            "alert_ids": [r.id for r in alerts],
            "primary_alert_id": primary.id,
            "excluded": list(plan.excluded),
            "model_alerts_left_out": sum(left_out.get(a, 0) for a in plan.accounts),
        })
        db.commit()
    except IntegrityError:
        db.rollback()
        log.warning("case build: a member changed while its case was being created; component skipped")
        return None
    raw = db.get(Alert, primary.id).raw_json or {}
    return Job(
        case_id=case.id,
        alert_id=primary.id,
        account_id=primary.account_id,
        alert_ts=primary.ts.isoformat(),
        rules=tuple(primary.rules or []),
        risk=primary.risk_score,
        txn_ids=tuple(int(i) for i in raw.get("txn_ids", [])),
    )


def _grow_case(db: Session, plan: g.Plan, by_account: dict[str, list[AlertRow]],
               active: dict[str, tuple[int, str]], left_out: dict[str, int]) -> bool:
    """Adds the free accounts, and every alert of the case's accounts it does not hold yet, to the open case. Returns True
    if anything was added. A failed case is flagged members_changed (its workflow is not re-run automatically). An
    awaiting-review case is flagged sar_stale."""
    case = db.get(Case, plan.case_id)
    joining = {acct for acct, (case_id, _) in active.items() if case_id == plan.case_id} | set(plan.accounts)
    alerts = [r for acct in sorted(joining) for r in by_account.get(acct, [])]
    linked = _linked_alert_ids(db, [r.id for r in alerts])
    new_alerts = [r for r in alerts if r.id not in linked]
    if not plan.accounts and not new_alerts:
        return False
    try:
        db.add_all(CaseAccount(case_id=case.id, account_id=acct, role="subject", active=True) for acct in plan.accounts)
        db.add_all(CaseAlert(case_id=case.id, alert_id=r.id, active=True) for r in new_alerts)
        write_event(db, case.id, "grouping", "added", {
            "accounts": list(plan.accounts),
            "alert_ids": [r.id for r in new_alerts],
            "model_alerts_left_out": sum(left_out.get(a, 0) for a in plan.accounts),
        })
        _flag_members_changed(db, case, "members added")
        db.commit()
    except IntegrityError:
        db.rollback()
        log.warning("case build: case %s changed while accounts were being added; component skipped", case.id)
        return False
    return True


def _merge_cases(db: Session, plan: g.Plan, by_account: dict[str, list[AlertRow]],
                 active: dict[str, tuple[int, str]]) -> bool:
    """Merges the losing cases into the winner: their account and alert links move to the winner, the losers are
    superseded (kept for the audit trail), and the winner is flagged. Returns False if a concurrent change interfered."""
    winner = db.get(Case, plan.case_id)
    losers = list(plan.losers)
    joining = {acct for acct, (case_id, _) in active.items() if case_id == winner.id or case_id in losers}
    joining |= set(plan.accounts)
    alerts = [r for acct in sorted(joining) for r in by_account.get(acct, [])]
    linked = _linked_alert_ids(db, [r.id for r in alerts])
    new_alerts = [r for r in alerts if r.id not in linked]
    try:
        db.execute(update(CaseAccount).where(CaseAccount.case_id.in_(losers)).values(case_id=winner.id)
                   .execution_options(synchronize_session=False))
        db.execute(update(CaseAlert).where(CaseAlert.case_id.in_(losers)).values(case_id=winner.id)
                   .execution_options(synchronize_session=False))
        db.add_all(CaseAccount(case_id=winner.id, account_id=acct, role="subject", active=True) for acct in plan.accounts)
        db.add_all(CaseAlert(case_id=winner.id, alert_id=r.id, active=True) for r in new_alerts)
        for loser_id in losers:
            loser = db.get(Case, loser_id)
            loser.status = "superseded"
            loser.state = {**(loser.state or {}), "merged_into": winner.id}
            write_event(db, loser_id, "grouping", "merged_into", {"winner": winner.id})
        write_event(db, winner.id, "grouping", "merged_from", {
            "cases": losers,
            "accounts": list(plan.accounts),
            "alert_ids": [r.id for r in new_alerts],
        })
        winner.risk_score = max([winner.risk_score] + [r.risk_score for r in alerts])
        _flag_members_changed(db, winner, "cases merged")
        db.commit()
    except IntegrityError:
        db.rollback()
        log.warning("case build: a merge into case %s was refused by the unique indexes; left for the next build",
                    winner.id)
        return False
    return True


def _flag_members_changed(db: Session, case: Case, reason: str) -> None:
    """Marks a case whose members changed. Failed cases show "rerun needed"; awaiting-review cases get a stale SAR."""
    state = {**(case.state or {}), "members_changed": True}
    if case.status == "awaiting_review":
        state["sar_stale"] = True
        write_event(db, case.id, "grouping", "sar_stale", {"reason": reason})
    case.state = state


def start_jobs(jobs: list[Job]) -> None:
    """Runs the new cases' workflows in one background thread, one after another, so LLM calls stay within the
    free-tier limits."""
    if jobs:
        threading.Thread(target=_run_jobs, args=(jobs,), name="case-jobs", daemon=True).start()


def _run_jobs(jobs: list[Job]) -> None:
    for job in jobs:
        run_case(job.case_id, job.alert_id, job.account_id, job.alert_ts, list(job.rules), job.risk, list(job.txn_ids))
