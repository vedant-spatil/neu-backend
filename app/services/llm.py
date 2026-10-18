"""The only module that talks to an LLM provider (plan 16.12).

Amazon Bedrock, Converse API, called over HTTPS with the Bedrock API key (bearer token).
Structured output: one tool whose input schema is the caller's JSON schema, forced with toolChoice.
Every call is cached by a hash of (model, system, user, schema), so reruns cost no tokens.
"""

import hashlib
import json
import time
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LlmCache

TIMEOUT_SECONDS = 60.0


class LlmError(RuntimeError):
    """Raised when a call fails after retries. Callers mark the node failed."""


def _cache_key(model: str, system: str, user: str, schema: dict[str, Any]) -> str:
    raw = json.dumps([model, system, user, schema], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _endpoint(model: str) -> str:
    # The model ID contains ":" (e.g. ...-v1:0), so it must be percent-encoded in the path.
    region = get_settings().bedrock_region
    return f"https://bedrock-runtime.{region}.amazonaws.com/model/{quote(model, safe='')}/converse"


def _post(url: str, body: dict[str, Any]) -> httpx.Response:
    """Single place that sends HTTP. Tests replace this function."""
    key = get_settings().bedrock_api_key
    if not key:
        raise LlmError("BEDROCK_API_KEY not set")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        return client.post(url, headers=headers, json=body)


def _extract_tool_input(payload: dict[str, Any], tool_name: str) -> dict[str, Any]:
    for block in payload.get("output", {}).get("message", {}).get("content", []):
        tool_use = block.get("toolUse")
        if tool_use and tool_use.get("name") == tool_name and isinstance(tool_use.get("input"), dict):
            return tool_use["input"]
    raise LlmError(f"no toolUse block for {tool_name} in response")


def complete_json(
    db: Session,
    *,
    model: str,
    system: str,
    user: str,
    schema_name: str,
    schema: dict[str, Any],
    max_completion_tokens: int,
) -> dict[str, Any]:
    if not model:
        raise LlmError("Bedrock model ID not set (LLM_MODEL_HEAVY / LLM_MODEL_LIGHT)")
    key = _cache_key(model, system, user, schema)
    hit = db.get(LlmCache, key)
    if hit is not None:
        return hit.response

    url = _endpoint(model)
    body = {
        "system": [{"text": system}],
        "messages": [{"role": "user", "content": [{"text": user}]}],
        "inferenceConfig": {"maxTokens": max_completion_tokens, "temperature": 0},
        "toolConfig": {
            "tools": [{"toolSpec": {
                "name": schema_name,
                "description": "Return the answer in this structure.",
                "inputSchema": {"json": schema},
            }}],
            "toolChoice": {"tool": {"name": schema_name}},
        },
    }

    last_err: Exception | None = None
    # At most 3 provider calls per request (test_no_loops checks this bound).
    for attempt in range(3):
        try:
            resp = _post(url, body)
            if resp.is_success:
                parsed = _extract_tool_input(resp.json(), schema_name)
                db.add(LlmCache(key=key, response=parsed))
                db.commit()
                return parsed
            if resp.status_code in (429, 500, 502, 503):
                last_err = LlmError(f"HTTP {resp.status_code}")
                time.sleep(2 * (attempt + 1))
                continue
            # Not retryable: bad request, bad key, or model not enabled in this account/region.
            raise LlmError(f"{model} rejected: HTTP {resp.status_code}: {resp.text[:300]}")
        except LlmError:
            raise
        except (httpx.TransportError, json.JSONDecodeError) as exc:
            last_err = exc
            time.sleep(2 * (attempt + 1))
    raise LlmError(f"{model} failed after 3 attempts: {type(last_err).__name__}: {last_err}")
