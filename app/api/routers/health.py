import httpx
from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.db.session import engine

router = APIRouter(prefix="/api", tags=["health"])


def _check_db() -> str:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # noqa: BLE001 - report any failure
        return f"error: {type(exc).__name__}"


def _check_neo4j() -> str:
    from neo4j import GraphDatabase

    s = get_settings()
    try:
        with GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password)) as drv:
            drv.verify_connectivity()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}"


def _check_yente() -> str:
    s = get_settings()
    try:
        r = httpx.get(f"{s.yente_url}/healthz", timeout=5)
        return "ok" if r.status_code == 200 else f"http {r.status_code}"
    except httpx.HTTPError as exc:
        return f"error: {type(exc).__name__}"


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "postgres": _check_db(), "neo4j": _check_neo4j(), "yente": _check_yente()}
