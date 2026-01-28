#!/usr/bin/env bash
set -euo pipefail

IMAGE="ai-core/web-rss-collector:latest"

AI_VAULT_ROOT="${AI_VAULT_ROOT:-/srv/ai-vault}"
JOB_SPEC_REL="${1:-}"

if [[ -z "$JOB_SPEC_REL" ]]; then
  echo "Usage: run.sh data/rss_finance.json"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SPEC_HOST_PATH="$REPO_ROOT/$JOB_SPEC_REL"

if [[ -d "$SPEC_HOST_PATH" ]]; then
  echo "ERROR: Spec path is a directory, expected a file: $JOB_SPEC_REL"
  exit 2
fi
if [[ ! -f "$SPEC_HOST_PATH" ]]; then
  echo "ERROR: Spec file not found: $JOB_SPEC_REL"
  exit 3
fi

# Build only if image doesn't exist (faster, avoids rebuild spam)
docker image inspect "$IMAGE" >/dev/null 2>&1 || \
  docker build -t "$IMAGE" "$REPO_ROOT/jobs/web_rss_collector"

docker run --rm \
  -e AI_VAULT_ROOT="$AI_VAULT_ROOT" \
  -e JOB_SPEC_PATH="/work/$JOB_SPEC_REL" \
  -v "$AI_VAULT_ROOT":"$AI_VAULT_ROOT" \
  -v "$REPO_ROOT":/work:ro \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m \
  --cpus="1.0" \
  --memory="1024m" \
  --pids-limit 256 \
  "$IMAGE"
