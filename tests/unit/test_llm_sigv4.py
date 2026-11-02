"""Bedrock calls signed with IAM (SigV4) when no bearer key is set (plan phase 2). No network: _post is replaced."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from app.services import llm


def _bedrock_settings(bearer: str = "") -> SimpleNamespace:
    return SimpleNamespace(llm_provider="bedrock", bedrock_api_key=bearer, bedrock_region="ap-south-1",
                           groq_api_key="")


def _tool_answer() -> httpx.Response:
    body = {"output": {"message": {"content": [
        {"toolUse": {"name": "typology_out", "input": {"typologies": []}}}]}}}
    return httpx.Response(200, json=body)


@pytest.fixture
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDTEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret-test")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


def _call(db) -> dict:
    return llm.complete_json(db, model="anthropic.claude-test", system="s", user="u", schema_name="typology_out",
                             schema={"type": "object"}, max_completion_tokens=100)


def test_signs_with_iam_when_no_bearer_key(monkeypatch, aws_creds) -> None:
    monkeypatch.setattr(llm, "get_settings", lambda: _bedrock_settings(bearer=""))
    sent: list[llm.Request] = []

    def fake_post(req):
        sent.append(req)
        return _tool_answer()

    monkeypatch.setattr(llm, "_post", fake_post)
    db = MagicMock()
    db.get.return_value = None

    assert _call(db) == {"typologies": []}
    req = sent[0]
    assert req.headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDTEST/")
    assert "X-Amz-Date" in req.headers
    assert req.url.startswith("https://bedrock-runtime.ap-south-1.amazonaws.com/model/")
    assert req.url.endswith("/converse")
    # The signature covers the exact bytes sent.
    assert req.payload == json.dumps(req.body).encode()


def test_bearer_key_still_wins_when_set(monkeypatch, aws_creds) -> None:
    monkeypatch.setattr(llm, "get_settings", lambda: _bedrock_settings(bearer="bedrock-test"))
    sent: list[llm.Request] = []

    def fake_post(req):
        sent.append(req)
        return _tool_answer()

    monkeypatch.setattr(llm, "_post", fake_post)
    db = MagicMock()
    db.get.return_value = None
    _call(db)
    assert sent[0].headers["Authorization"] == "Bearer bedrock-test"
    assert sent[0].payload is None


def test_no_credentials_and_no_key_is_a_clear_error(monkeypatch) -> None:
    monkeypatch.setattr(llm, "get_settings", lambda: _bedrock_settings(bearer=""))
    monkeypatch.setattr(llm.botocore.session, "get_session",
                        lambda: SimpleNamespace(get_credentials=lambda: None))
    monkeypatch.setattr(llm, "_post", lambda req: pytest.fail("no call expected"))
    db = MagicMock()
    db.get.return_value = None
    with pytest.raises(llm.LlmError, match="no AWS credentials"):
        _call(db)
