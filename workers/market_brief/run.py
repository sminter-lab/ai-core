"""studio — market brief processing entry point.

Reads raw market signals written by mintworker (NAS RawSourceLibrary) and
writes the processed brief to the NAS Documents share for downstream consumers
(Notion dashboard, daily_status, etc.).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tools.shared_store import docs_root


def main() -> None:
    ts = datetime.now(timezone.utc).isoformat()

    # ── TODO: replace stub with real RSS + Ollama signal processing ──────────
    brief = (
        f"{ts} | Regime: Calm | Headlines: TEST STUB (no RSS/Ollama yet) | "
        "Your Book Check: AAPL=No catalyst; NVDA=No catalyst; AMD=No catalyst; "
        "SPY=No catalyst; QQQ=No catalyst; VIX=No catalyst; BTC=No catalyst | "
        "Confidence: Med | Action bias: Keep size small; Favor defined risk"
    )

    # Write protocol line to NAS Documents so Notion runner can pick it up
    out_dir = docs_root() / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "brief.txt"
    out_path.write_text(f"UPDATE_BOARD|Finance|Daily Brief|{brief}\n", encoding="utf-8")

if __name__ == "__main__":
    main()
