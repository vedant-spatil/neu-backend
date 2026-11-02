#!/usr/bin/env bash
# Phase 2, step 1: build the arm64 AgentCore image and push it to ECR. Run from a machine with Docker buildx and an
# arm64 target (a laptop or CI). The box does not build this image.
#   scripts/aws/10-ecr-agent.sh
# Needs AWS CLI credentials with ecr:* on the repo. Prints the image URI (not secret).
set -euo pipefail
cd "$(dirname "$0")/../.."

REGION="${AWS_REGION:-ap-south-1}"
REPO="aml-agentcore"

# Stop early with a clear message if the AWS credentials are not working.
aws sts get-caller-identity >/dev/null 2>&1 || {
  echo "AWS credentials are not working. Run: aws sts get-caller-identity   (fix the credentials first)" >&2
  exit 1
}
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

# Building arm64 on an x86 machine needs QEMU emulation registered with the kernel. Skip if already present.
if ! docker buildx inspect --bootstrap 2>/dev/null | grep -q linux/arm64; then
  docker run --privileged --rm tonistiigi/binfmt --install arm64 >/dev/null
  echo "registered arm64 emulation"
fi
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
URI="$REGISTRY/$REPO:$TAG"

aws ecr describe-repositories --region "$REGION" --repository-names "$REPO" >/dev/null 2>&1 \
  || aws ecr create-repository --region "$REGION" --repository-name "$REPO" \
       --image-scanning-configuration scanOnPush=true >/dev/null

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker buildx create --use --name aml-builder >/dev/null 2>&1 || docker buildx use aml-builder
docker buildx build --platform linux/arm64 -f docker/agentcore.Dockerfile -t "$URI" --push .

echo "pushed $URI"
