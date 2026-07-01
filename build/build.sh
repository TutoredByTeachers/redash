#!/bin/bash
#
# Build and push the TBT Redash deployment image to ECR.
#
# Unlike the previous tbt-infra/redash/build script (which pulled redash/redash:<tag>
# and sed-patched it), this builds the image from THIS fork's source so our carried
# patches and frontend assets are baked in, then layers Doppler on top (build/Dockerfile).
#
# Usage: build/build.sh <dev|stage|prod> [image_tag]
set -euo pipefail

ENVIRONMENT="${1:-}"
if [[ "$ENVIRONMENT" != "dev" && "$ENVIRONMENT" != "stage" && "$ENVIRONMENT" != "prod" ]]; then
  echo "Usage: $0 <dev|stage|prod> [image_tag]"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Default the tag to the Redash version on this branch (e.g. 26.3.0). Pass an explicit
# tag (e.g. 26.3.0-2) to cut a new build of the same upstream version.
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO_ROOT/redash/__init__.py")"
IMAGE_TAG="${2:-${VERSION}}"
if [ -z "$IMAGE_TAG" ]; then
  echo "Could not determine image tag; pass one explicitly."
  exit 1
fi

REGISTRY="014491063547.dkr.ecr.us-east-2.amazonaws.com"
REGION="us-east-2"
REPO="redash-${ENVIRONMENT}"
PLATFORM="linux/amd64"
BASE_IMAGE="tbt-redash-base:${IMAGE_TAG}"

echo "==> Building Redash base image from fork source (frontend + backend): ${BASE_IMAGE}"
# skip_frontend_build is intentionally empty so the frontend (incl. the slack_bot icon) is built.
docker buildx build --platform="$PLATFORM" --load \
  --build-arg skip_frontend_build= \
  -t "$BASE_IMAGE" \
  "$REPO_ROOT"

echo "==> Layering Doppler + entrypoint: ${REPO}:${IMAGE_TAG}"
docker buildx build --platform="$PLATFORM" --load \
  --build-arg BASE_IMAGE="$BASE_IMAGE" \
  -t "${REPO}:${IMAGE_TAG}" \
  "$REPO_ROOT/build"

echo "==> Authenticating with AWS ECR"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

echo "==> Pushing ${REGISTRY}/${REPO}:${IMAGE_TAG}"
docker tag "${REPO}:${IMAGE_TAG}" "${REGISTRY}/${REPO}:${IMAGE_TAG}"
docker push "${REGISTRY}/${REPO}:${IMAGE_TAG}"

echo "==> Done: ${REGISTRY}/${REPO}:${IMAGE_TAG}"
