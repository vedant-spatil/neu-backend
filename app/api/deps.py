from collections.abc import Iterator

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.tokens import decode_access_token
from app.db.models import User
from app.db.session import get_db

bearer = HTTPBearer(auto_error=False)


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized
    try:
        claims = decode_access_token(creds.credentials)
    except jwt.InvalidTokenError:
        raise unauthorized from None
    user = db.get(User, int(claims["sub"]))
    # End the read transaction now. Otherwise the session stays "idle in transaction" and
    # blocks schema work such as the LangGraph checkpointer's CREATE INDEX CONCURRENTLY.
    db.commit()
    if user is None or not user.is_active:
        raise unauthorized
    return user


def db_session() -> Iterator[Session]:
    yield from get_db()


ROLES = ("admin", "qa")  # investigator folded into admin and qa


def require_role(*allowed: str):
    """Dependency factory: 403 unless the current user's role is in `allowed` (plan Part A)."""

    def check(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"requires role: {', '.join(allowed)}")
        return user

    return check
