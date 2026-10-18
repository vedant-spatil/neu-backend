"""CSV upload, preview and validation (ETL stages 1 and 2). Loading stays in app.ingest.load.

Admin only. Uploads stream to disk, never into memory, and are stored under their SHA-256 so the
same file cannot be imported twice under two names."""

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session, require_role
from app.db.models import Import, User

router = APIRouter(prefix="/api/imports", tags=["imports"])

UPLOAD_DIR = Path(__file__).resolve().parents[3] / "data" / "uploads"
MAX_BYTES = 2 * 1024**3  # 2 GB
REQUIRED = [
    "Timestamp", "From Bank", "Account", "To Bank", "Account",
    "Amount Received", "Receiving Currency", "Amount Paid", "Payment Currency",
    "Payment Format", "Is Laundering",
]


_admin = require_role("admin")


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


def _validate_file(path: Path) -> dict[str, Any]:
    """Row-level checks in chunks. Counts problems per type; never prints raw rows."""
    report: dict[str, Any] = {"rows": 0, "missing_columns": [], "errors": {}, "non_usd_rows": 0}
    header = pd.read_csv(path, nrows=0).columns.tolist()
    report["missing_columns"] = [c for c in REQUIRED if c not in header]
    if report["missing_columns"]:
        return report
    errors = {"bad_timestamp": 0, "bad_amount": 0, "null_account": 0, "bad_label": 0}
    for chunk in pd.read_csv(path, chunksize=250_000, dtype=str, keep_default_na=False):
        report["rows"] += len(chunk)
        ts = pd.to_datetime(chunk["Timestamp"], format="%Y/%m/%d %H:%M", errors="coerce")
        errors["bad_timestamp"] += int(ts.isna().sum())
        for col in ("Amount Received", "Amount Paid"):
            amt = pd.to_numeric(chunk[col], errors="coerce")
            errors["bad_amount"] += int(amt.isna().sum())
        errors["null_account"] += int((chunk["Account"].str.strip() == "").sum())
        errors["bad_label"] += int((~chunk["Is Laundering"].isin(["0", "1"])).sum())
        report["non_usd_rows"] += int(
            ((chunk["Receiving Currency"] != "US Dollar") | (chunk["Payment Currency"] != "US Dollar")).sum()
        )
    report["errors"] = {k: v for k, v in errors.items() if v}
    return report


@router.post("", status_code=status.HTTP_201_CREATED)
def upload(
    file: UploadFile,
    admin: User = Depends(_admin),
    db: Session = Depends(db_session),
) -> dict[str, Any]:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="CSV only")
    path, digest, size = _store(file)
    existing = db.execute(select(Import).where(Import.sha256 == digest)).scalar_one_or_none()
    if existing is not None:
        return {"id": existing.id, "status": existing.status, "duplicate": True, "report": existing.report}
    imp = Import(filename=file.filename or "upload.csv", sha256=digest, size_bytes=size, uploaded_by=admin.id)
    db.add(imp)
    db.commit()
    return {"id": imp.id, "status": imp.status, "sha256": digest, "size_bytes": size, "duplicate": False}


@router.post("/{import_id}/validate")
def validate(import_id: int, _: User = Depends(_admin), db: Session = Depends(db_session)) -> dict[str, Any]:
    imp = db.get(Import, import_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="import not found")
    path = UPLOAD_DIR / f"{imp.sha256}.csv"
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, detail="uploaded file missing")
    report = _validate_file(path)
    bad = bool(report["missing_columns"]) or bool(report["errors"])
    imp.status = "rejected" if bad else "validated"
    imp.report = report
    db.commit()
    return {"id": imp.id, "status": imp.status, "report": report}


@router.get("/{import_id}")
def get_import(import_id: int, _: User = Depends(_admin), db: Session = Depends(db_session)) -> dict[str, Any]:
    imp = db.get(Import, import_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="import not found")
    return {"id": imp.id, "filename": imp.filename, "status": imp.status, "size_bytes": imp.size_bytes,
            "report": imp.report, "created_at": imp.created_at.isoformat()}
