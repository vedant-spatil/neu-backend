from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CaseEvent


def write_event(db: Session, case_id: int, node: str, kind: str, payload: dict[str, Any] | None = None) -> None:
    """Append-only audit row. Never update or delete these rows (plan section 4)."""
    db.add(CaseEvent(case_id=case_id, node=node, kind=kind, payload_json=payload or {}))
    db.commit()


def write_events(db: Session, rows: list[tuple[int, str, str, dict[str, Any] | None]]) -> None:
    """Several audit rows in one commit. Each row is (case_id, node, kind, payload)."""
    db.add_all(
        CaseEvent(case_id=case_id, node=node, kind=kind, payload_json=payload or {})
        for case_id, node, kind, payload in rows
    )
    db.commit()
