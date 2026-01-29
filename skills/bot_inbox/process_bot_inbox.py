#!/usr/bin/env python3
from __future__ import annotations

# --- bootstrap repo imports (CRITICAL FIX) ---
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# --------------------------------------------

import json
import os
from typing import Any

from tools.memory.store import (
    write_daily,
    write_decision,
    write_fact,
    write_run,
)

# --- vault-backed inbox (NOT git-tracked data/) ---
AI_VAULT_ROOT = Path(os.environ.get("AI_VAULT_ROOT", "/srv/ai-vault")).expanduser().resolve()
INBOX = AI_VAULT_ROOT / "inbox" / "bot"
PROCESSED = INBOX / "processed"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require(d: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in d:
        raise KeyError(f"Missing required field '{key}' in {ctx}")
    return d[key]


def handle_payload(payload: dict[str, Any]) -> None:
    kind = payload.get("kind")
    data = payload.get("data", {}) or {}

    if kind == "daily":
        summary = require(data, "summary", "daily.data")
        sources = data.get("sources", [])
        name = data.get("name", "daily")
        write_daily(summary, sources, name=name)

    elif kind == "decision":
        title = require(data, "title", "decision.data")
        rationale = require(data, "rationale", "decision.data")
        consequences = data.get("consequences", [])
        name = data.get("name", "decision")
        write_decision(title, rationale, consequences, name=name)

    elif kind == "fact":
        key = require(data, "key", "fact.data")
        value = require(data, "value", "fact.data")
        confidence = float(data.get("confidence", 1.0))
        write_fact(key, value, confidence)

    elif kind == "run":
        run_id = require(data, "run_id", "run.data")
        status = require(data, "status", "run.data")
        notes = data.get("notes")
        write_run(run_id, status, notes)

    else:
        raise ValueError(f"Unknown kind: {kind!r}")


def main() -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)

    files = sorted(INBOX.glob("*.json"))
    if not files:
        print(f"No inbox files to process in {INBOX}")
        return 0

    ok = 0
    bad = 0

    for p in files:
        try:
            payload = load_json(p)
            handle_payload(payload)

            dest = PROCESSED / p.name
            p.rename(dest)
            print(f"processed: {p.name}")
            ok += 1

        except Exception as e:
            print(f"ERROR {p.name}: {e}")
            bad += 1

    print(f"done: ok={ok} bad={bad} inbox={INBOX}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
