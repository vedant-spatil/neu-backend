#!/usr/bin/env bash
# Phase 1, step 4: one SNS topic with an email subscription, and the alarms for the box and the stack.
#   INSTANCE_ID=i-xxxxxxxx scripts/aws/04-alarms.sh you@example.com
# The email must confirm the subscription (AWS sends a link). Idempotent: re-running updates the alarms in place.
set -euo pipefail

EMAIL="${1:?usage: 04-alarms.sh <alert email>}"
REGION="${AWS_REGION:-ap-south-1}"
LOG_NS="AML/Logs"
HOST_NS="AML/EC2"

if [ -z "${INSTANCE_ID:-}" ]; then
  TOKEN=$(curl -s -m 2 -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)
  INSTANCE_ID=$(curl -s -m 2 -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id || true)
fi
case "${INSTANCE_ID:-}" in
  i-*) ;;
  *) echo "set INSTANCE_ID=i-... (the EC2 instance ID; this is not running on the box)" >&2; exit 1 ;;
esac

TOPIC_ARN=$(aws sns create-topic --region "$REGION" --name aml-alerts --query TopicArn --output text)
if ! aws sns list-subscriptions-by-topic --region "$REGION" --topic-arn "$TOPIC_ARN" \
     --query "Subscriptions[?Endpoint=='$EMAIL'].SubscriptionArn" --output text | grep -q arn; then
  aws sns subscribe --region "$REGION" --topic-arn "$TOPIC_ARN" --protocol email --notification-endpoint "$EMAIL" >/dev/null
  echo "subscription sent to $EMAIL: confirm it in the email"
fi

# put-metric-alarm replaces an alarm with the same name, so this is safe to re-run.
alarm() {  # name, description, namespace, metric, statistic, comparison, threshold, periods, period, dims...
  local name="$1" desc="$2" ns="$3" metric="$4" stat="$5" cmp="$6" threshold="$7" periods="$8" period="$9"
  shift 9
  local dims=()
  if [ "$#" -gt 0 ]; then dims=(--dimensions "$@"); fi
  aws cloudwatch put-metric-alarm --region "$REGION" --alarm-name "$name" --alarm-description "$desc" \
    --namespace "$ns" --metric-name "$metric" --statistic "$stat" --comparison-operator "$cmp" \
    --threshold "$threshold" --evaluation-periods "$periods" --period "$period" \
    --treat-missing-data notBreaching --alarm-actions "$TOPIC_ARN" --ok-actions "$TOPIC_ARN" \
    "${dims[@]}"
  echo "alarm $name set"
}

INST="Name=InstanceId,Value=$INSTANCE_ID"
alarm aml-mem-high      "EC2 memory above 85% for 10 minutes" "$HOST_NS" mem_used_percent  Average GreaterThanThreshold 85 2 300 "$INST"
alarm aml-swap-high     "Swap above 50% for 10 minutes"       "$HOST_NS" swap_used_percent Average GreaterThanThreshold 50 2 300 "$INST"
alarm aml-disk-high     "Root disk above 80%"                 "$HOST_NS" disk_used_percent Average GreaterThanThreshold 80 1 300 "$INST"
alarm aml-cpu-high      "CPU above 80% for 15 minutes"        "$HOST_NS" cpu_usage_active  Average GreaterThanThreshold 80 3 300 "$INST"
alarm aml-nginx-5xx     "More than 5 nginx 5xx responses in 5 minutes" "$LOG_NS" Nginx5xx Sum GreaterThanThreshold 5 1 300
alarm aml-api-errors    "API traceback or ERROR line in 5 minutes"     "$LOG_NS" ApiErrors Sum GreaterThanThreshold 0 1 300
alarm aml-status-check  "EC2 status check failed"             AWS/EC2 StatusCheckFailed Maximum GreaterThanThreshold 0 2 60 "$INST"

echo "done. Topic: $TOPIC_ARN"
