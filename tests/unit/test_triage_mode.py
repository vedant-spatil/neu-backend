"""Triage mode switch: settings are refused when xgb has no run, and a case falls back to its rule score when the model
has no score for the account-day."""

from types import SimpleNamespace

import pytest

from app.agents import agents
from app.config import Settings


def test_xgb_without_a_run_is_refused() -> None:
    with pytest.raises(ValueError, match="TRIAGE_MODEL_RUN"):
        Settings(triage_model="xgb", triage_model_run="").check_triage()


def test_unknown_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="rules or xgb"):
        Settings(triage_model="nope").check_triage()


def test_rules_mode_needs_no_run() -> None:
    Settings(triage_model="rules", triage_model_run="").check_triage()


def _state() -> dict:
    return {"case_id": 1, "account_id": "A1", "alert_ts": "2022-09-02T10:00:00", "risk_score": 0.7,
            "alert_rules": ["R2_fan_out"]}


def _patch(monkeypatch, model_score):
    events: list[dict] = []
    cfg = SimpleNamespace(triage_model="xgb", triage_model_run="run1", triage_threshold=0.6,
                          triage_score_threshold=0.6)
    monkeypatch.setattr(agents, "get_settings", lambda: cfg)
    monkeypatch.setattr(agents, "_model_score", lambda db, state: model_score)
    monkeypatch.setattr(agents, "write_event", lambda db, case_id, node, kind, payload: events.append(payload))
    return events


def test_xgb_score_decides_when_present(monkeypatch) -> None:
    events = _patch(monkeypatch, (0.2, ["amount"]))
    out = agents.triage(None, _state())
    assert out == {"status": "closed_low_risk"}
    assert events[-1]["model_score"] == 0.2
    assert "model_missing" not in events[-1]


def test_missing_xgb_score_falls_back_to_the_rule_score(monkeypatch) -> None:
    events = _patch(monkeypatch, None)
    out = agents.triage(None, _state())
    assert out == {"status": "open"}  # rule score 0.7 is above the threshold
    assert events[-1]["model_missing"] is True
    assert events[-1]["score"] == 0.7
