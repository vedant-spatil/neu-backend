"""Unit tests for the LLM client in app/services/llm.py. No network: _post is replaced."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.services import llm

GROQ_MODEL = "llama-3.3-70b-versatile"
BEDROCK_MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0"
SCHEMA = {"type": "object", "properties": {"typology": {"type": "string"}}, "required": ["typology"]}


class FakeSession:
    """Only what complete_json uses: get, add, commit."""

    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    def get(self, _model: type, key: str) -> Any:
        return self.rows.get(key)

    def add(self, row: Any) -> None:
        self.rows[row.key] = row

    def commit(self) -> None:
        pass


def _groq_ok(payload: dict[str, Any]) -> httpx.Response:
    body = {"choices": [{"message": {"role": "assistant", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "typology_out", "arguments": json.dumps(payload)}}]}}]}
    return httpx.Response(200, json=body)


def _bedrock_ok(payload: dict[str, Any]) -> httpx.Response:
    body = {"output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "t1", "name": "typology_out", "input": payload}}]}}}
    return httpx.Response(200, json=body)


def _settings(provider: str = "groq", **overrides: str) -> SimpleNamespace:
    base = {"llm_provider": provider, "groq_api_key": "groq-test", "bedrock_api_key": "bedrock-test",
            "bedrock_region": "ap-south-1"}
    base.update(overrides)
    return SimpleNamespace(**base)


def _call(db: FakeSession, model: str = GROQ_MODEL) -> dict[str, Any]:
    return llm.complete_json(db, model=model, system="sys", user="usr",
                             schema_name="typology_out", schema=SCHEMA, max_completion_tokens=200)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)


@pytest.fixture
def use_provider(monkeypatch):
    """Sets the provider for one test: use_provider("bedrock")."""
    def set_provider(provider: str = "groq", **overrides: str) -> None:
        monkeypatch.setattr(llm, "get_settings", lambda: _settings(provider, **overrides))
    set_provider()
    return set_provider


def test_groq_request_forces_the_schema_tool(monkeypatch, use_provider):
    seen: dict[str, Any] = {}

    def fake_post(req: llm.Request) -> httpx.Response:
        seen["req"] = req
        return _groq_ok({"typology": "FAN-OUT"})

    monkeypatch.setattr(llm, "_post", fake_post)
    assert _call(FakeSession()) == {"typology": "FAN-OUT"}

    req = seen["req"]
    assert req.url == "https://api.groq.com/openai/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer groq-test"
    assert req.body["model"] == GROQ_MODEL
    assert req.body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    assert req.body["tools"][0]["function"]["name"] == "typology_out"
    assert req.body["tools"][0]["function"]["parameters"] == SCHEMA
    assert req.body["tool_choice"] == {"type": "function", "function": {"name": "typology_out"}}
    assert req.body["temperature"] == 0
    assert req.body["max_completion_tokens"] == 200


def test_bedrock_request_is_used_when_the_provider_is_bedrock(monkeypatch, use_provider):
    use_provider("bedrock")
    seen: dict[str, Any] = {}

    def fake_post(req: llm.Request) -> httpx.Response:
        seen["req"] = req
        return _bedrock_ok({"typology": "CYCLE"})

    monkeypatch.setattr(llm, "_post", fake_post)
    assert _call(FakeSession(), model=BEDROCK_MODEL) == {"typology": "CYCLE"}

    req = seen["req"]
    assert req.url == ("https://bedrock-runtime.ap-south-1.amazonaws.com/model/"
                       "anthropic.claude-haiku-4-5-20251001-v1%3A0/converse")
    assert req.headers["Authorization"] == "Bearer bedrock-test"
    assert req.body["toolConfig"]["toolChoice"] == {"tool": {"name": "typology_out"}}
    assert req.body["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"] == {"json": SCHEMA}


def test_the_same_prompt_is_cached_per_provider(monkeypatch, use_provider):
    calls = {"n": 0}

    def fake_post(req):
        calls["n"] += 1
        return _groq_ok({"typology": "CYCLE"})

    monkeypatch.setattr(llm, "_post", fake_post)
    db = FakeSession()
    _call(db)
    _call(db)
    assert calls["n"] == 1  # second identical call is served from the cache


def test_switching_provider_does_not_reuse_the_other_providers_answer(monkeypatch, use_provider):
    calls = {"n": 0}

    def fake_post(req):
        calls["n"] += 1
        return _groq_ok({"typology": "CYCLE"}) if req.url.startswith("https://api.groq") else _bedrock_ok(
            {"typology": "CYCLE"})

    monkeypatch.setattr(llm, "_post", fake_post)
    db = FakeSession()
    _call(db)
    use_provider("bedrock")
    _call(db, model=BEDROCK_MODEL)
    assert calls["n"] == 2


def test_unknown_provider_is_refused_before_any_call(monkeypatch, use_provider):
    use_provider("openai")
    monkeypatch.setattr(llm, "_post", lambda req: pytest.fail("no call expected"))
    with pytest.raises(llm.LlmError, match="unknown LLM_PROVIDER"):
        _call(FakeSession())


def test_missing_key_is_refused_before_any_call(monkeypatch, use_provider):
    use_provider("groq", groq_api_key="")
    monkeypatch.setattr(llm, "_post", lambda req: pytest.fail("no call expected"))
    with pytest.raises(llm.LlmError, match="GROQ_API_KEY not set"):
        _call(FakeSession())


def test_retries_throttling_then_succeeds(monkeypatch, use_provider):
    responses = [httpx.Response(429, text="rate limited"), httpx.Response(503, text="busy"),
                 _groq_ok({"typology": "NONE"})]
    monkeypatch.setattr(llm, "_post", lambda req: responses.pop(0))
    assert _call(FakeSession()) == {"typology": "NONE"}


def test_throttling_waits_longer_than_other_errors():
    assert llm._wait_seconds(httpx.Response(429), 0) > llm._wait_seconds(httpx.Response(503), 0)


def test_model_not_available_fails_without_retry(monkeypatch, use_provider):
    calls = {"n": 0}

    def fake_post(req):
        calls["n"] += 1
        return httpx.Response(403, text="model not available to this account")

    monkeypatch.setattr(llm, "_post", fake_post)
    with pytest.raises(llm.LlmError, match="HTTP 403"):
        _call(FakeSession())
    assert calls["n"] == 1


def test_missing_tool_call_is_an_error(monkeypatch, use_provider):
    monkeypatch.setattr(llm, "_post", lambda req: httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": "free text, no tool"}}]}))
    with pytest.raises(llm.LlmError, match="no tool call"):
        _call(FakeSession())


def test_empty_model_name_is_refused_before_any_call(monkeypatch, use_provider):
    monkeypatch.setattr(llm, "_post", lambda req: pytest.fail("no call expected"))
    with pytest.raises(llm.LlmError, match="model not set"):
        _call(FakeSession(), model="")
