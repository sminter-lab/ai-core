import os
import re
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

REPO_DIR = Path(os.environ.get("AI_CORE_REPO", "/opt/repos/ai-core"))
SOURCES_FILE = REPO_DIR / "workers" / "market_brief" / "sources.txt"
OUTPUT_FILE = Path(os.environ.get("AI_OUTPUT_FILE", str(REPO_DIR / "ai_output.txt")))

CATEGORY = os.environ.get("MARKET_BRIEF_CATEGORY", "Finance")
METRIC = os.environ.get("MARKET_BRIEF_METRIC", "Daily Brief")

MAX_ITEMS_PER_FEED = int(os.environ.get("MARKET_BRIEF_MAX_ITEMS", "6"))
TIMEOUT_SEC = int(os.environ.get("MARKET_BRIEF_TIMEOUT", "15"))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("MARKET_BRIEF_MODEL", "llama3.1:8b")


def fetch_rss(url: str):
    r = requests.get(
        url,
        timeout=TIMEOUT_SEC,
        headers={"User-Agent": "ai-core/market-brief"},
    )
    r.raise_for_status()
    return feedparser.parse(r.text)


def _ascii_sanitize(s: str) -> str:
    """
    Prevent smart quotes / unicode bullets / odd characters from breaking logs or Notion.
    - Convert common bullets to '-'
    - Strip non-ascii
    - Collapse whitespace a bit
    """
    if not s:
        return ""

    # Normalize common unicode bullets to "-"
    s = s.replace("•", "-").replace("–", "-").replace("—", "-")

    # Normalize fancy quotes to plain
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")

    # Strip non-ascii
    s = s.encode("ascii", errors="ignore").decode("ascii")

    # Remove any accidental control chars
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)

    return s.strip()


def ollama_summarize(headlines: list[str]) -> str:
    """
    Ask local/remote Ollama for a short daily market brief.
    Return plain text.
    """
    # Force ASCII-safe bullets in the instruction itself
    prompt = (
        "You are a trading ops assistant for a WEEKLY options seller.\n"
        "Create a short daily market brief from the headlines.\n\n"
        "Output rules:\n"
        "- Plain text ONLY.\n"
        "- Use '-' for bullets (ASCII hyphen), no unicode bullets.\n"
        "- Max 8 bullets total.\n"
        "- Start with: Regime: Calm / Normal / Risk-On / Risk-Off.\n"
        "- Include a section exactly titled: Your Book Check\n"
        "  Cover: AAPL, NVDA, AMD, SPY, QQQ, VIX, BTC.\n"
        "  If no catalyst, say: No major catalyst spotted.\n"
        "- Include political items ONLY if they clearly impact markets "
        "(tariffs/export controls/rates/energy/semis) and state the linkage in one clause.\n"
        "- End with:\n"
        "  Confidence: High/Med/Low\n"
        "  Action bias (weekly options seller): 2 bullets max, risk-aware.\n\n"
        "Headlines:\n"
        + "\n".join(headlines[:30])
    )

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 260,
        },
    }

    r = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json=payload,
        timeout=TIMEOUT_SEC * 6,
    )
    r.raise_for_status()
    data = r.json()
    return (data.get("response") or "").strip() or "No significant items found."


def main():
    ts = datetime.now(timezone.utc).isoformat()

    if not SOURCES_FILE.exists():
        raise SystemExit(f"Missing sources file: {SOURCES_FILE}")

    urls = [
        line.strip()
        for line in SOURCES_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    headlines: list[str] = []
    for url in urls:
        try:
            feed = fetch_rss(url)
            for it in feed.entries[:MAX_ITEMS_PER_FEED]:
                title = (it.get("title") or "").strip()
                link = (it.get("link") or "").strip()
                if title:
                    # Keep headlines readable and short
                    line = f"- {title}"
                    if link:
                        line += f" ({link})"
                    headlines.append(_ascii_sanitize(line))
        except Exception as e:
            headlines.append(_ascii_sanitize(f"- [FEED ERROR] {url}: {e.__class__.__name__}"))

    # If all feeds fail, still give a deterministic output
    if not headlines:
        headlines = ["- No headlines collected (all feeds empty or failed)."]

    try:
        summary = ollama_summarize(headlines)
    except Exception as e:
        summary = f"SUMMARY ERROR: {e.__class__.__name__}: {e}"

    summary = _ascii_sanitize(summary)

    # Compose brief text. Runner needs UPDATE_BOARD to be ONE physical line.
    brief = f"{ts}\n\n{summary}"

    # Escape newlines so the UPDATE_BOARD line stays single-line
    safe_brief = brief.replace("\r\n", "\n").replace("\n", "\\n")

    OUTPUT_FILE.write_text(
        f"UPDATE_BOARD|{CATEGORY}|{METRIC}|{safe_brief}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
