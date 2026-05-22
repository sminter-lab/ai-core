"""Schwab token expiry monitor.

Reads .token.json and fires a macOS notification when the refresh token
is within WARN_DAYS of expiry. Run daily from cron (before market open).

Schwab refresh tokens expire every 7 days and require browser re-auth.
This script gives you a heads-up so you re-auth on your schedule,
not when the 7am brief fails.

Re-auth command:
  cd ~/ai-core && source .venv/bin/activate && python -m workers.schwab.auth
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

WARN_DAYS   = 2      # notify this many days before expiry
EXPIRY_DAYS = 7      # Schwab refresh token lifetime


def notify(title: str, body: str) -> None:
    safe_body  = body.replace('"', "'")
    safe_title = title.replace('"', "'")
    subprocess.run(
        ["osascript", "-e",
         f'display notification "{safe_body}" with title "{safe_title}" sound name "default"'],
        check=False,
    )


def main() -> None:
    load_dotenv()

    token_path = Path(
        os.environ.get("SCHWAB_TOKEN_PATH", ".token.json")
    ).expanduser().resolve()

    if not token_path.exists():
        notify("ai-core Schwab ⚠", "Token file missing — re-auth required")
        print("ERROR: token file not found:", token_path)
        sys.exit(1)

    data = json.loads(token_path.read_text())

    # schwab-py stores creation_timestamp at top level
    created_ts = data.get("creation_timestamp")
    # Also check expires_at inside the token dict (access token expiry, not refresh)
    token      = data.get("token", {})
    expires_at = token.get("expires_at")

    now = datetime.now(timezone.utc)

    # Refresh token expiry = creation + 7 days
    if created_ts:
        created    = datetime.fromtimestamp(float(created_ts), tz=timezone.utc)
        expires    = created + timedelta(days=EXPIRY_DAYS)
        remaining  = expires - now
        days_left  = remaining.total_seconds() / 86400

        print(f"[token_check] created:  {created.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"[token_check] expires:  {expires.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"[token_check] remaining: {days_left:.1f} days")

        if days_left <= 0:
            notify(
                "ai-core Schwab ✗ TOKEN EXPIRED",
                "Schwab token is expired — run: python -m workers.schwab.auth"
            )
            print("STATUS: EXPIRED — re-auth required")
            sys.exit(2)

        elif days_left <= WARN_DAYS:
            notify(
                f"ai-core Schwab ⚠ Token expires in {days_left:.0f}d",
                "Run: cd ~/ai-core && python -m workers.schwab.auth"
            )
            print(f"STATUS: WARNING — expires in {days_left:.1f} days")

        else:
            print(f"STATUS: OK — {days_left:.1f} days remaining")
    else:
        # Fallback: check expires_at on the access token
        print("[token_check] no creation_timestamp found — checking expires_at")
        if expires_at:
            exp = datetime.fromtimestamp(float(expires_at), tz=timezone.utc)
            print(f"[token_check] access token expires_at: {exp}")
        notify("ai-core Schwab ⚠", "Token structure unexpected — verify manually")
        sys.exit(1)


if __name__ == "__main__":
    main()
