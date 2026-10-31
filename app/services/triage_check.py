"""Startup check for XGBoost triage: warns when the configured model run has no scores in the database.

With TRIAGE_MODEL=xgb, a case whose account-day has no score falls back to its rule score (recorded in the case audit
trail as model_missing). This warning shows that fallback is happening for every case, before any case runs.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import TriageScore
from app.db.session import SessionLocal

log = logging.getLogger(__name__)


def warn_if_no_scores() -> int:
    """Returns the number of score rows for the configured run. Does nothing unless triage is xgb."""
    settings = get_settings()
    if settings.triage_model != "xgb":
        return 0
    with SessionLocal() as db:
        count = db.execute(
            select(func.count()).select_from(TriageScore).where(TriageScore.model_version == settings.triage_model_run)
        ).scalar_one()
    if count == 0:
        log.warning("TRIAGE_MODEL=xgb but triage_scores has no rows for run %r; cases use rule scores until it is loaded",
                    settings.triage_model_run)
    return count
