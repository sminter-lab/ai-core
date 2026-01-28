#!/usr/bin/env bash
set -euo pipefail

AI_VAULT_ROOT="${AI_VAULT_ROOT:-/srv/ai-vault}"
JOB_SPEC_REL="${1:-}"

if [[ -z "$JOB_SPEC_REL" ]]; then
  echo "Usage: run.sh data/rss_finance.json"
  exit 1
fi

# Must be run from repo root so relative paths resolve
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

IMAGE="ai-core/web-rss-collector:latest"

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
