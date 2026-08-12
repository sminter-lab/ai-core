#!/usr/bin/env bash
# Schwab evening pipeline with catch-up.
#
# Why: cron fires at fixed times, but after a crash reboot this Mac sits at the
# FileVault login window with the data volume locked — every cron job silently
# no-ops until someone logs in (missed runs on 2026-07-28, 2026-08-07..10).
# Cron calls this hourly through the evening instead; it exits instantly if
# today's run row already exists, so the pipeline still executes exactly once
# per day but self-heals if a login happens later the same evening.
set -euo pipefail

cd /Users/samuelminter/ai-core

# runs.ts is UTC; 18:00 ET = 22:00/23:00 UTC, same calendar date either way
DONE=$(sqlite3 data/schwab/schwab.sqlite3 \
  "SELECT COUNT(*) FROM runs WHERE date(ts)=date('now') AND status='OK'" 2>/dev/null || echo 0)
if [ "${DONE}" -gt 0 ]; then
  exit 0
fi

source .venv/bin/activate
python -m workers.schwab.collector \
  && python -m workers.schwab.options_collector \
  && python -m workers.schwab.market_snapshot \
  && python -m workers.schwab.verify
