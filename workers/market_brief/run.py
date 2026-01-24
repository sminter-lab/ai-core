from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "ai_output.txt"

def main() -> None:
    ts = datetime.now(timezone.utc).isoformat()
    # keep it single-line for runner parsing; runner/store can store rich text
    brief = (
        f"{ts} | Regime: Calm | Headlines: TEST STUB (no RSS/Ollama yet) | "
        "Your Book Check: AAPL=No catalyst; NVDA=No catalyst; AMD=No catalyst; "
        "SPY=No catalyst; QQQ=No catalyst; VIX=No catalyst; BTC=No catalyst | "
        "Confidence: Med | Action bias: Keep size small; Favor defined risk"
    )
    OUT.write_text(f"UPDATE_BOARD|Finance|Daily Brief|{brief}\n", encoding="utf-8")

if __name__ == "__main__":
    main()
