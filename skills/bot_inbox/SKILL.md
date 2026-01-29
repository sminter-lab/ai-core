---
name: process-bot-inbox
description: Import bot inbox JSON into ai-core memory and archive processed files.
user-invocable: true
---

Run:
python3 {baseDir}/process_bot_inbox.py

Reads:
$AI_VAULT_ROOT/inbox/bot/*.json

Writes:
ai-core memory via tools.memory.store
