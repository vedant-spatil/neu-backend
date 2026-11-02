#!/usr/bin/env bash
# Phase 2, step 4: point the EC2 API at the AgentCore runtime. Run ON THE BOX.
#   scripts/aws/13-api-env.sh <agentRuntimeArn>
# Sets two names in .env (CASE_RUNNER and AGENTCORE_RUNTIME_ARN), then recreates the API. No values are printed.
# To go back to the in-process workflow: CASE_RUNNER=local, then recreate the API.
set -euo pipefail
cd "$(dirname "$0")/../.."

ARN="${1:?usage: 13-api-env.sh <agentRuntimeArn>}"
case "$ARN" in arn:aws:bedrock-agentcore:*:runtime/*) ;; *) echo "not an AgentCore runtime ARN" >&2; exit 1 ;; esac
[ -f .env ] || { echo ".env not found" >&2; exit 1; }

python3 - "$ARN" <<'PY'
import pathlib, sys
arn = sys.argv[1]
path = pathlib.Path(".env")
settings = {"CASE_RUNNER": "agentcore", "AGENTCORE_RUNTIME_ARN": arn}
lines = path.read_text().splitlines()
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0]
    if key in settings:
        out.append(f"{key}={settings[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in settings.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n")
PY
chmod 600 .env

docker compose -f docker-compose.prod.yml up -d --force-recreate api
echo "API now runs case workflows on AgentCore. Check: docker compose -f docker-compose.prod.yml logs --tail=20 api"
