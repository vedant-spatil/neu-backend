import pytest
from fastapi import HTTPException

from app.api.deps import require_role
from app.db.models import User
from app.tasks.assess import compute_tags


def _user(role: str) -> User:
    return User(id=1, email="x@example.com", password_hash="", role=role, is_active=True)


@pytest.mark.parametrize("role,allowed", [("admin", True), ("qa", False), ("investigator", False)])
def test_admin_only_gate(role, allowed):
    check = require_role("admin")
    if allowed:
        assert check(_user(role)).role == role
    else:
        with pytest.raises(HTTPException) as e:
            check(_user(role))
        assert e.value.status_code == 403


def test_qa_gate_rejects_investigator():
    check = require_role("qa")
    with pytest.raises(HTTPException):
        check(_user("investigator"))
    assert check(_user("qa")).role == "qa"


def test_tags_anomaly_and_case_found():
    assert compute_tags("awaiting_review", 0.6, ["structuring"], 0) == ["anomaly", "case_found", "typology:structuring"]


def test_tags_sanctions_hit_only():
    assert compute_tags("closed", 0.1, [], 2) == ["sanctions_hit"]


def test_tags_no_anomaly_when_nothing_fires():
    assert compute_tags("closed", 0.1, [], 0) == ["no_anomaly"]


@pytest.mark.parametrize("tag,ok", [
    ("anomaly", True), ("case_found", True), ("sanctions_hit", True), ("no_anomaly", True),
    ("typology:structuring", True), ("typology:", False), ("random", False), ("Anomaly", False),
])
def test_fixed_tag_list(tag, ok):
    from app.tasks.assess import is_allowed_tag

    assert is_allowed_tag(tag) is ok
