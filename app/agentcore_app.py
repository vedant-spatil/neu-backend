"""AgentCore Runtime entrypoint: runs one case workflow (plan: AgentCore for case runs, phase 2).

The API (on EC2) calls this through app/services/agentcore.py. The container is built from docker/agentcore.Dockerfile,
where CASE_RUNNER=local, so the workflow runs here and never calls AgentCore again. Errors are returned to the API as
{"status": "error", "error": ...}; the API records them on the case.
"""

import logging
from typing import Any

from app.services.cases import run_case_local
from app.services.errors import describe_error

log = logging.getLogger(__name__)

REQUIRED = ("case_id", "alert_id", "account_id", "alert_ts", "rules", "risk", "txn_ids", "attempt")


def handle_case(payload: dict[str, Any]) -> dict[str, Any]:
    """Runs the case described by the payload and returns the status for the API."""
    missing = [k for k in REQUIRED if k not in payload]
    if missing:
        return {"status": "error", "error": f"payload is missing {', '.join(missing)}"}
    try:
        run_case_local(
            int(payload["case_id"]), int(payload["alert_id"]), str(payload["account_id"]), str(payload["alert_ts"]),
            list(payload["rules"]), float(payload["risk"]), [int(i) for i in payload["txn_ids"]],
            int(payload["attempt"]),
        )
    except Exception as exc:  # reported to the API as a result, not a crash of the runtime
        log.exception("case %s failed in the agent", payload.get("case_id"))
        return {"status": "error", "error": describe_error(exc)}
    return {"status": "ok", "case_id": int(payload["case_id"])}


def build_app() -> Any:
    # Imported here so the module loads (and its tests run) without the AgentCore SDK installed.
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload: dict[str, Any]) -> dict[str, Any]:
        return handle_case(payload)

    return app


if __name__ == "__main__":
    build_app().run()
