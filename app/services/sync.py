"""Sync an uploaded CSV (ETL stages 3 and 4): validate it with the saved column mapping, load it through the ingest
path, then open cases for the highest-risk new alerts. Runs in a background thread. Progress is written to
imports.status and imports.report, which the frontend polls.

Replace semantics, as in app.ingest.load: transactions, parties and the account graph are replaced on every sync.
Rule alerts that already have cases are kept, and so are model alerts.
"""

from __future__ import annotations

import logging
import resource
import threading
import time
from pathlib import Path

from sqlalchemy import select, text

from app.config import get_settings
from app.db.models import Alert, Case, Import
from app.db.session import SessionLocal
from app.ingest.load import (
    _batches,
    _copy_transactions,
    _load_neo4j,
    _planted_names,
    _write_alerts,
    build_parties,
)
from app.ingest.mapping import load_mapped, validate_mapped
from app.services import cases as case_service
from app.services.graph_store import driver

log = logging.getLogger(__name__)
PLANTED_NAMES = 2000  # same default as the loader's --limit-plant
BUSY_STATUSES = {"validating", "loading", "triaging"}

# Imports whose sync thread is running in this process. A busy import that is not in here has no thread behind it.
_live: set[int] = set()
_live_lock = threading.Lock()
_started: dict[int, float] = {}


def is_live(import_id: int) -> bool:
    with _live_lock:
        return import_id in _live


def can_reset(status: str, live: bool) -> str | None:
    """None when the import may be reset. Otherwise the reason it may not."""
    if status not in BUSY_STATUSES:
        return f"import is {status}, not running"
    if live:
        return "sync is still running in this process; wait for it or restart the API"
    return None


def start_sync(import_id: int, path: Path | str, mapping: dict[str, str]) -> None:
    with _live_lock:
        _live.add(import_id)
    threading.Thread(target=_thread_main, args=(import_id, str(path), mapping), name=f"sync-{import_id}",
                     daemon=True).start()


def _thread_main(import_id: int, path: str, mapping: dict[str, str]) -> None:
    try:
        _run(import_id, path, mapping)
    finally:
        with _live_lock:
            _live.discard(import_id)


def fail_interrupted_imports() -> int:
    """At startup: a busy import has no sync thread (the restart killed it), so mark it failed.
    The sync replaces all data when it runs again, so nothing else needs repairing."""
    with SessionLocal() as db:
        stuck = db.execute(select(Import).where(Import.status.in_(BUSY_STATUSES))).scalars().all()
        for imp in stuck:
            imp.status = "failed"
            imp.report = {**(imp.report or {}), "error": "interrupted by restart"}
        db.commit()
        return len(stuck)


def _progress(import_id: int, status: str, **fields) -> None:
    """Writes status and fields to the import report. Each update also records the process's peak memory and the
    seconds since this sync started, so a finished run shows its time and memory per stage."""
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: KB
    elapsed = time.perf_counter() - _started.get(import_id, time.perf_counter())
    with SessionLocal() as db:
        imp = db.get(Import, import_id)
        imp.status = status
        imp.report = {**(imp.report or {}), **fields,
                      "elapsed_s": round(elapsed, 1), "process_peak_rss_mb": round(peak_mb)}
        db.commit()
    log.info("sync %s %s elapsed=%.0fs peak_rss=%.0fMB", import_id, status, elapsed, peak_mb)


def _run(import_id: int, path: str, mapping: dict[str, str]) -> None:
    _started[import_id] = time.perf_counter()
    try:
        _progress(import_id, "validating")
        report = validate_mapped(path, mapping)
        if report["errors"]:
            _progress(import_id, "rejected", validation=report)
            return
        _progress(import_id, "loading", validation=report, stage="alerts")
        df = load_mapped(path, mapping)
        _write_alerts(df)
        _progress(import_id, "loading", stage="transactions", rows=len(df))
        _copy_transactions(df)
        accounts = sorted(set(df["from_account"]).union(df["to_account"]))
        _progress(import_id, "loading", stage="parties", accounts=len(accounts))
        _replace_parties(accounts)
        _progress(import_id, "loading", stage="graph")
        _replace_graph(df, accounts)
        _progress(import_id, "triaging", stage="cases")
        opened = _open_cases()
        _progress(import_id, "triaged", stage="done", cases_opened=opened)
    except Exception as exc:
        log.exception("sync %s failed", import_id)
        _progress(import_id, "failed", error=str(exc)[:500])


def _replace_parties(accounts: list[str]) -> None:
    try:
        planted = _planted_names(PLANTED_NAMES)
    except Exception:  # noqa: BLE001
        log.warning("OpenSanctions names unavailable; parties are loaded without planted sanctions hits")
        planted = []
    parties = build_parties(accounts, planted)
    with SessionLocal() as db:
        db.execute(text("TRUNCATE parties RESTART IDENTITY"))
        db.commit()
        for batch in _batches(parties, 50_000):
            db.add_all(batch)
            db.flush()
        db.commit()


CLEAR_BATCH = 50_000  # one transaction per batch; a single delete of a full graph exceeds Neo4j's transaction memory


def _clear_graph() -> None:
    """Deletes every TRANSFER edge, then every Account, in batches of CLEAR_BATCH."""
    with driver().session() as s:
        while s.run("MATCH ()-[r:TRANSFER]->() WITH r LIMIT $n DELETE r RETURN count(*) AS n",
                    n=CLEAR_BATCH).single()["n"]:
            pass
        while s.run("MATCH (a:Account) WITH a LIMIT $n DETACH DELETE a RETURN count(*) AS n",
                    n=CLEAR_BATCH).single()["n"]:
            pass


def _replace_graph(df, accounts: list[str]) -> None:
    _clear_graph()
    _load_neo4j(df, accounts)


def _open_cases() -> int:
    """Opens a case for each new rule alert at or above the triage threshold, highest risk first, up to the cap.
    Each case runs the graph in turn (the LLM steps are rate limited, so the cases run one at a time)."""
    settings = get_settings()
    with SessionLocal() as db:
        alerts = db.execute(
            select(Alert)
            .where(Alert.source == "rules", Alert.risk_score >= settings.triage_threshold,
                   Alert.id.not_in(select(Case.alert_id)))
            .order_by(Alert.risk_score.desc(), Alert.id)
            .limit(settings.sync_max_cases)
        ).scalars().all()
        queued = [
            (a.id, a.account_id, a.ts.isoformat(), list(a.rules or []), a.risk_score,
             [int(i) for i in (a.raw_json or {}).get("txn_ids", [])])
            for a in alerts
        ]
        case_ids = [case_service.start_case(db, a).id for a in alerts]
    for case_id, (alert_id, account_id, ts, rules, risk, txn_ids) in zip(case_ids, queued):
        case_service.run_case(case_id, alert_id, account_id, ts, rules, risk, txn_ids)
    return len(case_ids)
