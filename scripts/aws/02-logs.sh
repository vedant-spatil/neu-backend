#!/usr/bin/env bash
# Phase 1, step 2: the CloudWatch log group for the stack, its retention, and metric filters for alarms.
#   scripts/aws/02-logs.sh
# Idempotent. Needs AWS CLI credentials with logs:* on /aml/* (the admin machine or the instance role).
#
# Stream names inside /aml/prod are set by docker-compose.prod.yml: api, nginx, yente, elasticsearch.
# Metric filters match on any stream in the group, so the patterns are written to match only their own log format.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
LOG_GROUP="${AWS_LOG_GROUP:-/aml/prod}"
RETENTION_DAYS=14
METRIC_NAMESPACE="AML/Logs"

# Only "already exists" counts as success. Any other error (bad credentials, no permission) stops the script.
if OUT=$(aws logs create-log-group --region "$REGION" --log-group-name "$LOG_GROUP" 2>&1); then
  echo "created log group $LOG_GROUP"
elif grep -q ResourceAlreadyExistsException <<<"$OUT"; then
  echo "log group $LOG_GROUP already exists"
else
  echo "$OUT" >&2
  exit 1
fi
aws logs put-retention-policy --region "$REGION" --log-group-name "$LOG_GROUP" --retention-in-days "$RETENTION_DAYS"
echo "retention $RETENTION_DAYS days on $LOG_GROUP"

# nginx access log, default format: ip - - [time] "request" status size ...
# Count responses with a 5xx status.
aws logs put-metric-filter --region "$REGION" --log-group-name "$LOG_GROUP" \
  --filter-name aml-nginx-5xx \
  --filter-pattern '[ip, id, user, timestamp, request, status_code = 5*, size]' \
  --metric-transformations "metricName=Nginx5xx,metricNamespace=$METRIC_NAMESPACE,metricValue=1,defaultValue=0"
echo "metric filter aml-nginx-5xx set"

# API errors: any line with a Python traceback or an ERROR level (uvicorn and the app's own logging).
aws logs put-metric-filter --region "$REGION" --log-group-name "$LOG_GROUP" \
  --filter-name aml-api-errors \
  --filter-pattern '?ERROR ?Traceback' \
  --metric-transformations "metricName=ApiErrors,metricNamespace=$METRIC_NAMESPACE,metricValue=1,defaultValue=0"
echo "metric filter aml-api-errors set"
