import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import create_access_token
from app.db.models import User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut
from app.services.cache import invalidate

router = APIRouter(prefix="/api/auth", tags=["auth"])

_INVALID = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

# Sign-up is open and always creates a QA user (roles plan, group B). Limited per client IP, in memory: one API process.
SIGNUP_WINDOW_S = 600.0
SIGNUP_MAX_PER_WINDOW = 5
_signup_hits: dict[str, list[float]] = {}
_signup_lock = threading.Lock()


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)


def _signup_allowed(ip: str, now: float) -> bool:
    with _signup_lock:
        recent = [t for t in _signup_hits.get(ip, []) if now - t < SIGNUP_WINDOW_S]
        if len(recent) >= SIGNUP_MAX_PER_WINDOW:
            _signup_hits[ip] = recent
            return False
        recent.append(now)
        _signup_hits[ip] = recent
        return True


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(db_session)) -> TokenResponse:
    user = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise _INVALID
    return TokenResponse(access_token=create_access_token(user.id, user.role))


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(body: SignupIn, request: Request, db: Session = Depends(db_session)) -> TokenResponse:
    ip = request.client.host if request.client else "unknown"
    if not _signup_allowed(ip, time.monotonic()):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="too many sign-ups; try again later")
    if db.execute(select(User.id).where(User.email == body.email)).first() is not None:
        # Generic on purpose: do not confirm which emails have accounts.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="could not create the account; try signing in")
    user = User(email=body.email, password_hash=hash_password(body.password), role="qa", is_active=True)
    db.add(user)
    db.commit()
    invalidate()
    return TokenResponse(access_token=create_access_token(user.id, user.role))


def password_change_error(current_matches: bool, current: str, new: str) -> str | None:
    """The first problem with a password change, or None. Mirrors the sign-up rule for the new password."""
    if not current_matches:
        return "the current password is wrong"
    if len(new) < 10:
        return "use at least 10 characters for the new password"
    if new == current:
        return "the new password must differ from the current one"
    return None


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, user: User = Depends(current_user),
                    db: Session = Depends(db_session)) -> dict[str, str]:
    """Changes the signed-in user's password. The password itself is never returned or logged: only its hash is kept."""
    problem = password_change_error(verify_password(body.current_password, user.password_hash),
                                    body.current_password, body.new_password)
    if problem is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem)
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"status": "changed"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut(id=user.id, email=user.email, role=user.role)
