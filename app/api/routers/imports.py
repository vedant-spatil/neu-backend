"""CSV upload (ETL stage 1), column mapping and sync. Loading and triage run in app.services.sync.

Admin only. Uploads stream to disk, never into memory, and are stored under their SHA-256 so the same file cannot
be imported twice under two names. The flow is: upload, read the columns, map them to the schema (names that match
are filled in automatically), then sync. Sync validates the file, loads it and opens cases for the top alerts."""

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_role
from app.db.models import Import, User
from app.ingest.mapping import SCHEMA, auto_map, mapping_problems, read_header
from app.services.sync import BUSY_STATUSES, can_reset, is_live, start_sync

router = APIRouter(prefix="/api/imports", tags=["imports"])

UPLOAD_DIR = Path(__file__).resolve().parents[3] / "data" / "uploads"
MAX_BYTES = 2 * 1024**3  # 2 GB

_admin = require_role("admin")


class SyncIn(BaseModel):
    mapping: dict[str, str]  # schema field -> CSV column


def _store(upload: UploadFile) -> tuple[Path, str, int]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = UPLOAD_DIR / "incoming.part"
    digest = hashlib.sha256()
    size = 0
    with tmp.open("wb") as fh:
        while chunk := upload.file.read(8 * 1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                tmp.unlink(missing_ok=True)
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="file over 2 GB")
            digest.update(chunk)
            fh.write(chunk)
    final = UPLOAD_DIR / f"{digest.hexdigest()}.csv"
    if final.exists():
        tmp.unlink()
    else:
        tmp.rename(final)
    return final, digest.hexdigest(), size


def _get_import(db: Session, import_id: int) -> Import:
    imp = db.get(Import, import_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="import not found")
    return imp


def _file_path(imp: Import) -> Path:
    path = UPLOAD_DIR / f"{imp.sha256}.csv"
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, detail="uploaded file missing")
    return path


@router.post("", status_code=status.HTTP_201_CREATED)
def upload(
    file: UploadFile,
    admin: User = Depends(_admin),
    db: Session = Depends(db_session),
) -> dict[str, Any]:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="CSV only")
    _, digest, size = _store(file)
    existing = db.execute(select(Import).where(Import.sha256 == digest)).scalar_one_or_none()
    if existing is not None:
        return {"id": existing.id, "status": existing.status, "duplicate": True, "report": existing.report}
    imp = Import(filename=file.filename or "upload.csv", sha256=digest, size_bytes=size, uploaded_by=admin.id)
    db.add(imp)
    db.commit()
    return {"id": imp.id, "status": imp.status, "sha256": digest, "size_bytes": size, "duplicate": False}


@router.get("/{import_id}/columns")
def columns(import_id: int, _: User = Depends(_admin), db: Session = Depends(db_session)) -> dict[str, Any]:
    """The CSV's headers, the automatic mapping, and the schema fields to map."""
    imp = _get_import(db, import_id)
    header = read_header(_file_path(imp))
    return {
        "columns": header,
        "auto_mapping": auto_map(header),
        "schema": [{"field": f, "required": req} for f, req in SCHEMA.items()],
    }


@router.post("/{import_id}/sync", status_code=status.HTTP_202_ACCEPTED)
def sync(import_id: int, body: SyncIn, _: User = Depends(_admin), db: Session = Depends(db_session)) -> dict[str, Any]:
    imp = _get_import(db, import_id)
    if imp.status in BUSY_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"import is {imp.status}")
    path = _file_path(imp)
    problems = mapping_problems(body.mapping, read_header(path))
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problems)
    imp.status = "validating"
    imp.report = {"mapping": body.mapping}
    db.commit()
    start_sync(imp.id, path, body.mapping)
    return {"id": imp.id, "status": imp.status}


@router.post("/{import_id}/reset")
def reset(import_id: int, _: User = Depends(_admin), db: Session = Depends(db_session)) -> dict[str, Any]:
    """Marks a busy import as failed so it can be synced again. Only allowed when no sync thread is running for it.
    Data already written is not rolled back; the next sync replaces all of it."""
    imp = _get_import(db, import_id)
    reason = can_reset(imp.status, is_live(import_id))
    if reason is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=reason)
    imp.status = "failed"
    imp.report = {**(imp.report or {}), "error": "reset by admin"}
    db.commit()
    return {"id": imp.id, "status": imp.status}


@router.get("/{import_id}")
def get_import(import_id: int, _: User = Depends(_admin), db: Session = Depends(db_session)) -> dict[str, Any]:
    imp = _get_import(db, import_id)
    return {"id": imp.id, "filename": imp.filename, "status": imp.status, "size_bytes": imp.size_bytes,
            "report": imp.report, "created_at": imp.created_at.isoformat()}
