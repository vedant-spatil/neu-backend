"""Unit tests for the Bedrock client in app/services/llm.py. No network: _post is replaced."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services import llm

MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0"
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


def _ok(payload: dict[str, Any]) -> httpx.Response:
    body = {"output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "t1", "name": "typology_out", "input": payload}}]}}}
    return httpx.Response(200, json=body)


def _call(db: FakeSession) -> dict[str, Any]:
    return llm.complete_json(db, model=MODEL, system="sys", user="usr",
                             schema_name="typology_out", schema=SCHEMA, max_completion_tokens=200)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)


def test_request_forces_the_schema_tool_and_encodes_the_model_id(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_post(url: str, body: dict[str, Any]) -> httpx.Response:
        seen["url"], seen["body"] = url, body
        return _ok({"typology": "FAN-OUT"})

    monkeypatch.setattr(llm, "_post", fake_post)
    assert _call(FakeSession()) == {"typology": "FAN-OUT"}

    assert seen["url"].endswith("/model/anthropic.claude-haiku-4-5-20251001-v1%3A0/converse")
    assert seen["url"].startswith("https://bedrock-runtime.ap-south-1.amazonaws.com/")
    tool = seen["body"]["toolConfig"]["tools"][0]["toolSpec"]
    assert tool["name"] == "typology_out"
    assert tool["inputSchema"] == {"json": SCHEMA}
    assert seen["body"]["toolConfig"]["toolChoice"] == {"tool": {"name": "typology_out"}}
    assert seen["body"]["inferenceConfig"]["temperature"] == 0


def test_second_identical_call_is_served_from_cache(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, body):
        calls["n"] += 1
        return _ok({"typology": "CYCLE"})

    monkeypatch.setattr(llm, "_post", fake_post)
    db = FakeSession()
    _call(db)
    _call(db)
    assert calls["n"] == 1


def test_retries_throttling_then_succeeds(monkeypatch):
    responses = [httpx.Response(429, text="throttled"), httpx.Response(503, text="busy"),
                 _ok({"typology": "NONE"})]
    monkeypatch.setattr(llm, "_post", lambda url, body: responses.pop(0))
    assert _call(FakeSession()) == {"typology": "NONE"}


def test_model_not_enabled_fails_without_retry(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, body):
        calls["n"] += 1
        return httpx.Response(403, text="model not enabled in this account")

    monkeypatch.setattr(llm, "_post", fake_post)
    with pytest.raises(llm.LlmError, match="HTTP 403"):
        _call(FakeSession())
    assert calls["n"] == 1


def test_missing_tool_block_is_an_error(monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda url, body: httpx.Response(
        200, json={"output": {"message": {"content": [{"text": "free text, no tool"}]}}}))
    with pytest.raises(llm.LlmError, match="no toolUse block"):
        _call(FakeSession())


def test_empty_model_id_is_refused_before_any_call(monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda url, body: pytest.fail("no call expected"))
    with pytest.raises(llm.LlmError, match="model ID not set"):
        llm.complete_json(FakeSession(), model="", system="s", user="u",
                          schema_name="typology_out", schema=SCHEMA, max_completion_tokens=10)
