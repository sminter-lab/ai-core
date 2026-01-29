#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

AI_VAULT_ROOT="${AI_VAULT_ROOT:-/srv/ai-vault}"
export AI_VAULT_ROOT

python3 -m workers.daily_status.run --date "${1:-today}" --format text
