#!/usr/bin/env python3
"""
Mac-side morning digest reader.
Reads mintworker's status.json + brief.txt from NAS RawSourceLibrary
and fires a native macOS notification.

Live:  ~/MountPoints/RawSourceLibrary/market/
Test:  ~/MountPoints/RawSourceLibrary/market/test/

Usage:
  python3 scripts/morning_digest.py           # live
  AI_CORE_ENV=test python3 scripts/morning_digest.py  # test output
"""
import json
import os
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

_ENV        = os.environ.get("AI_CORE_ENV", "live").strip().lower()
STALE_HOURS = 36


def _find_raw_mount():
    """Locate RawSourceLibrary under ~/MountPoints regardless of ConnectMeNow suffix."""
    mount_root = Path.home() / "MountPoints"
    if not mount_root.exists():
        return None
    for entry in mount_root.iterdir():
        if entry.name.startswith("RawSourceLibrary"):
            return entry
    return None


_RAW = _find_raw_mount()
_OUT_DIR    = (_RAW / "market" / ("test" if _ENV == "test" else "")) if _RAW else None
BRIEF_FILE  = _OUT_DIR / "brief.txt"  if _OUT_DIR else None
STATUS_FILE = _OUT_DIR / "status.json" if _OUT_DIR else None


def notify(title: str, body: str) -> None:
    safe_body  = body.replace('"', "'").replace("\\", "")
    safe_title = title.replace('"', "'")
    script = f'display notification "{safe_body}" with title "{safe_title}" sound name "default"'
    subprocess.run(["osascript", "-e", script])


def main() -> None:
    print(f"[digest] env={_ENV}  reading from {_OUT_DIR}")

    # ── NAS reachability ──────────────────────────────────────────────────────
    if _RAW is None:
        notify("ai-core ⚠", "RawSourceLibrary not found in ~/MountPoints — mount NAS first")
        print("ERROR: no RawSourceLibrary mount found under ~/MountPoints")
        return
    if not _OUT_DIR.exists():
        notify("ai-core ⚠", f"market/ dir missing on NAS — mintworker hasn't run yet")
        print(f"ERROR: {_OUT_DIR} does not exist — run mintworker once to create it")
        return

    # ── Status file ───────────────────────────────────────────────────────────
    if not STATUS_FILE.exists():
        notify("ai-core ⚠", "mintworker: no status.json on NAS — worker may not have run")
        print("ERROR: status.json missing")
        return

    status = json.loads(STATUS_FILE.read_text())
    ts     = datetime.fromisoformat(status["timestamp"])
    age    = datetime.now(timezone.utc) - ts
    age_h  = int(age.total_seconds() // 3600)
    age_m  = int(age.total_seconds() // 60)

    # ── Error state ───────────────────────────────────────────────────────────
    if status.get("status") != "success":
        stage = status.get("stage", "unknown")
        error = status.get("error", "unknown error")
        notify("ai-core ✗ Pipeline Error", f"[{stage}] {error[:120]}")
        print(f"ERROR reported by mintworker at stage={stage}:")
        print(f"  {error}")
        feed_errors = status.get("feed_errors", [])
        if feed_errors:
            print(f"  Feed errors ({len(feed_errors)}):")
            for e in feed_errors:
                print(f"    - {e}")
        return

    # ── Stale check ───────────────────────────────────────────────────────────
    if age > timedelta(hours=STALE_HOURS):
        notify("ai-core ⚠", f"mintworker brief is stale ({age_h}h old) — check cron")
        print(f"WARNING: brief is {age_h}h old")
        return

    # ── Partial feed failures ─────────────────────────────────────────────────
    feed_errors = status.get("feed_errors", [])
    if feed_errors:
        print(f"WARNING: {len(feed_errors)} feed(s) failed during last run:")
        for e in feed_errors:
            print(f"  - {e}")

    # ── Read brief ────────────────────────────────────────────────────────────
    if not BRIEF_FILE.exists():
        notify("ai-core ⚠", "status=success but brief.txt missing — disk issue?")
        print("ERROR: brief.txt missing despite success status")
        return

    line    = BRIEF_FILE.read_text(encoding="utf-8").strip()
    parts   = line.split("|", 3)
    content = parts[3] if len(parts) == 4 else line
    readable = content.replace("\\n", "\n")

    # Notification: first block (regime + top bullets), max 160 chars
    first_block = readable.split("\n\n")[0][:160]
    feed_warn   = f" ⚠ {len(feed_errors)} feed(s) failed" if feed_errors else ""
    notify(f"ai-core Daily Brief{feed_warn}", first_block)

    # Full brief to stdout (Claude Code / terminal can read this)
    headlines = status.get("headlines_collected", "?")
    model     = status.get("model", "?")
    print("=" * 60)
    print(f"MINTWORKER DAILY BRIEF  [{_ENV}]  age={age_m}m  "
          f"headlines={headlines}  model={model}")
    print("=" * 60)
    print(readable)
    print()
    if feed_errors:
        print(f"Feed failures: {len(feed_errors)}")
        for e in feed_errors:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
