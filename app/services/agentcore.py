"""Calls the AgentCore Runtime that runs one case workflow (plan: AgentCore for case runs, phase 2).

The runtime itself is app/agentcore_app.py, a container with CASE_RUNNER=local. This module is the API-side client.
A case run can take many minutes, so the read timeout is long. Retries are off: a retry would start a second run of the
same case.
"""

import json
import uuid
from typing import Any

import boto3
from botocore.config import Config

from app.config import get_settings

RUN_TIMEOUT_SECONDS = 8 * 60 * 60  # AgentCore allows runs up to 8 hours


def invoke_case(payload: dict[str, Any], client: Any = None) -> dict[str, Any]:
    """Runs one case on AgentCore and waits for the agent's answer. Raises RuntimeError if the agent reports an error."""
    settings = get_settings()
    if client is None:
        client = boto3.client(
            "bedrock-agentcore",
            region_name=settings.agentcore_region or settings.bedrock_region,
            config=Config(read_timeout=RUN_TIMEOUT_SECONDS, connect_timeout=15, retries={"max_attempts": 1}),
        )
    # A session id per run. It must be at least 33 characters, so it carries 32 hex characters of randomness.
    session_id = f"case-{payload['case_id']}-{payload['attempt']}-{uuid.uuid4().hex}"
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=settings.agentcore_runtime_arn,
        runtimeSessionId=session_id,
        payload=json.dumps(payload).encode(),
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(resp["response"].read() or b"{}")
    if result.get("status") != "ok":
        raise RuntimeError(result.get("error") or "AgentCore agent reported an error")
    return result
