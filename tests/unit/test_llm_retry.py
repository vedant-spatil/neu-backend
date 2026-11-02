"""The LLM client retries a Groq tool_use_failed answer with a larger token budget (services/llm.complete_json). No network:
the HTTP call is replaced."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import llm


class _Resp:
    def __init__(self, status: int, body: dict | None = None, text: str = "") -> None:
        self.status_code = status
        self.is_success = 200 <= status < 300
        self._body = body or {}
        self.text = text

    def json(self) -> dict:
        return self._body


def _ok_body() -> dict:
    return {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "typology_out", "arguments": '{"typologies": []}'}}]}}]}


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setattr(llm, "get_settings", lambda: SimpleNamespace(llm_provider="groq", groq_api_key="test-key"))
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    db = MagicMock()
    db.get.return_value = None
    return db


def _call(db, max_tokens: int = 800) -> dict:
    return llm.complete_json(db, model="m", system="s", user="u", schema_name="typology_out",
                             schema={"type": "object"}, max_completion_tokens=max_tokens)


def test_tool_use_failed_is_retried_with_a_larger_budget(monkeypatch, groq) -> None:
    budgets: list[int] = []
    responses = [_Resp(400, text='{"error": {"code": "tool_use_failed"}}'), _Resp(200, _ok_body())]

    def fake_post(req):
        budgets.append(req.body["max_completion_tokens"])
        return responses.pop(0)

    monkeypatch.setattr(llm, "_post", fake_post)
    assert _call(groq) == {"typologies": []}
    assert budgets == [800, 1200]


def test_tool_use_failed_gives_up_after_three_calls(monkeypatch, groq) -> None:
    calls = []

    def fake_post(req):
        calls.append(req)
        return _Resp(400, text="tool_use_failed")

    monkeypatch.setattr(llm, "_post", fake_post)
    with pytest.raises(llm.LlmError, match="tool_use_failed"):
        _call(groq)
    assert len(calls) == 3


def test_other_bad_requests_are_not_retried(monkeypatch, groq) -> None:
    calls = []

    def fake_post(req):
        calls.append(req)
        return _Resp(400, text="invalid api key")

    monkeypatch.setattr(llm, "_post", fake_post)
    with pytest.raises(llm.LlmError, match="rejected"):
        _call(groq)
    assert len(calls) == 1
