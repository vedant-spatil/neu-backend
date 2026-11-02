#!/usr/bin/env bash
# Phase 1, step 3: host metrics (memory, swap, disk, CPU) through the CloudWatch agent. Run ON THE BOX, as a user with sudo.
#   scripts/aws/03-cw-agent.sh
# Idempotent: reinstalls the package only when it is missing, then loads the config and starts the agent.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
cd "$(dirname "$0")/../.."
CONFIG="$(pwd)/docker/cloudwatch/agent.json"
CTL=/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl

[ -f "$CONFIG" ] || { echo "missing $CONFIG" >&2; exit 1; }

if [ ! -x "$CTL" ]; then
  curl -fsSL -o /tmp/amazon-cloudwatch-agent.deb \
    "https://amazoncloudwatch-agent-${REGION}.s3.${REGION}.amazonaws.com/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb"
  sudo dpkg -i -E /tmp/amazon-cloudwatch-agent.deb
  rm -f /tmp/amazon-cloudwatch-agent.deb
  echo "installed amazon-cloudwatch-agent"
fi

# Load the config (-m ec2 uses the instance role; no keys) and start the agent.
sudo "$CTL" -a fetch-config -m ec2 -c "file:$CONFIG" -s
sudo systemctl enable amazon-cloudwatch-agent >/dev/null 2>&1 || true
sudo "$CTL" -a status | grep -E '"status"|"configstatus"' || true
echo "agent running. Metrics appear under AML/EC2 within a few minutes."
