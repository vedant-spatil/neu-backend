"""Background assessment of one assigned account (plan Part A).

Runs the full case workflow for the account's highest-scored alert, then writes tags for QA.
Worker runs with --concurrency=1 to stay within the Bedrock throughput quota for the model.

Disabled: the Celery task evaluate_account is commented out below, with its imports. The tag
helpers stay active because the QA tag check uses them."""

# from sqlalchemy import select
#
# from app.db.models import Alert, Case
# from app.db.session import SessionLocal
# from app.orchestration.graph import get_graph
# from app.services import cases as case_service
# from app.tasks import celery_app


def compute_tags(case_status: str, rule_score: float, typologies: list[str], screening_hits: int) -> list[str]:
    """Pure function so the tag rules can be unit tested."""
    tags: list[str] = []
    if rule_score >= 0.3:
        tags.append("anomaly")
    if case_status == "awaiting_review":
        tags.append("case_found")
    tags.extend(f"typology:{t}" for t in typologies)
    if screening_hits:
        tags.append("sanctions_hit")
    if not tags:
        tags.append("no_anomaly")
    return tags


ALLOWED_TAGS = {"anomaly", "case_found", "sanctions_hit", "no_anomaly"}


def is_allowed_tag(tag: str) -> bool:
    """Fixed tag list (plan: tags). Typology tags are typology:<name>."""
    return tag in ALLOWED_TAGS or (tag.startswith("typology:") and len(tag) > len("typology:"))


# @celery_app.task(name="evaluate_account")
# def evaluate_account(project_id: int, account_id: str) -> None:
#     """Auto layer: run the case workflow for the account and store its tags as a QA draft."""
#     from app.db.models import Evaluation
#
#     with SessionLocal() as db:
#         top = db.execute(
#             select(Alert).where(Alert.account_id == account_id).order_by(Alert.risk_score.desc(), Alert.id).limit(1)
#         ).scalar_one_or_none()
#         if top is None:
#             db.add(Evaluation(project_id=project_id, account_id=account_id, source="auto", status="draft",
#                               tags=["no_alert"], notes="no alert for account"))
#             db.commit()
#             return
#         case = case_service.start_case(db, top)
#         case_id, rules, risk = case.id, list(top.rules or []), top.risk_score
#         alert_id, ts = top.id, top.ts.isoformat()
#         txn_ids = [int(i) for i in (top.raw_json or {}).get("txn_ids", [])]
#
#     case_service.run_case(case_id, alert_id, account_id, ts, rules, risk, txn_ids)
#     with SessionLocal() as db:
#         status_now = db.get(Case, case_id).status
#     state = get_graph().get_state({"configurable": {"thread_id": str(case_id)}}).values
#     typologies = [t.get("name", "") for t in state.get("typologies", []) if t.get("name")]
#     tags = compute_tags(status_now, risk, typologies, len(state.get("screening_hits", [])))
#     with SessionLocal() as db:
#         db.add(Evaluation(project_id=project_id, account_id=account_id, source="auto", status="draft",
#                           tags=tags, notes="", case_id=case_id))
#         db.commit()
