import os
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

SOURCES_FILE = Path(__file__).with_name("sources.txt")

OUTPUT_FILE = Path(os.environ.get("AI_OUTPUT_FILE", "/opt/repos/ai-core/ai_output.txt"))

CATEGORY = os.environ.get("MARKET_BRIEF_CATEGORY", "Finance")
METRIC = os.environ.get("MARKET_BRIEF_METRIC", "Daily Brief")

MAX_ITEMS_PER_FEED = int(os.environ.get("MARKET_BRIEF_MAX_ITEMS", "5"))
TIMEOUT_SEC = int(os.environ.get("MARKET_BRIEF_TIMEOUT", "15"))

def fetch_rss(url: str):
    r = requests.get(url, timeout=TIMEOUT_SEC, headers={"User-Agent": "ai-core/market-brief"})
    r.raise_for_status()
    return feedparser.parse(r.text)

def ollama_summarize(headlines: list[str]) -> str:
    model = os.environ.get("MARKET_BRIEF_MODEL", "llama3.1:8b")
    ollama_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

    prompt = (
        "You are a trading ops assistant for a WEEKLY options seller.\n"
        "Create a short daily market brief from headlines.\n\n"
        "Output rules:\n"
        "- Plain text.\n"
        "- Max 8 bullets total.\n"
        "- Start with: Regime: Calm / Normal / Risk-On / Risk-Off.\n"
        "- Include a section: 'Your Book Check' covering AAPL, NVDA, AMD, SPY, QQQ, VIX, BTC.\n"
        "  If a ticker has no catalyst in headlines, say 'No major catalyst spotted'.\n"
        "- Only include political items if they clearly impact markets (tariffs/export controls/rates/energy/semis) and explain the linkage in 1 clause.\n"
        "- End with:\n"
        "  Confidence: High/Med/Low\n"
        "  Action bias (weekly options seller): 2 bullets max, general and risk-aware.\n\n"
        "Headlines:\n"
        + "\n".join(headlines[:25])
    )

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 220},
    }

    r = requests.post(f"{ollama_url}/api/generate", json=payload, timeout=TIMEOUT_SEC * 4)
    r.raise_for_status()
    data = r.json()
    return (data.get("response") or "").strip() or "No significant items found."

def main():
    ts = datetime.now(timezone.utc).isoformat()

    if not SOURCES_FILE.exists():
        raise SystemExit(f"Missing sources file: {SOURCES_FILE}")

    urls = [
        line.strip()
        for line in SOURCES_FILE.read_text().splitlines()
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
                    headlines.append(f"- {title}" + (f" ({link})" if link else ""))
        except Exception as e:
            headlines.append(f"- [FEED ERROR] {url}: {e.__class__.__name__}")

    try:
        summary = ollama_summarize(headlines) if headlines else "No significant items found."
    except Exception as e:
        summary = f"SUMMARY ERROR: {e.__class__.__name__}: {e}"

    brief = f"{ts}\n\n{summary}"

    # Runner parses line-by-line, so keep UPDATE_BOARD as exactly one line:
    safe_brief = brief.replace("\r\n", "\n").replace("\n", "\\n")

    OUTPUT_FILE.write_text(
        f"UPDATE_BOARD|{CATEGORY}|{METRIC}|{safe_brief}\n",
        encoding="utf-8",
    )

if __name__ == "__main__":
    main()
