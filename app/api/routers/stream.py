"""Live updates over server-sent events (roles plan, group D).

The client sends the Bearer token in the Authorization header (fetch streaming), so the token never appears in a URL.
The stream does not hold a database connection between pushes: each push opens a short session, reads, and closes it.
"""

import asyncio
import time

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.auth.tokens import decode_access_token
from app.db.models import Notification, User
from app.db.session import SessionLocal
from app.services.cache import get_or_compute, version
from app.services.dashboard import dashboard_payload
from app.services.stream import CHECK_S, HEARTBEAT_S, PUSH_INTERVAL, should_push, sse

router = APIRouter(prefix="/api", tags=["stream"])


def _user_id(request: Request) -> int:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    try:
        return int(decode_access_token(token)["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="not authenticated") from None


def _snapshot(user_id: int) -> dict:
    """The dashboard and the unread count for one user. Raises LookupError if the user is gone or inactive."""
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise LookupError(user_id)
        dash = get_or_compute(f"dashboard:{user.role}:{user.id}", lambda: dashboard_payload(db, user))
        unread = db.execute(
            select(func.count()).select_from(Notification)
            .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        ).scalar_one()
        return {"dashboard": dash, "unread": unread}


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    user_id = _user_id(request)

    async def events():
        seen = -1  # the change version this connection last sent
        last_push = -PUSH_INTERVAL  # so the first snapshot goes out at once
        last_beat = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            now = time.monotonic()
            current = version()
            if should_push(seen != current, last_push, now):
                # Record the version before reading. A change during the read leaves seen != version, so the next
                # tick pushes again rather than missing it.
                seen = current
                try:
                    data = await run_in_threadpool(_snapshot, user_id)
                except LookupError:
                    yield sse("error", {"detail": "user unavailable"})
                    return
                last_push = now
                last_beat = now
                yield sse("snapshot", data)
            elif now - last_beat >= HEARTBEAT_S:
                last_beat = now
                yield ": ping\n\n"
            await asyncio.sleep(CHECK_S)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
