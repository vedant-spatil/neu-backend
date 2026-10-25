"""Notifications (roles plan, group C). Messages are generic: they name the case, never its accounts, amounts or SAR text.
Every write also invalidates the cache, so the live stream pushes the new unread count."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Notification, User
from app.services.cache import invalidate


def notify(db: Session, user_id: int, kind: str, message: str, link: str | None = None) -> None:
    db.add(Notification(user_id=user_id, kind=kind, message=message[:300], link=link))
    db.commit()
    invalidate()


def notify_admins(db: Session, kind: str, message: str, link: str | None = None) -> None:
    admin_ids = db.execute(select(User.id).where(User.role == "admin", User.is_active.is_(True))).scalars().all()
    db.add_all(Notification(user_id=uid, kind=kind, message=message[:300], link=link) for uid in admin_ids)
    db.commit()
    invalidate()
