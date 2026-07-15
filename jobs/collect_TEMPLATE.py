"""MINTWORKER — {PROJECT} COLLECTOR (template).

Copy this file to jobs/collect_{project}.py and fill in the fetch logic.
See docs/framework_architecture.md for the framework contract.

The collector's ONLY job: fetch raw data from external sources and write it
to shared storage. No scoring, no LLM calls, no digests — that's the
analyzer's job on Mac Studio.

Required setup:
  1. config/{project}.json          — sources + max_api_calls budget
  2. control/{project}.enabled      — file containing "1"
  3. Cron on MintWorker (M-F only, lowest cadence first):
       0 6 * * 1-5  cd /path/to/ai-core && .venv/bin/python -m jobs.collect_{project}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.shared_store import raw_root

PROJECT = "TEMPLATE"  # ← rename

_BASE        = Path(__file__).resolve().parents[1]
_CONFIG      = json.loads((_BASE / "config" / f"{PROJECT}.json").read_text(encoding="utf-8"))
_OUT_DIR     = raw_root() / PROJECT / "raw"
_ENABLE_FLAG = _BASE / "control" / f"{PROJECT}.enabled"
_LOCKFILE    = _BASE / "control" / f"{PROJECT}.collector.lock"
_FAIL_FILE   = _BASE / "control" / f"{PROJECT}.failures"
_API_LOG     = _BASE / "logs" / f"{PROJECT}_api_usage.log"

MAX_API_CALLS = int(_CONFIG["collector"].get("max_api_calls", 50))
FAIL_LIMIT    = int(_CONFIG["collector"].get("failure_backoff_threshold", 3))

_api_calls_used = 0


# ── Runaway protection (required — do not remove) ────────────────────────────

def check_enabled() -> None:
    if not _ENABLE_FLAG.exists() or _ENABLE_FLAG.read_text().strip() != "1":
        print(f"[{PROJECT}] Enable flag off or missing — exiting.")
        sys.exit(0)


def check_weekday(force: bool) -> None:
    if not force and datetime.now().weekday() >= 5:
        print(f"[{PROJECT}] Weekend — exiting.")
        sys.exit(0)


def acquire_lock() -> None:
    if _LOCKFILE.exists():
        try:
            os.kill(int(_LOCKFILE.read_text().strip()), 0)
            print(f"[{PROJECT}] Already running — exiting.")
            sys.exit(0)
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # stale
    _LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    _LOCKFILE.write_text(str(os.getpid()))


def release_lock() -> None:
    _LOCKFILE.unlink(missing_ok=True)


def record_result(success: bool) -> None:
    if success:
        _FAIL_FILE.unlink(missing_ok=True)
        return
    fails = int(_FAIL_FILE.read_text().strip()) + 1 if _FAIL_FILE.exists() else 1
    _FAIL_FILE.write_text(str(fails))
    if fails >= FAIL_LIMIT:
        _ENABLE_FLAG.write_text("DISABLED")
        print(f"[{PROJECT}] WARN: auto-DISABLED after {fails} failures.")


def log_api_call(endpoint: str, detail: str, status: str) -> None:
    _API_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _API_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()}\t{endpoint}\t{detail}\t{status}\n")


def budget_available() -> bool:
    return _api_calls_used < MAX_API_CALLS


# ── Fetch logic (project-specific — fill in) ─────────────────────────────────

def fetch_all() -> dict:
    """Fetch from configured sources. Return {'items': [...], ...}.

    Rules:
      - Call budget_available() before every paid API call; increment
        _api_calls_used and log_api_call() after.
      - Basic spec matching only (price band, location, type).
      - No scoring, no LLM.
    """
    raise NotImplementedError("fill in project fetch logic")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="skip weekday guard")
    args = parser.parse_args()

    check_enabled()
    check_weekday(args.force)
    acquire_lock()
    try:
        data = fetch_all()
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = _OUT_DIR / f"{datetime.now():%Y-%m-%d}.json"
        out.write_text(json.dumps({
            "collected_at":   datetime.now(timezone.utc).isoformat(),
            "api_calls_used": _api_calls_used,
            "api_budget":     MAX_API_CALLS,
            **data,
        }, indent=2), encoding="utf-8")
        print(f"[{PROJECT}] OK → {out}")
        record_result(success=True)
    except Exception:
        record_result(success=False)
        raise
    finally:
        release_lock()


if __name__ == "__main__":
    main()
