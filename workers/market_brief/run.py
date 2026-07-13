"""mintworker — market capture pipeline (collection-only).

Scope (2026-07 re-scope): local hardware captures, verifies, and sorts data.
Complex market analysis/synthesis happens offsite (Claude). This worker no
longer calls Ollama — it assembles verified raw inputs for the offsite outlook.

Data sources (in priority order):
  1. NAS schwab_snapshot.json  — real quotes + movers written by Mac Schwab cron
  2. RSS (Yahoo Finance + CNBC) — macro narrative headlines only

If schwab_snapshot.json is missing or stale (>20h), falls back to RSS-only
and flags it in status.json so the offsite outlook can surface the warning.

Output layout (under raw_root() / "market/"):
  capture.json — structured raw inputs (regime, book quotes, movers, headlines)
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

MAX_ITEMS    = int(os.environ.get("MARKET_BRIEF_MAX_ITEMS", "5"))
TIMEOUT      = int(os.environ.get("MARKET_BRIEF_TIMEOUT",  "15"))

# Symbols the offsite outlook cares about (the book + market context)
BOOK_SYMBOLS = ["AAPL", "NVDA", "AMD", "SPY", "QQQ", "VIX", "BTC"]

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
    """Return (headlines, feed_errors) from RSS feeds."""
    headlines, errors = [], []
    for url in RSS_FEEDS:
        try:
            r = requests.get(url, timeout=TIMEOUT,
                             headers={"User-Agent": "ai-core/market-capture"})
            r.raise_for_status()
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:MAX_ITEMS]:
                title = (entry.get("title") or "").strip()
                if title:
                    headlines.append(_sanitize(title))
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}")
    return headlines, errors


def sort_book_quotes(quotes: dict) -> dict:
    """Verify and sort quote data for the book symbols; flag missing ones."""
    book, missing = {}, []
    for sym in BOOK_SYMBOLS:
        q = quotes.get(sym)
        if q:
            book[sym] = q
        else:
            missing.append(sym)
    return {"quotes": book, "missing": missing}


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[market_capture] env={_ENV}  out={_OUT_DIR}")

    warnings = []

    # 1. Schwab snapshot (primary data source)
    snap, snap_warn = load_schwab_snapshot()
    if snap_warn:
        warnings.append(snap_warn)
        print(f"[market_capture] WARN: {snap_warn}")

    quotes = (snap or {}).get("quotes", {})
    movers = (snap or {}).get("movers", {})
    regime = (snap or {}).get("regime", "Unknown")
    book   = sort_book_quotes(quotes)
    if book["missing"]:
        warnings.append(f"no quote data for: {', '.join(book['missing'])}")

    # 2. RSS headlines (macro narrative)
    headlines, feed_errors = fetch_headlines()
    if feed_errors:
        warnings.extend(feed_errors)
    if not headlines and not snap:
        _write_status("error", error="No Schwab snapshot and no RSS headlines",
                      stage="fetch")
        raise SystemExit("ERROR: no data from any source")

    # 3. Write verified capture for offsite analysis (no local synthesis)
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    capture = {
        "captured_at": ts,
        "env":         _ENV,
        "regime":      regime,
        "book":        book,
        "movers":      movers,
        "headlines":   headlines,
        "warnings":    warnings,
        "schwab_snapshot_present": snap is not None,
    }
    (_OUT_DIR / "capture.json").write_text(
        json.dumps(capture, indent=2), encoding="utf-8")

    _write_status(
        "success" if not warnings else "success_with_warnings",
        schwab_data=snap is not None,
        regime=regime,
        headlines_collected=len(headlines),
        feed_errors=feed_errors,
        warnings=warnings,
        output=str(_OUT_DIR / "capture.json"),
    )

    print(f"[market_capture] OK — regime={regime}  "
          f"schwab={'yes' if snap else 'NO'}  "
          f"headlines={len(headlines)}  "
          f"warnings={len(warnings)}")


if __name__ == "__main__":
    main()
