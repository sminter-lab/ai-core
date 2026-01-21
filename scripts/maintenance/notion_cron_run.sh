#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$HOME/ai-core"
cd "$REPO_DIR"

# Load secrets/env (local-only file; not committed)
if [ -f ".env" ]; then
  set -a
  source ".env"
  set +a
fi

# Use the repo virtualenv
if [ -f ".venv/bin/activate" ]; then
  source ".venv/bin/activate"
else
  echo "ERROR: Missing venv at $REPO_DIR/.venv"
  exit 1
fi

# Ensure logs dir exists
mkdir -p logs

# Input source: a file that your AI writes (you can change this later)
INPUT_FILE="${1:-$REPO_DIR/ai_output.txt}"

# If no input file, do nothing (cron should be harmless)
if [ ! -f "$INPUT_FILE" ]; then
  echo "$(date -Iseconds) no input file: $INPUT_FILE" >> logs/notion_cron.log
  exit 0
fi

# Run the updater and append output to a cron log
python3 -m agents.notion.runner "$INPUT_FILE" >> logs/notion_cron.log 2>&1
echo "$(date -Iseconds) ran notion updater using $INPUT_FILE" >> logs/notion_cron.log
