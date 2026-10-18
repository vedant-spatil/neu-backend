"""Live-stack tests. Need `docker compose up -d` and the API on API_URL (default :8001).
Skipped when the stack is not reachable, so unit runs stay fast."""

import os

import httpx
import pytest
from sqlalchemy import create_engine, text

from app.config import get_settings

API_URL = os.environ.get("API_URL", "http://localhost:8001")
INVESTIGATOR = ("investigator1@example.com", os.environ.get("TEST_INVESTIGATOR_PASSWORD", ""))
ADMIN = ("admin@example.com", os.environ.get("TEST_ADMIN_PASSWORD", ""))


def _up() -> bool:
    try:
        return httpx.get(f"{API_URL}/api/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _up(), reason="live API not reachable")


def _token(email: str, password: str) -> str:
    r = httpx.post(f"{API_URL}/api/auth/login", json={"email": email, "password": password}, timeout=10)
    if r.status_code != 200:
        pytest.skip(f"login failed for {email} (set TEST_*_PASSWORD)")
    return r.json()["access_token"]


def test_health_reports_all_dependencies():
    body = httpx.get(f"{API_URL}/api/health", timeout=10).json()
    assert body["postgres"] == "ok" and body["neo4j"] == "ok" and body["yente"] == "ok"


def test_protected_routes_reject_missing_token():
    assert httpx.get(f"{API_URL}/api/alerts", timeout=10).status_code == 401
    assert httpx.get(f"{API_URL}/api/dashboard", timeout=10).status_code == 401


def test_alerts_are_paginated():
    tok = _token(*INVESTIGATOR)
    r = httpx.get(f"{API_URL}/api/alerts", params={"offset": 0, "limit": 5},
                  headers={"Authorization": f"Bearer {tok}"}, timeout=30).json()
    assert set(r) == {"total", "offset", "limit", "items"}
    assert len(r["items"]) <= 5
    scores = [a["risk_score"] for a in r["items"]]
    assert scores == sorted(scores, reverse=True)


def test_cases_list_is_paginated_and_filters_by_status():
    tok = _token(*INVESTIGATOR)
    headers = {"Authorization": f"Bearer {tok}"}
    r = httpx.get(f"{API_URL}/api/cases", params={"offset": 0, "limit": 5}, headers=headers, timeout=30).json()
    assert set(r) == {"total", "offset", "limit", "items"}
    assert len(r["items"]) <= 5
    ids = [c["id"] for c in r["items"]]
    assert ids == sorted(ids, reverse=True)
    awaiting = httpx.get(f"{API_URL}/api/cases", params={"status": "awaiting_review"},
                         headers=headers, timeout=30).json()
    assert all(c["status"] == "awaiting_review" for c in awaiting["items"])


def test_dashboard_shape():
    tok = _token(*INVESTIGATOR)
    r = httpx.get(f"{API_URL}/api/dashboard", headers={"Authorization": f"Bearer {tok}"}, timeout=60).json()
    for key in ("cases_by_status", "decisions_by_action", "alerts_above_threshold_by_rule",
                "top_accounts_by_alerts", "score_histogram", "failed_cases"):
        assert key in r


def test_import_is_admin_only():
    tok = _token(*INVESTIGATOR)
    r = httpx.post(f"{API_URL}/api/imports", headers={"Authorization": f"Bearer {tok}"},
                   files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}, timeout=30)
    assert r.status_code == 403


def test_case_events_are_append_only():
    engine = create_engine(get_settings().database_url)
    with engine.begin() as conn:
        row = conn.execute(text("SELECT id FROM case_events ORDER BY id LIMIT 1")).first()
        if row is None:
            pytest.skip("no case_events yet")
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("UPDATE case_events SET kind = 'x' WHERE id = :i"), {"i": row[0]})


def test_investigator_cannot_create_projects():
    tok = _token(*INVESTIGATOR)
    r = httpx.post(f"{API_URL}/api/projects", json={"name": "nope", "auto_validate": False},
                   headers={"Authorization": f"Bearer {tok}"}, timeout=20)
    assert r.status_code == 403
