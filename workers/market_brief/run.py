"""mintworker — market brief pipeline.

Fetches RSS headlines, summarises via local Ollama, and writes the result to
the NAS RawSourceLibrary share so downstream consumers (studio, Notion runner,
Mac digest notifier) can pick it up.

Output layout (under raw_root() / "market/"):
  brief.txt    — UPDATE_BOARD protocol line for Notion runner
  status.json  — last-run metadata (success/error, timestamps, counts)

Test mode
---------
Set  AI_CORE_ENV=test  to write to  raw_root() / "market/test/"  instead.
Live output is never touched during test runs.

  AI_CORE_ENV=test python -m workers.market_brief.run
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

from tools.shared_store import raw_root

# ── Config ────────────────────────────────────────────────────────────────────
_REPO        = Path(__file__).resolve().parents[2]
SOURCES_FILE = _REPO / "workers" / "market_brief" / "sources.txt"

_ENV     = os.environ.get("AI_CORE_ENV", "live").strip().lower()
_OUT_DIR = raw_root() / "market" / ("test" if _ENV == "test" else "")

OLLAMA_URL   = os.environ.get("OLLAMA_URL",            "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("MARKET_BRIEF_MODEL",    "llama3.1:8b")
MAX_ITEMS    = int(os.environ.get("MARKET_BRIEF_MAX_ITEMS", "6"))
TIMEOUT      = int(os.environ.get("MARKET_BRIEF_TIMEOUT",  "15"))
CATEGORY     = os.environ.get("MARKET_BRIEF_CATEGORY", "Finance")
METRIC       = os.environ.get("MARKET_BRIEF_METRIC",   "Daily Brief")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitize(s: str) -> str:
    """Strip non-ASCII and control characters; normalise common unicode."""
    if not s:
        return ""
    for src, dst in [("\u2022", "-"), ("\u2013", "-"), ("\u2014", "-"),
                     ("\u201c", '"'), ("\u201d", '"'), ("\u2018", "'"), ("\u2019", "'")]:
        s = s.replace(src, dst)
    s = s.encode("ascii", errors="ignore").decode("ascii")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    return s.strip()


def _write_status(status: str, **extra) -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "env":       _ENV,
        "status":    status,
        **extra,
    }
    (_OUT_DIR / "status.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


# ── Pipeline stages ───────────────────────────────────────────────────────────

def fetch_headlines() -> tuple[list[str], list[str]]:
    """Return (headlines, feed_errors)."""
    if not SOURCES_FILE.exists():
        raise FileNotFoundError(f"sources.txt not found: {SOURCES_FILE}")

    urls = [
        ln.strip()
        for ln in SOURCES_FILE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]

    headlines, errors = [], []
    for url in urls:
        try:
            r = requests.get(
                url, timeout=TIMEOUT, headers={"User-Agent": "ai-core/market-brief"}
            )
            r.raise_for_status()
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:MAX_ITEMS]:
                title = (entry.get("title") or "").strip()
                link  = (entry.get("link")  or "").strip()
                if title:
                    line = f"- {title}"
                    if link:
                        line += f" ({link})"
                    headlines.append(_sanitize(line))
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")

    return headlines, errors


def summarise(headlines: list[str]) -> str:
    prompt = (
        "You are a trading ops assistant for a WEEKLY options seller.\n"
        "Create a short daily market brief from the headlines.\n\n"
        "Output rules:\n"
        "- Plain text ONLY. Use '-' for bullets (ASCII hyphen).\n"
        "- Max 8 bullets total.\n"
        "- Start with: Regime: Calm / Normal / Risk-On / Risk-Off.\n"
        "- Include a section exactly titled: Your Book Check\n"
        "  Cover: AAPL, NVDA, AMD, SPY, QQQ, VIX, BTC.\n"
        "  If no catalyst, say: No major catalyst spotted.\n"
        "- Include political items ONLY if they clearly impact markets "
        "(tariffs/export controls/rates/energy/semis).\n"
        "- End with:\n"
        "  Confidence: High/Med/Low\n"
        "  Action bias (weekly options seller): 2 bullets max, risk-aware.\n\n"
        "Headlines:\n" + "\n".join(headlines[:30])
    )
    payload = {
        "model":   OLLAMA_MODEL,
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": 0.2, "num_predict": 260},
    }
    r = requests.post(
        f"{OLLAMA_URL}/api/generate", json=payload, timeout=TIMEOUT * 6
    )
    r.raise_for_status()
    return _sanitize(r.json().get("response") or "") or "No significant items found."


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[market_brief] env={_ENV}  out={_OUT_DIR}")

    # 1. Fetch
    try:
        headlines, feed_errors = fetch_headlines()
    except FileNotFoundError as exc:
        _write_status("error", error=str(exc), stage="fetch")
        raise SystemExit(f"ERROR: {exc}")

    if not headlines:
        _write_status(
            "error",
            error="All feeds returned empty or failed",
            feed_errors=feed_errors,
            stage="fetch",
        )
        raise SystemExit("ERROR: no headlines collected — check feed URLs and network")

    # 2. Summarise
    try:
        summary = summarise(headlines)
    except Exception as exc:
        _write_status(
            "error",
            error=f"{type(exc).__name__}: {exc}",
            feed_errors=feed_errors,
            headlines_collected=len(headlines),
            stage="summarise",
        )
        raise SystemExit(f"ERROR: Ollama summarise failed — {exc}")

    # 3. Write outputs
    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    brief    = f"{ts}\n\n{summary}"
    safe     = brief.replace("\r\n", "\n").replace("\n", "\\n")
    protocol = f"UPDATE_BOARD|{CATEGORY}|{METRIC}|{safe}\n"

    (_OUT_DIR / "brief.txt").write_text(protocol, encoding="utf-8")

    _write_status(
        "success",
        feeds_attempted=len([
            ln for ln in SOURCES_FILE.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]),
        headlines_collected=len(headlines),
        feed_errors=feed_errors,
        model=OLLAMA_MODEL,
        output=str(_OUT_DIR / "brief.txt"),
    )

    if feed_errors:
        print(f"[market_brief] WARNING: {len(feed_errors)} feed(s) failed:")
        for e in feed_errors:
            print(f"  - {e}")

    print(f"[market_brief] OK — {len(headlines)} headlines → {_OUT_DIR / 'brief.txt'}")


if __name__ == "__main__":
    main()
