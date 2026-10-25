"""The only module that talks to an LLM provider (plan 16.12).

The provider comes from LLM_PROVIDER (settings.llm_provider), and the model names from LLM_MODEL_HEAVY and
LLM_MODEL_LIGHT, so switching provider is a .env change:
  - "groq" (default): OpenAI-compatible chat completions with the Groq API key. The free tier is tight
    (about 8,000 tokens a minute per model), so 429 responses wait longer before retrying.
  - "bedrock": Amazon Bedrock Converse API with the Bedrock API key, in BEDROCK_REGION.

Structured output: one tool whose parameters are the caller's JSON schema, and the model is forced to call it.
Every call is cached by a hash of (provider, model, system, user, schema), so reruns cost no tokens.
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LlmCache

TIMEOUT_SECONDS = 60.0
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class LlmError(RuntimeError):
    """Raised when a call fails after retries. Callers mark the node failed."""


@dataclass(frozen=True)
class Request:
    url: str
    headers: dict[str, str]
    body: dict[str, Any]


def _cache_key(provider: str, model: str, system: str, user: str, schema: dict[str, Any]) -> str:
    raw = json.dumps([provider, model, system, user, schema], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


# ---- Groq: OpenAI-compatible chat completions ------------------------------------------------------------------

def _groq_request(model: str, system: str, user: str, schema_name: str, schema: dict[str, Any],
                  max_tokens: int) -> Request:
    key = get_settings().groq_api_key
    if not key:
        raise LlmError("GROQ_API_KEY not set")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_completion_tokens": max_tokens,
        "tools": [{
            "type": "function",
            "function": {
                "name": schema_name,
                "description": "Return the answer in this structure.",
                "parameters": schema,
            },
        }],
        "tool_choice": {"type": "function", "function": {"name": schema_name}},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    return Request(GROQ_ENDPOINT, headers, body)


def _groq_parse(payload: dict[str, Any], tool_name: str) -> dict[str, Any]:
    for choice in payload.get("choices", []):
        for call in choice.get("message", {}).get("tool_calls") or []:
            fn = call.get("function", {})
            if fn.get("name") == tool_name:
                args = json.loads(fn.get("arguments") or "{}")
                if isinstance(args, dict):
                    return args
    raise LlmError(f"no tool call for {tool_name} in response")


# ---- Amazon Bedrock: Converse API -----------------------------------------------------------------------------

def _bedrock_request(model: str, system: str, user: str, schema_name: str, schema: dict[str, Any],
                     max_tokens: int) -> Request:
    settings = get_settings()
    if not settings.bedrock_api_key:
        raise LlmError("BEDROCK_API_KEY not set")
    # The model ID contains ":" (e.g. ...-v1:0), so it must be percent-encoded in the path.
    url = f"https://bedrock-runtime.{settings.bedrock_region}.amazonaws.com/model/{quote(model, safe='')}/converse"
    body = {
        "system": [{"text": system}],
        "messages": [{"role": "user", "content": [{"text": user}]}],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
        "toolConfig": {
            "tools": [{"toolSpec": {
                "name": schema_name,
                "description": "Return the answer in this structure.",
                "inputSchema": {"json": schema},
            }}],
            "toolChoice": {"tool": {"name": schema_name}},
        },
    }
    headers = {"Authorization": f"Bearer {settings.bedrock_api_key}", "Content-Type": "application/json"}
    return Request(url, headers, body)


def _bedrock_parse(payload: dict[str, Any], tool_name: str) -> dict[str, Any]:
    for block in payload.get("output", {}).get("message", {}).get("content", []):
        tool_use = block.get("toolUse")
        if tool_use and tool_use.get("name") == tool_name and isinstance(tool_use.get("input"), dict):
            return tool_use["input"]
    raise LlmError(f"no toolUse block for {tool_name} in response")


# Provider name -> (build the request, read the answer). Add a provider here and in config.
PROVIDERS: dict[str, tuple[Callable[..., Request], Callable[[dict[str, Any], str], dict[str, Any]]]] = {
    "groq": (_groq_request, _groq_parse),
    "bedrock": (_bedrock_request, _bedrock_parse),
}


def _post(req: Request) -> httpx.Response:
    """Single place that sends HTTP. Tests replace this function."""
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        return client.post(req.url, headers=req.headers, json=req.body)


def _wait_seconds(resp: httpx.Response, attempt: int) -> float:
    """Throttling (429) clears within about a minute on the free tier, so wait longer for it."""
    if resp.status_code == 429:
        return 15.0 * (attempt + 1)
    return 2.0 * (attempt + 1)


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
    provider = get_settings().llm_provider
    if provider not in PROVIDERS:
        raise LlmError(f"unknown LLM_PROVIDER {provider!r}; use one of {sorted(PROVIDERS)}")
    if not model:
        raise LlmError("model not set (LLM_MODEL_HEAVY / LLM_MODEL_LIGHT)")
    key = _cache_key(provider, model, system, user, schema)
    hit = db.get(LlmCache, key)
    if hit is not None:
        return hit.response

    build, parse = PROVIDERS[provider]
    req = build(model, system, user, schema_name, schema, max_completion_tokens)

    last_err: Exception | None = None
    # At most 3 provider calls per request (test_no_loops checks this bound).
    for attempt in range(3):
        try:
            resp = _post(req)
            if resp.is_success:
                parsed = parse(resp.json(), schema_name)
                db.add(LlmCache(key=key, response=parsed))
                db.commit()
                return parsed
            if resp.status_code in (429, 500, 502, 503):
                last_err = LlmError(f"HTTP {resp.status_code}")
                time.sleep(_wait_seconds(resp, attempt))
                continue
            # Not retryable: bad request, bad key, or model not available to this account.
            raise LlmError(f"{provider} {model} rejected: HTTP {resp.status_code}: {resp.text[:300]}")
        except LlmError:
            raise
        except (httpx.TransportError, json.JSONDecodeError) as exc:
            last_err = exc
            time.sleep(2 * (attempt + 1))
    raise LlmError(f"{provider} {model} failed after 3 attempts: {type(last_err).__name__}: {last_err}")
