import pytest
from pydantic import ValidationError

from app.api.routers.cases import DecisionIn


def test_reason_minimum_length():
    with pytest.raises(ValidationError):
        DecisionIn(action="close", reason="too short", reviewed=True)


def test_action_enum_only_approve_or_close():
    with pytest.raises(ValidationError):
        DecisionIn(action="escalate", reason="x" * 25, reviewed=True)


def test_valid_decision():
    d = DecisionIn(action="approve", reason="x" * 25, reviewed=True, typologies=["structuring"])
    assert d.action == "approve"
