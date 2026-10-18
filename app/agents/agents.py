"""Agent nodes. Each takes (db, state) and returns a partial state update. Failures are recorded
in state["failures"] and the branch is marked failed; nothing is skipped silently (plan section 4)."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Party, SarDraft, Transaction
from app.orchestration.prompts import (
    SAR_SCHEMA,
    SAR_SYSTEM,
    TYPOLOGY_NAMES,
    TYPOLOGY_SCHEMA,
    TYPOLOGY_SYSTEM,
)
from app.services import graph_store, llm, pii, sanctions
from app.services.audit import write_event

WINDOW = timedelta(hours=48)
MAX_COUNTERPARTIES = 10
LIGHT_MAX_TOKENS = 800
HEAVY_MAX_TOKENS = 2000


def _window(ts: datetime) -> dict[str, str]:
    return {"start": (ts - WINDOW).isoformat(), "end": (ts + WINDOW).isoformat()}


def triage(db: Session, state: dict[str, Any]) -> dict[str, Any]:
    threshold = get_settings().triage_threshold
    score = float(state["risk_score"])
    if score < threshold:
        write_event(db, state["case_id"], "triage", "decision", {"score": score, "threshold": threshold})
        return {"status": "closed_low_risk"}
    write_event(db, state["case_id"], "triage", "opened", {"score": score, "rules": state["alert_rules"]})
    return {"status": "open"}


def load_counterparties(db: Session, state: dict[str, Any]) -> dict[str, Any]:
    """Counterparties of the alert account within the alert window (plan 16.3 party scope)."""
    acct = state["account_id"]
    ts = datetime.fromisoformat(state["alert_ts"])
    w = _window(ts)
    rows = db.execute(
        select(Transaction.from_account, Transaction.to_account)
        .where(
            or_(Transaction.from_account == acct, Transaction.to_account == acct),
            Transaction.ts >= datetime.fromisoformat(w["start"]),
            Transaction.ts <= datetime.fromisoformat(w["end"]),
        )
        .limit(500)
    ).all()
    cps: list[str] = []
    for f, t in rows:
        for a in (f, t):
            if a != acct and a not in cps:
                cps.append(a)
            if len(cps) >= MAX_COUNTERPARTIES:
                break
    return {"counterparties": cps}


def flow_tracer(db: Session, state: dict[str, Any]) -> dict[str, Any]:
    acct = state["account_id"]
    ts = datetime.fromisoformat(state["alert_ts"])
    w = _window(ts)
    try:
        paths = {
            "fan_out": graph_store.run_template("fan_out", acct=acct, min_targets=10, **w),
            "fan_in": graph_store.run_template("fan_in", acct=acct, min_sources=10, **w),
            "cycle": graph_store.run_template("cycle", acct=acct, **w),
        }
    except Exception as exc:  # noqa: BLE001
        write_event(db, state["case_id"], "flow_tracer", "error", {"error": str(exc)})
        return {"failures": [f"flow_tracer: {type(exc).__name__}"]}
    write_event(db, state["case_id"], "flow_tracer", "done", {k: len(v) for k, v in paths.items()})
    return {"paths": paths}


def screening(db: Session, state: dict[str, Any]) -> dict[str, Any]:
    accounts = [state["account_id"], *state.get("counterparties", [])]
    parties = {
        p.account_id: p
        for p in db.execute(select(Party).where(Party.account_id.in_(accounts))).scalars()
    }
    hits: list[dict[str, Any]] = []
    try:
        for acct in accounts:
            p = parties.get(acct)
            if p is None:
                continue
            for h in sanctions.match_party(p.name, p.dob, p.country):
                hits.append({"party_token_account": acct, **h})
    except Exception as exc:  # noqa: BLE001
        write_event(db, state["case_id"], "screening", "error", {"error": str(exc)})
        return {"failures": [f"screening: {type(exc).__name__}"]}
    write_event(db, state["case_id"], "screening", "done", {"hits": len(hits)})
    return {"screening_hits": hits}


def _evidence_ids(state: dict[str, Any]) -> set[int]:
    ids = set(state.get("txn_ids", []))
    for rows in (state.get("paths") or {}).values():
        for r in rows:
            ids.update(r.get("txn_ids") or [])
    return {int(i) for i in ids}


EVIDENCE_TXN_LIMIT = 15  # transactions shown to the LLM (largest first); keeps prompts small
MAX_CITATIONS = 10


def _txn_rows(db: Session, ids: list[int]) -> list[dict[str, Any]]:
    """Transactions the LLM may cite. Account ids are synthetic; names never reach this list."""
    if not ids:
        return []
    rows = db.execute(
        select(Transaction)
        .where(Transaction.id.in_(ids[:200]))
        .order_by(Transaction.amount_paid.desc())
        .limit(EVIDENCE_TXN_LIMIT)
    ).scalars()
    return [
        {"txn_id": t.id, "ts": t.ts.isoformat(), "from": t.from_account, "to": t.to_account,
         "amount": float(t.amount_paid), "currency": t.payment_currency, "format": t.payment_format}
        for t in rows
    ]


def typology(db: Session, state: dict[str, Any]) -> dict[str, Any]:
    s = get_settings()
    evidence = {
        "transactions": _txn_rows(db, sorted(_evidence_ids(state))),
        "alert_rules": state.get("alert_rules", []),
        "paths": {k: [{kk: vv for kk, vv in r.items() if kk != "party_token_account"} for r in v][:5]
                  for k, v in (state.get("paths") or {}).items()},
        "screening_hit_count": len(state.get("screening_hits", [])),
        "txn_ids": sorted(_evidence_ids(state))[:200],
        "allowed_names": TYPOLOGY_NAMES,
    }
    try:
        out = llm.complete_json(
            db,
            model=s.llm_model_light,
            system=TYPOLOGY_SYSTEM,
            user=str(evidence),
            schema_name="typology",
            schema=TYPOLOGY_SCHEMA,
            max_completion_tokens=LIGHT_MAX_TOKENS,
        )
    except llm.LlmError as exc:
        write_event(db, state["case_id"], "typology", "error", {"error": str(exc)})
        return {"failures": ["typology: llm failed"]}
    allowed = _evidence_ids(state)
    clean = []
    for t in out.get("typologies", []):
        ids = [i for i in t.get("evidence_txn_ids", []) if i in allowed]
        clean.append({**t, "evidence_txn_ids": ids, "dropped_ids": len(t.get("evidence_txn_ids", [])) - len(ids)})
    write_event(db, state["case_id"], "typology", "done", {"count": len(clean)})
    return {"typologies": clean}


def report_writer(db: Session, state: dict[str, Any]) -> dict[str, Any]:
    s = get_settings()
    parties = {
        p.account_id: p
        for p in db.execute(
            select(Party).where(Party.account_id.in_([state["account_id"], *state.get("counterparties", [])]))
        ).scalars()
    }
    names = [p.name for p in parties.values()]
    token_of = {acct: f"PARTY_{i:03d}" for i, acct in enumerate([state["account_id"], *state.get("counterparties", [])], 1)}
    evidence = {
        "subject_token": token_of.get(state["account_id"]),
        "alert_ts": state["alert_ts"],
        "rules": state.get("alert_rules", []),
        "typologies": [{k: v for k, v in t.items() if k != "dropped_ids"} for t in state.get("typologies", [])],
        "screening_hits": [{"token": token_of.get(h["party_token_account"]), "dataset": h["dataset"],
                            "program_id": h.get("program_id"), "score": h["score"]}
                           for h in state.get("screening_hits", [])],
        "transactions": _txn_rows(db, sorted(_evidence_ids(state))),
        "txn_ids": sorted(_evidence_ids(state))[:200],
        "max_citations": MAX_CITATIONS,
    }
    try:
        out = llm.complete_json(
            db,
            model=s.llm_model_heavy,
            system=SAR_SYSTEM,
            user=str(evidence),
            schema_name="sar_draft",
            schema=SAR_SCHEMA,
            max_completion_tokens=HEAVY_MAX_TOKENS,
        )
    except llm.LlmError as exc:
        write_event(db, state["case_id"], "report_writer", "error", {"error": str(exc)})
        return {"failures": ["report_writer: llm failed"]}
    allowed = _evidence_ids(state)
    cited = [i for i in out.get("cited_txn_ids", []) if i in allowed][:MAX_CITATIONS]
    out["cited_txn_ids"] = cited
    out["narrative"] = pii.scrub_text(out.get("narrative", ""), names)
    version = (db.execute(select(func.count()).select_from(SarDraft).where(SarDraft.case_id == state["case_id"])).scalar_one()) + 1
    db.add(SarDraft(case_id=state["case_id"], version=version, body_redacted=out))
    db.commit()
    write_event(db, state["case_id"], "report_writer", "done", {"cited": len(cited), "sar_version": version})
    return {"sar_draft": out}
