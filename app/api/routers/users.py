"""User lookup for admin screens (pick QA members for a project). Admin only."""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_role
from app.db.models import User

router = APIRouter(tags=["users"])
_admin = require_role("admin")


@router.get("/api/users")
def list_users(role: str | None = Query(default=None, pattern="^(admin|qa)$"),
               _: User = Depends(_admin), db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    q = select(User).where(User.is_active.is_(True)).order_by(User.email)
    if role is not None:
        q = q.where(User.role == role)
    return [{"id": u.id, "email": u.email, "role": u.role} for u in db.execute(q).scalars()]
