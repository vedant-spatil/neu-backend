import operator
from typing import Annotated, Any, TypedDict


class CaseState(TypedDict, total=False):
    case_id: int
    alert_id: int
    account_id: str
    alert_ts: str  # ISO 8601
    alert_rules: list[str]
    risk_score: float
    txn_ids: list[int]
    counterparties: list[str]
    status: str  # open | awaiting_review | approved | closed | failed | closed_low_risk
    paths: dict[str, Any]  # Flow Tracer output
    screening_hits: list[dict[str, Any]]
    typologies: list[dict[str, Any]]
    sar_draft: dict[str, Any] | None
    pseudonym_map: str  # Fernet token, never plain
    failures: Annotated[list[str], operator.add]  # parallel branches append
    decision: dict[str, Any] | None
