"""Notifications for the signed-in user (roles plan, group C). A user sees and marks only their own."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.db.models import Notification, User
from app.services.cache import invalidate

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _out(n: Notification) -> dict[str, Any]:
    return {"id": n.id, "kind": n.kind, "message": n.message, "link": n.link,
            "created_at": n.created_at.isoformat() if n.created_at else None, "read": n.read_at is not None}


def _unread(db: Session, user_id: int) -> int:
    return db.execute(
        select(func.count()).select_from(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    ).scalar_one()


@router.get("")
def list_notifications(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                       user: User = Depends(current_user), db: Session = Depends(db_session)) -> dict[str, Any]:
    """Unread first, then newest first. Pages with limit and offset; total counts every notification of the user."""
    rows = db.execute(
        select(Notification).where(Notification.user_id == user.id)
        .order_by(Notification.read_at.is_(None).desc(), Notification.id.desc()).offset(offset).limit(limit)
    ).scalars()
    total = db.execute(
        select(func.count()).select_from(Notification).where(Notification.user_id == user.id)
    ).scalar_one()
    return {"unread": _unread(db, user.id), "total": total, "offset": offset, "limit": limit,
            "items": [_out(n) for n in rows]}


@router.post("/{notification_id}/read")
def mark_read(notification_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)) -> dict[str, Any]:
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="notification not found")
    if n.read_at is None:
        n.read_at = datetime.now(UTC)
        db.commit()
        invalidate()
    return {"unread": _unread(db, user.id)}


@router.post("/read-all")
def mark_all_read(user: User = Depends(current_user), db: Session = Depends(db_session)) -> dict[str, Any]:
    db.execute(
        Notification.__table__.update()
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    db.commit()
    invalidate()
    return {"unread": 0}
