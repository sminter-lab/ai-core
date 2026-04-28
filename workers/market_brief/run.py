"""mintworker — market brief pipeline.

Data sources (in priority order):
  1. NAS schwab_snapshot.json  — real quotes + movers written by Mac Schwab cron
  2. RSS (Yahoo Finance + CNBC) — macro narrative headlines only
  3. Ollama (llama3.1:8b)       — synthesis

If schwab_snapshot.json is missing or stale (>20h), falls back to RSS-only
and flags it in status.json so the digest notifier can surface the warning.

Output layout (under raw_root() / "market/"):
  brief.txt    — UPDATE_BOARD protocol line for Notion runner
  status.json  — last-run metadata (success/error, data sources used)

Test mode
---------
  AI_CORE_ENV=test python -m workers.market_brief.run
  Writes to raw_root() / "market/test/" — live output never touched.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests

from tools.shared_store import raw_root

# ── Config ────────────────────────────────────────────────────────────────────
_REPO        = Path(__file__).resolve().parents[2]
_ENV         = os.environ.get("AI_CORE_ENV", "live").strip().lower()
_OUT_DIR     = raw_root() / "market" / ("test" if _ENV == "test" else "")
_SCHWAB_SNAP = raw_root() / "market" / "schwab_snapshot.json"
SCHWAB_STALE_HOURS = 20   # snapshot older than this → warn + RSS-only fallback

OLLAMA_URL   = os.environ.get("OLLAMA_URL",            "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("MARKET_BRIEF_MODEL",    "llama3.1:8b")
MAX_ITEMS    = int(os.environ.get("MARKET_BRIEF_MAX_ITEMS", "5"))
TIMEOUT      = int(os.environ.get("MARKET_BRIEF_TIMEOUT",  "15"))
CATEGORY     = os.environ.get("MARKET_BRIEF_CATEGORY", "Finance")
METRIC       = os.environ.get("MARKET_BRIEF_METRIC",   "Daily Brief")

# Two reliable RSS feeds for macro narrative only
RSS_FEEDS = [
    "https://finance.yahoo.com/news/rssindex",
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "https://www.cnbc.com/id/19854910/device/rss/rss.html",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitize(s: str) -> str:
    if not s:
        return ""
    for src, dst in [("•", "-"), ("–", "-"), ("—", "-"),
                     ("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")]:
        s = s.replace(src, dst)
    s = s.encode("ascii", errors="ignore").decode("ascii")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    return s.strip()


def _write_status(status: str, **extra) -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    (_OUT_DIR / "status.json").write_text(
        json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "env":    _ENV,
            "status": status,
            **extra,
        }, indent=2),
        encoding="utf-8",
    )


def _fmt(val, suffix="", prefix="") -> str:
    if val is None:
        return "n/a"
    return f"{prefix}{val}{suffix}"


# ── Data sources ──────────────────────────────────────────────────────────────

def load_schwab_snapshot() -> tuple[dict | None, str | None]:
    """Return (snapshot_dict, warning_string).  warning is set if stale/missing."""
    if not _SCHWAB_SNAP.exists():
        return None, "schwab_snapshot.json not found — Mac Schwab cron may not have run"

    snap = json.loads(_SCHWAB_SNAP.read_text(encoding="utf-8"))
    ts   = datetime.fromisoformat(snap.get("snapshot_at", "2000-01-01T00:00:00+00:00"))
    age  = datetime.now(timezone.utc) - ts

    if age > timedelta(hours=SCHWAB_STALE_HOURS):
        return snap, f"schwab_snapshot.json is {int(age.total_seconds()//3600)}h old"

    return snap, None


def fetch_headlines() -> tuple[list[str], list[str]]:
    """Return (headlines, feed_errors) from RSS fallback feeds."""
    headlines, errors = [], []
    for url in RSS_FEEDS:
        try:
            r = requests.get(url, timeout=TIMEOUT,
                             headers={"User-Agent": "ai-core/market-brief"})
            r.raise_for_status()
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:MAX_ITEMS]:
                title = (entry.get("title") or "").strip()
                if title:
                    headlines.append(_sanitize(f"- {title}"))
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}")
    return headlines, errors


def build_book_check(quotes: dict) -> str:
    """Format the Your Book Check section from real Schwab quote data."""
    lines = []
    book = [
        ("AAPL",   "AAPL"),
        ("NVDA",   "NVDA"),
        ("AMD",    "AMD"),
        ("SPY",    "SPY"),
        ("QQQ",    "QQQ"),
        ("$VIX.X", "VIX"),
        ("BTC",    "BTC"),
    ]
    for sym, label in book:
        q = quotes.get(sym)
        if not q:
            lines.append(f"{label}: no data")
            continue
        last    = _fmt(q.get("last"),       prefix="$")
        chg_pct = q.get("change_pct")
        arrow   = ("+" if chg_pct >= 0 else "") if chg_pct is not None else ""
        pct_str = f"{arrow}{chg_pct:.2f}%" if chg_pct is not None else "n/a"
        lines.append(f"{label}: {last}  {pct_str}")
    return "\n".join(lines)


def build_movers_str(movers: dict) -> str:
    up   = movers.get("up",   [])[:3]
    down = movers.get("down", [])[:3]
    parts = []
    if up:
        parts.append("Leaders: " + ", ".join(
            f"{m['symbol']} +{m['change_pct']:.1f}%" for m in up))
    if down:
        parts.append("Laggards: " + ", ".join(
            f"{m['symbol']} {m['change_pct']:.1f}%" for m in down))
    return "  |  ".join(parts) if parts else "movers unavailable"


def summarise(regime: str, book_check: str, movers_str: str,
              headlines: list[str]) -> str:
    prompt = (
        "You are a trading ops assistant for a WEEKLY options seller.\n"
        "You have been given REAL market data — use it precisely.\n\n"
        f"REGIME (data-driven): {regime}\n\n"
        f"BOOK CHECK (real closing prices):\n{book_check}\n\n"
        f"NASDAQ MOVERS: {movers_str}\n\n"
        "MACRO HEADLINES (context only):\n"
        + "\n".join(headlines[:15]) + "\n\n"
        "Write a concise daily brief. Rules:\n"
        "- Plain text, '-' bullets only.\n"
        "- First line: Regime: <value from above — do not change it>\n"
        "- Section: Your Book Check — use the EXACT prices and % above, "
        "add one-line context if a move is significant (>1.5%).\n"
        "- Section: Macro — 3-4 bullets from headlines relevant to your book.\n"
        "- Section: Action bias — 2 bullets max, specific and risk-aware.\n"
        "- Confidence: High/Med/Low\n"
        "- Do not invent data. If a value is n/a, say so."
    )
    payload = {
        "model":   OLLAMA_MODEL,
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": 0.15, "num_predict": 320},
    }
    r = requests.post(f"{OLLAMA_URL}/api/generate",
                      json=payload, timeout=TIMEOUT * 8)
    r.raise_for_status()
    return _sanitize(r.json().get("response") or "") or "No significant items found."


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[market_brief] env={_ENV}  out={_OUT_DIR}")

    warnings = []

    # 1. Schwab snapshot (primary data source)
    snap, snap_warn = load_schwab_snapshot()
    if snap_warn:
        warnings.append(snap_warn)
        print(f"[market_brief] WARN: {snap_warn}")

    quotes    = (snap or {}).get("quotes",  {})
    movers    = (snap or {}).get("movers",  {})
    regime    = (snap or {}).get("regime",  "Unknown")
    book_check  = build_book_check(quotes)
    movers_str  = build_movers_str(movers)

    # 2. RSS headlines (macro narrative)
    headlines, feed_errors = fetch_headlines()
    if feed_errors:
        warnings.extend(feed_errors)
    if not headlines and not snap:
        _write_status("error", error="No Schwab snapshot and no RSS headlines",
                      stage="fetch")
        raise SystemExit("ERROR: no data from any source")

    # 3. Summarise
    try:
        summary = summarise(regime, book_check, movers_str, headlines)
    except Exception as exc:
        _write_status("error", error=f"{type(exc).__name__}: {exc}",
                      stage="summarise", warnings=warnings)
        raise SystemExit(f"ERROR: Ollama failed — {exc}")

    # 4. Write outputs
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    brief    = f"{ts}\n\n{summary}"
    safe     = brief.replace("\r\n", "\n").replace("\n", "\\n")
    (_OUT_DIR / "brief.txt").write_text(
        f"UPDATE_BOARD|{CATEGORY}|{METRIC}|{safe}\n", encoding="utf-8")

    _write_status(
        "success" if not warnings else "success_with_warnings",
        schwab_data=snap is not None,
        regime=regime,
        headlines_collected=len(headlines),
        feed_errors=feed_errors,
        warnings=warnings,
        model=OLLAMA_MODEL,
        output=str(_OUT_DIR / "brief.txt"),
    )

    print(f"[market_brief] OK — regime={regime}  "
          f"schwab={'yes' if snap else 'NO'}  "
          f"headlines={len(headlines)}  "
          f"warnings={len(warnings)}")


if __name__ == "__main__":
    main()
