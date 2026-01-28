#!/usr/bin/env bash
set -euo pipefail

AI_VAULT_ROOT="${AI_VAULT_ROOT:-/srv/ai-vault}"
JOB_SPEC_PATH="${1:-}"

if [[ -z "$JOB_SPEC_PATH" ]]; then
  echo "Usage: run.sh /path/to/job_spec.json"
  exit 1
fi

IMAGE="ai-core/web-rss-collector:latest"

docker build -t "$IMAGE" "$(dirname "$0")"

docker run --rm \
  -e AI_VAULT_ROOT="$AI_VAULT_ROOT" \
  -e JOB_SPEC_PATH="/work/$(basename "$JOB_SPEC_PATH")" \
  -v "$AI_VAULT_ROOT":"$AI_VAULT_ROOT" \
  -v "$(cd "$(dirname "$JOB_SPEC_PATH")" && pwd)":/work:ro \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m \
  --cpus="1.0" \
  --memory="1024m" \
  --pids-limit 256 \
  "$IMAGE"
