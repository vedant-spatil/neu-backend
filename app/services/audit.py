from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CaseEvent


def write_event(db: Session, case_id: int, node: str, kind: str, payload: dict[str, Any] | None = None) -> None:
    """Append-only audit row. Never update or delete these rows (plan section 4)."""
    db.add(CaseEvent(case_id=case_id, node=node, kind=kind, payload_json=payload or {}))
    db.commit()
