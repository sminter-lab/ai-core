"""Data health check — the local "verify" step.

Scope (2026-07 re-scope): local hardware captures, verifies, and sorts data;
analysis happens offsite. This module answers one question after each collect:
"is the data fresh and trustworthy enough to analyze?"

Checks:
  1. Schwab token   — days until refresh-token expiry (7-day lifetime)
  2. Last run       — status + note of the most recent collector run
  3. Data freshness — hours since last positions/balances/quotes rows

Writes data/health.json (read by the offsite morning outlook) and prints a
one-line summary. Exit code 0 = healthy, 1 = degraded, 2 = broken.

Run after the collector:
  python -m workers.schwab.collector && python -m workers.schwab.verify
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

TOKEN_LIFETIME_DAYS = 7
FRESH_HOURS         = 26   # data older than this → degraded
HEALTH_PATH         = "data/health.json"


def token_days_left(token_path: Path) -> float | None:
    if not token_path.exists():
        return None
    data = json.loads(token_path.read_text())
    created_ts = data.get("creation_timestamp")
    if not created_ts:
        return None
    created = datetime.fromtimestamp(float(created_ts), tz=timezone.utc)
    expires = created + timedelta(days=TOKEN_LIFETIME_DAYS)
    return (expires - datetime.now(timezone.utc)).total_seconds() / 86400


def main() -> None:
    load_dotenv()
    now = datetime.now(timezone.utc)

    db_path    = Path(os.environ.get("SCHWAB_DB_PATH", "data/schwab/schwab.sqlite3"))
    token_path = Path(os.environ.get("SCHWAB_TOKEN_PATH", ".token.json")).expanduser()

    problems, warnings = [], []
    report: dict = {"checked_at": now.isoformat()}

    # 1. Token
    days_left = token_days_left(token_path)
    report["token_days_left"] = round(days_left, 2) if days_left is not None else None
    if days_left is None:
        problems.append("token file missing or unreadable")
    elif days_left <= 0:
        problems.append("Schwab token EXPIRED — run: python -m workers.schwab.auth")
    elif days_left <= 2:
        warnings.append(f"token expires in {days_left:.1f} days — re-auth soon")

    # 2 & 3. Last run + data freshness
    if not db_path.exists():
        problems.append(f"database missing: {db_path}")
    else:
        c = sqlite3.connect(db_path)
        row = c.execute(
            "SELECT ts, status, note FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            report["last_run"] = {"ts": row[0], "status": row[1], "note": row[2]}
            if row[1] != "OK":
                problems.append(f"last collector run {row[1]}: {row[2]}")
        else:
            problems.append("no collector runs recorded")

        report["freshness_hours"] = {}
        for table in ("positions", "balances", "quotes"):
            ts = c.execute(f"SELECT MAX(ts) FROM {table}").fetchone()[0]
            if ts is None:
                report["freshness_hours"][table] = None
                problems.append(f"{table}: no data")
                continue
            age_h = (now - datetime.fromisoformat(ts)).total_seconds() / 3600
            report["freshness_hours"][table] = round(age_h, 1)
            if age_h > FRESH_HOURS:
                warnings.append(f"{table} data is {age_h:.0f}h old")
        c.close()

    # Verdict
    if problems:
        status, code = "broken", 2
    elif warnings:
        status, code = "degraded", 1
    else:
        status, code = "healthy", 0

    report["status"]   = status
    report["problems"] = problems
    report["warnings"] = warnings

    out = Path(HEALTH_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"[verify] {status.upper()} — "
          f"token {report['token_days_left']}d left | "
          f"problems={len(problems)} warnings={len(warnings)} → {out}")
    for p in problems:
        print(f"[verify]   ✗ {p}")
    for w in warnings:
        print(f"[verify]   ⚠ {w}")

    sys.exit(code)


if __name__ == "__main__":
    main()
