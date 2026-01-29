# daily_status

Generates a safe, outward-facing "what happened today" report by reading only
known artifact locations and writing a single daily digest into the AI vault.

## What it reads (safe, fixed paths)
- $AI_VAULT_ROOT/processed/rss/**/_index.json
- $AI_VAULT_ROOT/inbox/bot/**
- /var/lib/ai-core/state/** (optional)

## What it writes
- $AI_VAULT_ROOT/memory/daily/YYYY-MM-DD.md
- $AI_VAULT_ROOT/memory/daily/YYYY-MM-DD.json

## Run (manual)
AI_VAULT_ROOT=/srv/ai-vault python3 -m workers.daily_status.run --date today

## Run (Clawdbot)
This skill is intended to be executed as a command skill.
