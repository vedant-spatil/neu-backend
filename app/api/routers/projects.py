"""Projects (admin), QA evaluations and admin review (plan: projects, QA review)."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_role
from app.db.models import Evaluation, Project, ProjectAccount, ProjectMember, User
from app.tasks.assess import ALLOWED_TAGS, is_allowed_tag

# Disabled: the auto-validate feature (auto_validate flag, auto drafts from the Celery worker,
# and the QA validate-draft endpoint) is commented out below. The DB column stays.

router = APIRouter(tags=["projects"])
_admin = require_role("admin")
_qa = require_role("qa")


class ProjectIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    # auto_validate: bool = False  # disabled


# class ProjectPatch(BaseModel):  # disabled with auto-validate
#     auto_validate: bool


class MemberIn(BaseModel):
    user_id: int


class AccountsIn(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=200)


class TagsIn(BaseModel):
    tags: list[str] = Field(min_length=1, max_length=10)
    notes: str = Field(default="", max_length=4000)


class ReviewIn(BaseModel):
    action: str = Field(pattern="^(accept|reject)$")
    reason: str = Field(default="", max_length=2000)


def _project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="project not found")
    return p


def _check_tags(tags: list[str]) -> list[str]:
    bad = [t for t in tags if not is_allowed_tag(t)]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"tags not allowed: {bad}")
    return list(dict.fromkeys(tags))


def _is_member(db: Session, project_id: int, user_id: int) -> bool:
    return db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
    ).scalar_one_or_none() is not None


def _eval_out(e: Evaluation) -> dict[str, Any]:
    return {
        "id": e.id, "project_id": e.project_id, "account_id": e.account_id, "source": e.source,
        "status": e.status, "tags": e.tags or [], "notes": e.notes, "case_id": e.case_id,
        "qa_user_id": e.qa_user_id, "reject_reason": e.reject_reason,
    }


# ---------- admin ----------

@router.post("/api/projects", status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectIn, admin: User = Depends(_admin), db: Session = Depends(db_session)) -> dict[str, Any]:
    p = Project(name=body.name, created_by=admin.id)
    db.add(p)
    db.commit()
    return {"id": p.id, "name": p.name}


# @router.patch("/api/projects/{project_id}")  # disabled: auto-validate toggle
# def set_auto_validate(project_id: int, body: ProjectPatch, _: User = Depends(_admin),
#                       db: Session = Depends(db_session)) -> dict[str, Any]:
#     p = _project(db, project_id)
#     p.auto_validate = body.auto_validate
#     db.commit()
#     return {"id": p.id, "auto_validate": p.auto_validate}


@router.post("/api/projects/{project_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(project_id: int, body: MemberIn, _: User = Depends(_admin), db: Session = Depends(db_session)) -> dict[str, Any]:
    _project(db, project_id)
    u = db.get(User, body.user_id)
    if u is None or u.role != "qa" or not u.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="member must be an active qa user")
    if not _is_member(db, project_id, u.id):
        db.add(ProjectMember(project_id=project_id, user_id=u.id))
        db.commit()
    return {"project_id": project_id, "user_id": u.id}


@router.post("/api/projects/{project_id}/accounts", status_code=status.HTTP_202_ACCEPTED)
def add_accounts(project_id: int, body: AccountsIn, _: User = Depends(_admin),
                 db: Session = Depends(db_session)) -> dict[str, Any]:
    _project(db, project_id)
    added: list[str] = []
    for acct in dict.fromkeys(body.account_ids):
        exists = db.execute(select(ProjectAccount).where(
            ProjectAccount.project_id == project_id, ProjectAccount.account_id == acct)).scalar_one_or_none()
        if exists is None:
            db.add(ProjectAccount(project_id=project_id, account_id=acct))
            db.commit()
            added.append(acct)
            # Disabled: auto layer queued evaluate_account.delay(project_id, acct) here.
    return {"added": added}


@router.get("/api/projects")
def list_projects(user: User = Depends(require_role("admin", "qa")), db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    q = select(Project).order_by(Project.id.desc())
    if user.role == "qa":
        q = q.join(ProjectMember, ProjectMember.project_id == Project.id).where(ProjectMember.user_id == user.id)
    return [{"id": p.id, "name": p.name} for p in db.execute(q).scalars()]


@router.get("/api/projects/{project_id}")
def get_project(project_id: int, user: User = Depends(require_role("admin", "qa")),
                db: Session = Depends(db_session)) -> dict[str, Any]:
    p = _project(db, project_id)
    if user.role == "qa" and not _is_member(db, project_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="project not found")
    members = db.execute(
        select(User.id, User.email).join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id).order_by(User.email)
    ).all()
    accounts = db.execute(
        select(ProjectAccount.account_id).where(ProjectAccount.project_id == project_id)
        .order_by(ProjectAccount.account_id)
    ).scalars().all()
    return {
        "id": p.id, "name": p.name,
        "members": [{"user_id": uid, "email": email} for uid, email in members],
        "accounts": list(accounts),
    }


@router.get("/api/tags")
def list_tags(_: User = Depends(require_role("admin", "qa"))) -> dict[str, Any]:
    """Fixed tag list for QA evaluations. Typology tags are typology:<name> and are not listed here."""
    return {"tags": sorted(ALLOWED_TAGS), "typology_prefix": "typology:"}


@router.get("/api/projects/{project_id}/evaluations")
def project_evaluations(project_id: int, _: User = Depends(_admin), db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    _project(db, project_id)
    rows = db.execute(select(Evaluation).where(Evaluation.project_id == project_id).order_by(Evaluation.id.desc())).scalars()
    return [_eval_out(e) for e in rows]


@router.post("/api/evaluations/{evaluation_id}/review")
def review(evaluation_id: int, body: ReviewIn, admin: User = Depends(_admin), db: Session = Depends(db_session)) -> dict[str, Any]:
    e = db.get(Evaluation, evaluation_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="evaluation not found")
    if e.status != "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"evaluation is {e.status}, not submitted")
    if body.action == "reject":
        if len(body.reason.strip()) < 10:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="reject needs a reason (10+ chars)")
        e.status, e.reject_reason = "rejected", body.reason.strip()
    else:
        e.status, e.reject_reason = "accepted", None
    e.reviewed_by, e.reviewed_at = admin.id, datetime.now(UTC)
    db.commit()
    return _eval_out(e)


# ---------- QA ----------

@router.get("/api/qa/projects/{project_id}/accounts")
def qa_accounts(project_id: int, qa: User = Depends(_qa), db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    if not _is_member(db, project_id, qa.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="project not found")
    accounts = db.execute(select(ProjectAccount.account_id).where(ProjectAccount.project_id == project_id)).scalars()
    evals = db.execute(select(Evaluation).where(Evaluation.project_id == project_id)).scalars().all()
    latest = {}
    for e in evals:
        if e.qa_user_id in (None, qa.id):
            latest[e.account_id] = e
    return [
        {"account_id": a, "evaluation_id": latest[a].id if a in latest else None,
         "status": latest[a].status if a in latest else "not_started",
         "source": latest[a].source if a in latest else None}
        for a in accounts
    ]


@router.post("/api/qa/projects/{project_id}/evaluations", status_code=status.HTTP_201_CREATED)
def qa_manual_evaluation(project_id: int, account_id: str, body: TagsIn, qa: User = Depends(_qa),
                         db: Session = Depends(db_session)) -> dict[str, Any]:
    _project(db, project_id)
    if not _is_member(db, project_id, qa.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="project not found")
    e = Evaluation(project_id=project_id, account_id=account_id, qa_user_id=qa.id, source="qa",
                   status="submitted", tags=_check_tags(body.tags), notes=body.notes)
    db.add(e)
    db.commit()
    return _eval_out(e)


# @router.post("/api/qa/evaluations/{evaluation_id}/validate")  # disabled: auto-validate draft endpoint
# def qa_validate_draft(evaluation_id: int, body: TagsIn, qa: User = Depends(_qa), db: Session = Depends(db_session)) -> dict[str, Any]:
#     """QA confirms or changes an auto draft and submits it. Only the first QA to open it may submit."""
#     e = db.get(Evaluation, evaluation_id)
#     if e is None or e.source != "auto" or e.status != "draft":
#         raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no draft to validate")
#     if not _is_member(db, e.project_id, qa.id):
#         raise HTTPException(status.HTTP_404_NOT_FOUND, detail="evaluation not found")
#     if e.qa_user_id not in (None, qa.id):
#         raise HTTPException(status.HTTP_409_CONFLICT, detail="draft is held by another QA user")
#     e.qa_user_id, e.tags, e.notes, e.status = qa.id, _check_tags(body.tags), body.notes, "submitted"
#     db.commit()
#     return _eval_out(e)


@router.post("/api/qa/evaluations/{evaluation_id}/resubmit")
def qa_resubmit(evaluation_id: int, body: TagsIn, qa: User = Depends(_qa), db: Session = Depends(db_session)) -> dict[str, Any]:
    e = db.get(Evaluation, evaluation_id)
    if e is None or e.qa_user_id != qa.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="evaluation not found")
    if e.status != "rejected":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"evaluation is {e.status}, not rejected")
    e.tags, e.notes, e.status, e.reject_reason = _check_tags(body.tags), body.notes, "submitted", None
    db.commit()
    return _eval_out(e)


@router.get("/api/qa/evaluations")
def qa_my_evaluations(qa: User = Depends(_qa), db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    rows = db.execute(select(Evaluation).where(Evaluation.qa_user_id == qa.id).order_by(Evaluation.id.desc())).scalars()
    return [_eval_out(e) for e in rows]
