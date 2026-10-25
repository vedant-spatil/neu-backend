"""Who may see which case (roles plan, group B).

Admin sees every case. QA sees only the cases assigned to them. A case the user may not see is reported as not found,
so case IDs don't leak.
"""

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement
from sqlalchemy.orm import Session

from app.db.models import Case, User


def can_see_case(user: User, case: Case) -> bool:
    return user.role == "admin" or case.assignee_id == user.id


def get_visible_case(db: Session, user: User, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None or not can_see_case(user, case):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="case not found")
    return case


def case_scope(user: User) -> ColumnElement[bool] | None:
    """A WHERE clause for queries over cases. None means no restriction (admin)."""
    return None if user.role == "admin" else Case.assignee_id == user.id
