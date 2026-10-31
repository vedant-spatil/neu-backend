"""Admin-only summary of case-workflow telemetry (plan Part 1B). Reads CloudWatch Logs; see app.services.observability."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import require_role
from app.services.observability import ObservabilityError, summary

router = APIRouter(prefix="/api/observability", tags=["observability"])

_admin = require_role("admin")


@router.get("/summary")
def observability_summary(
    hours: int = Query(24, ge=1, le=168),
    _: Any = Depends(_admin),
) -> dict[str, Any]:
    try:
        return summary(hours)
    except ObservabilityError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="could not read the logs") from None
