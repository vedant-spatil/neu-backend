"""Sync an uploaded CSV: validate it with the saved column mapping, then load it through the ingest path. Runs in a
background thread. Progress is written to imports.status and imports.report, which the frontend polls. The sync opens
no cases: a case starts only when an investigator opens it from the alert queue (each case runs the LLM workflow).

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

from app.db.models import Import
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
        # No cases are opened here. Each case runs the LLM workflow, so a case starts only when an investigator opens
        # it from the alert queue.
        _progress(import_id, "triaged", stage="done", cases_opened=0)
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


