#!/usr/bin/env bash
# Phase 2, step 3: the AgentCore Runtime for case runs (plan phase 2). Run from an admin machine with AWS CLI credentials.
#   IMAGE_URI=<account>.dkr.ecr.ap-south-1.amazonaws.com/aml-agentcore:<tag> scripts/aws/12-agentcore.sh
#
# Creates (or updates) the runtime role and the runtime "aml_case_agent". Runtime environment variables come from the
# Secrets Manager secret AGENT_SECRET_ID (default aml/agent). Its JSON keys are the agent's settings: DATABASE_URL,
# NEO4J_URI, NEO4J_PASSWORD, PII_ENCRYPTION_KEY, JWT_SECRET_KEY, LLM_PROVIDER, LLM_MODEL_HEAVY, LLM_MODEL_LIGHT,
# BEDROCK_REGION, YENTE_URL, YENTE_BASIC_USER, YENTE_BASIC_PASSWORD, APP_ENV. Nothing from the secret is printed.
#
# Verify the CLI subcommands and flags with `aws bedrock-agentcore-control create-agent-runtime help` before the first run.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
aws sts get-caller-identity >/dev/null 2>&1 || {
  echo "AWS credentials are not working. Run: aws sts get-caller-identity   (fix the credentials first)" >&2
  exit 1
}
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ROLE="aml-agentcore-runtime"
RUNTIME_NAME="aml_case_agent"
AGENT_SECRET_ID="${AGENT_SECRET_ID:-aml/agent}"
: "${IMAGE_URI:?set IMAGE_URI to the pushed aml-agentcore image (script 10)}"

# 1. Runtime role: pull the image, write logs, call Bedrock. Trusted only by AgentCore in this account.
TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
  "Principal":{"Service":"bedrock-agentcore.amazonaws.com"},
  "Action":"sts:AssumeRole",
  "Condition":{"StringEquals":{"aws:SourceAccount":"$ACCOUNT"}}}]}
JSON
)
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document "$TRUST" >/dev/null
  echo "created role $ROLE"
fi
ROLE_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Sid":"PullImage","Effect":"Allow","Action":["ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:BatchCheckLayerAvailability"],
   "Resource":"arn:aws:ecr:$REGION:$ACCOUNT:repository/aml-agentcore"},
  {"Sid":"EcrToken","Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},
  {"Sid":"Logs","Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],
   "Resource":"arn:aws:logs:$REGION:$ACCOUNT:log-group:/aws/bedrock-agentcore/*"},
  {"Sid":"Bedrock","Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],
   "Resource":["arn:aws:bedrock:$REGION::foundation-model/*","arn:aws:bedrock:$REGION:$ACCOUNT:inference-profile/*"]}
]}
JSON
)
aws iam put-role-policy --role-name "$ROLE" --policy-name aml-agentcore-runtime --policy-document "$ROLE_POLICY"
ROLE_ARN=$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)

# 2. Runtime environment from the secret. The values are passed in the request and never echoed.
SECRET_JSON=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$AGENT_SECRET_ID" \
  --query SecretString --output text)
ENV_JSON=$(printf '%s' "$SECRET_JSON" | python3 -c '
import json, sys
d = json.load(sys.stdin)
d["CASE_RUNNER"] = "local"  # the agent runs the workflow itself; it must never call AgentCore again
print(json.dumps({k: str(v) for k, v in d.items()}))')
unset SECRET_JSON

ARTIFACT="{\"containerConfiguration\":{\"containerUri\":\"$IMAGE_URI\"}}"
NETWORK='{"networkMode":"PUBLIC"}'

# 3. Create or update the runtime (idempotent by name).
RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
  --query "agentRuntimes[?agentRuntimeName=='$RUNTIME_NAME'].agentRuntimeId | [0]" --output text)
if [ "$RUNTIME_ID" = "None" ] || [ -z "$RUNTIME_ID" ]; then
  RUNTIME_ID=$(aws bedrock-agentcore-control create-agent-runtime --region "$REGION" \
    --agent-runtime-name "$RUNTIME_NAME" --agent-runtime-artifact "$ARTIFACT" --role-arn "$ROLE_ARN" \
    --network-configuration "$NETWORK" --environment-variables "$ENV_JSON" \
    --query agentRuntimeId --output text)
  echo "created runtime $RUNTIME_NAME"
else
  aws bedrock-agentcore-control update-agent-runtime --region "$REGION" --agent-runtime-id "$RUNTIME_ID" \
    --agent-runtime-artifact "$ARTIFACT" --role-arn "$ROLE_ARN" --network-configuration "$NETWORK" \
    --environment-variables "$ENV_JSON" >/dev/null
  echo "updated runtime $RUNTIME_NAME"
fi

# 4. Wait for READY (bounded: about 5 minutes).
for _ in $(seq 1 30); do
  STATUS=$(aws bedrock-agentcore-control get-agent-runtime --region "$REGION" --agent-runtime-id "$RUNTIME_ID" \
    --query status --output text)
  [ "$STATUS" = "READY" ] && break
  case "$STATUS" in *FAILED*|*ERROR*) echo "runtime status: $STATUS" >&2; exit 1 ;; esac
  sleep 10
done
[ "$STATUS" = "READY" ] || { echo "runtime not READY after 5 minutes (status: $STATUS)" >&2; exit 1; }

ARN=$(aws bedrock-agentcore-control get-agent-runtime --region "$REGION" --agent-runtime-id "$RUNTIME_ID" \
  --query agentRuntimeArn --output text)
echo "runtime ARN: $ARN"
echo "next: scripts/aws/13-api-env.sh $ARN"
