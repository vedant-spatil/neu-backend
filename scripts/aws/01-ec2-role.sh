#!/usr/bin/env bash
# Phase 1, step 1: IAM role for the EC2 box, so the CloudWatch agent, the awslogs driver and the API can write and read
# CloudWatch without access keys on the host (plan: CloudWatch on the EC2 stack).
#
# Run from an admin machine with AWS CLI credentials (or on the box with an instance role that can manage IAM).
#   INSTANCE_ID=i-xxxxxxxx scripts/aws/01-ec2-role.sh
# If INSTANCE_ID is unset and this runs on the box, the instance is found through the metadata service.
# Idempotent: re-running updates the inline policy and skips what exists.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
ROLE="aml-ec2-role"
PROFILE="aml-ec2-profile"
LOG_GROUP_PREFIX="/aml"

if [ -z "${INSTANCE_ID:-}" ]; then
  TOKEN=$(curl -s -m 2 -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)
  INSTANCE_ID=$(curl -s -m 2 -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id || true)
fi
case "${INSTANCE_ID:-}" in
  i-*) ;;
  *) echo "set INSTANCE_ID=i-... (the EC2 instance ID; this is not running on the box)" >&2; exit 1 ;;
esac
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document "$TRUST" \
    --description "AML EC2 box: CloudWatch agent, awslogs driver, admin observability queries" >/dev/null
  echo "created role $ROLE"
fi

aws iam attach-role-policy --role-name "$ROLE" \
  --policy-arn arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy

# Inline policy: the admin observability page runs Logs Insights queries on /aml/* only, and can set retention on /aml/*.
# bedrock-agentcore:InvokeAgentRuntime is for phase 2 (AgentCore case runs). It is harmless until that is deployed.
POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AmlLogsRead",
      "Effect": "Allow",
      "Action": ["logs:StartQuery", "logs:StopQuery", "logs:GetQueryResults", "logs:DescribeLogGroups"],
      "Resource": "*"
    },
    {
      "Sid": "AmlLogsWrite",
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:PutRetentionPolicy",
                 "logs:DescribeLogStreams"],
      "Resource": "arn:aws:logs:${REGION}:${ACCOUNT}:log-group:${LOG_GROUP_PREFIX}*"
    },
    {
      "Sid": "AgentCoreInvoke",
      "Effect": "Allow",
      "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
      "Resource": "arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT}:runtime/*"
    }
  ]
}
JSON
)
aws iam put-role-policy --role-name "$ROLE" --policy-name aml-cloudwatch-access --policy-document "$POLICY"
echo "inline policy aml-cloudwatch-access set"

if ! aws iam get-instance-profile --instance-profile-name "$PROFILE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$PROFILE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$PROFILE" --role-name "$ROLE"
  echo "created instance profile $PROFILE"
fi
aws iam wait instance-profile-exists --instance-profile-name "$PROFILE"

# Attach the profile to the instance, or replace an existing association.
ASSOC=$(aws ec2 describe-iam-instance-profile-associations --region "$REGION" \
  --filters "Name=instance-id,Values=$INSTANCE_ID" --query "IamInstanceProfileAssociations[0].AssociationId" --output text)
if [ "$ASSOC" = "None" ] || [ -z "$ASSOC" ]; then
  aws ec2 associate-iam-instance-profile --region "$REGION" --instance-id "$INSTANCE_ID" \
    --iam-instance-profile Name="$PROFILE" >/dev/null
  echo "associated $PROFILE with $INSTANCE_ID"
else
  aws ec2 replace-iam-instance-profile-association --region "$REGION" --association-id "$ASSOC" \
    --iam-instance-profile Name="$PROFILE" >/dev/null
  echo "replaced instance profile association on $INSTANCE_ID"
fi
echo "done. If the box already runs Docker, restart the awslogs driver's containers after step 02 (compose recreate)."
