"""Case runs on AgentCore (plan phase 2): settings, the API-side client, the dispatcher in services/cases.run_case, and the
agent's handler. No AWS and no database: every outside call is replaced."""

import io
import json
from types import SimpleNamespace

import pytest

from app.agentcore_app import handle_case
from app.config import Settings
from app.services import agentcore, cases

ARN = "arn:aws:bedrock-agentcore:ap-south-1:123456789012:runtime/aml_case_agent-abc"


class _FakeAgentCore:
    def __init__(self, answer: dict):
        self.answer = answer
        self.calls: list[dict] = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": io.BytesIO(json.dumps(self.answer).encode())}


@pytest.fixture
def agent_settings(monkeypatch):
    monkeypatch.setattr(agentcore, "get_settings",
                        lambda: SimpleNamespace(agentcore_runtime_arn=ARN, agentcore_region=None,
                                                bedrock_region="ap-south-1"))


def test_agentcore_runner_needs_the_runtime_arn() -> None:
    with pytest.raises(ValueError, match="AGENTCORE_RUNTIME_ARN"):
        Settings(case_runner="agentcore", agentcore_runtime_arn="").check_case_runner()


def test_unknown_case_runner_is_refused() -> None:
    with pytest.raises(ValueError, match="CASE_RUNNER"):
        Settings(case_runner="lambda").check_case_runner()


def test_local_runner_needs_nothing() -> None:
    Settings(case_runner="local", agentcore_runtime_arn="").check_case_runner()


def test_invoke_case_sends_the_payload_and_returns_the_agent_answer(agent_settings) -> None:
    client = _FakeAgentCore({"status": "ok", "case_id": 7})
    payload = {"case_id": 7, "attempt": 0, "alert_id": 1}
    result = agentcore.invoke_case(payload, client=client)
    call = client.calls[0]
    assert call["agentRuntimeArn"] == ARN
    assert json.loads(call["payload"]) == payload
    assert len(call["runtimeSessionId"]) >= 33  # AgentCore's minimum session id length
    assert result == {"status": "ok", "case_id": 7}


def test_invoke_case_raises_when_the_agent_reports_an_error(agent_settings) -> None:
    client = _FakeAgentCore({"status": "error", "error": "Groq: rate limit"})
    with pytest.raises(RuntimeError, match="rate limit"):
        agentcore.invoke_case({"case_id": 7, "attempt": 0}, client=client)


def _args() -> tuple:
    return (7, 1, "A", "2022-09-01T10:00:00", ["R2_fan_out"], 0.8, [1, 2], 0)


def test_run_case_uses_agentcore_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(cases, "get_settings", lambda: SimpleNamespace(case_runner="agentcore"))
    monkeypatch.setattr(cases, "_clear_members_changed", lambda case_id: None)
    monkeypatch.setattr(cases, "invalidate", lambda: None)
    sent: list[dict] = []
    monkeypatch.setattr(cases, "invoke_case", lambda payload: sent.append(payload) or {"status": "ok"})
    monkeypatch.setattr(cases, "run_case_local", lambda *a, **k: pytest.fail("local run must not happen"))

    cases.run_case(*_args())

    assert sent == [{"case_id": 7, "alert_id": 1, "account_id": "A", "alert_ts": "2022-09-01T10:00:00",
                     "rules": ["R2_fan_out"], "risk": 0.8, "txn_ids": [1, 2], "attempt": 0}]


def test_run_case_records_an_agentcore_failure_on_the_case(monkeypatch) -> None:
    monkeypatch.setattr(cases, "get_settings", lambda: SimpleNamespace(case_runner="agentcore"))
    monkeypatch.setattr(cases, "_clear_members_changed", lambda case_id: None)
    monkeypatch.setattr(cases, "invalidate", lambda: None)

    def boom(payload):
        raise RuntimeError("runtime not reachable")

    failed: list[tuple] = []
    monkeypatch.setattr(cases, "invoke_case", boom)
    monkeypatch.setattr(cases, "_mark_failed", lambda case_id, error: failed.append((case_id, error)))

    cases.run_case(*_args())

    assert failed and failed[0][0] == 7
    assert "runtime not reachable" in failed[0][1]


def test_run_case_stays_local_by_default(monkeypatch) -> None:
    monkeypatch.setattr(cases, "get_settings", lambda: SimpleNamespace(case_runner="local"))
    monkeypatch.setattr(cases, "_clear_members_changed", lambda case_id: None)
    local: list[tuple] = []
    monkeypatch.setattr(cases, "run_case_local", lambda *a: local.append(a))
    monkeypatch.setattr(cases, "invoke_case", lambda payload: pytest.fail("AgentCore must not be called"))

    cases.run_case(*_args())

    assert local and local[0][0] == 7


def test_agent_handler_reports_missing_fields() -> None:
    result = handle_case({"case_id": 7})
    assert result["status"] == "error"
    assert "alert_id" in result["error"]


def test_agent_handler_runs_the_local_workflow(monkeypatch) -> None:
    import app.agentcore_app as agent_app

    seen: list[tuple] = []
    monkeypatch.setattr(agent_app, "run_case_local", lambda *a: seen.append(a))
    payload = {"case_id": 7, "alert_id": 1, "account_id": "A", "alert_ts": "2022-09-01T10:00:00",
               "rules": ["R2_fan_out"], "risk": 0.8, "txn_ids": [1, 2], "attempt": 0}
    assert handle_case(payload) == {"status": "ok", "case_id": 7}
    assert seen[0][0] == 7 and seen[0][6] == [1, 2]


def test_agent_handler_returns_workflow_errors_as_a_result(monkeypatch) -> None:
    import app.agentcore_app as agent_app

    def fail(*a):
        raise RuntimeError("Bedrock: model not available")

    monkeypatch.setattr(agent_app, "run_case_local", fail)
    payload = {"case_id": 7, "alert_id": 1, "account_id": "A", "alert_ts": "2022-09-01T10:00:00",
               "rules": [], "risk": 0.8, "txn_ids": [], "attempt": 0}
    result = handle_case(payload)
    assert result["status"] == "error"
