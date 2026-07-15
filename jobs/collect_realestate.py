"""MINTWORKER — real estate COLLECTOR.

Role (framework v1.0): scrape, fetch, and store raw data. Nothing else.
No scoring, no Ollama, no digests — analysis happens on Mac Studio
(jobs/analyze_realestate.py).

Runaway protection (all required, see docs/framework_architecture.md):
  1. Enable flag   — control/realestate.enabled must contain "1"
  2. API budget    — hard cap on RapidAPI calls per run (config: max_api_calls)
  3. Weekday guard — exits immediately Sat/Sun
  4. Run lock      — PID lockfile prevents overlapping runs
  5. Backoff       — 3 consecutive failures auto-disables the job
  6. Cost log      — every RapidAPI call appended to logs/realestate_api_usage.log

Output:
  raw_root()/real_estate/raw/YYYY-MM-DD.json   (NAS shared store)

Usage:
  python -m jobs.collect_realestate [--force]   # --force skips weekday guard
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from tools.shared_store import raw_root

_BASE        = Path(__file__).resolve().parents[1]
_CONFIG      = json.loads((_BASE / "config" / "realestate.json").read_text(encoding="utf-8"))
_COLLECT     = _CONFIG["collector"]

_ENV         = os.environ.get("AI_CORE_ENV", "live").strip().lower()
_OUT_DIR     = raw_root() / "real_estate" / ("test/raw" if _ENV == "test" else "raw")
_ENABLE_FLAG = _BASE / "control" / "realestate.enabled"
_LOCKFILE    = _BASE / "control" / "realestate.collector.lock"
_FAIL_FILE   = _BASE / "control" / "realestate.failures"
_API_LOG     = _BASE / "logs" / "realestate_api_usage.log"

MAX_API_CALLS = int(_COLLECT.get("max_api_calls", 50))
MAX_ITEMS     = int(_COLLECT.get("max_items_per_feed", 10))
TIMEOUT       = int(_COLLECT.get("timeout_sec", 20))
FAIL_LIMIT    = int(_COLLECT.get("failure_backoff_threshold", 3))

ZILLOW_KEY  = os.environ.get("ZILLOW_RAPIDAPI_KEY", "")
ZILLOW_HOST = _COLLECT["zillow"]["host"]

_api_calls_used = 0


# ── Runaway protection ────────────────────────────────────────────────────────

def check_enabled() -> None:
    if not _ENABLE_FLAG.exists() or _ENABLE_FLAG.read_text().strip() != "1":
        print(f"[collect_re] Enable flag off or missing ({_ENABLE_FLAG}) — exiting.")
        sys.exit(0)


def check_weekday(force: bool) -> None:
    if force:
        return
    if datetime.now().weekday() >= 5:  # 5=Sat 6=Sun
        print("[collect_re] Weekend — exiting (use --force to override).")
        sys.exit(0)


def acquire_lock() -> None:
    if _LOCKFILE.exists():
        try:
            pid = int(_LOCKFILE.read_text().strip())
            os.kill(pid, 0)  # raises if not running
            print(f"[collect_re] Already running (pid {pid}) — exiting.")
            sys.exit(0)
        except (ValueError, ProcessLookupError, PermissionError):
            print("[collect_re] Stale lockfile — reclaiming.")
    _LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    _LOCKFILE.write_text(str(os.getpid()))


def release_lock() -> None:
    _LOCKFILE.unlink(missing_ok=True)


def record_result(success: bool) -> None:
    """Track consecutive failures; auto-disable after FAIL_LIMIT."""
    if success:
        _FAIL_FILE.unlink(missing_ok=True)
        return
    fails = 0
    if _FAIL_FILE.exists():
        try:
            fails = int(_FAIL_FILE.read_text().strip())
        except ValueError:
            fails = 0
    fails += 1
    _FAIL_FILE.write_text(str(fails))
    if fails >= FAIL_LIMIT:
        _ENABLE_FLAG.write_text("DISABLED")
        print(f"[collect_re] WARN: {fails} consecutive failures — job auto-DISABLED. "
              f"Fix the issue then write '1' to {_ENABLE_FLAG} to re-enable.")


def log_api_call(endpoint: str, location: str, status: str) -> None:
    _API_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _API_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()}\t{endpoint}\t{location}\t{status}\n")


def budget_available() -> bool:
    return _api_calls_used < MAX_API_CALLS


# ── Text extraction (basic spec matching only) ───────────────────────────────

def _sanitize(s: str) -> str:
    if not s:
        return ""
    for src, dst in [("•", "-"), ("–", "-"), ("—", "-"),
                     ("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")]:
        s = s.replace(src, dst)
    s = s.encode("ascii", errors="ignore").decode("ascii")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s).strip()


def _extract_price(text: str) -> float | None:
    m = re.search(r'\$\s*([\d,]+\.?\d*)\s*([kKmM]?)', text)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    suffix = m.group(2).lower()
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    return num if num >= 10_000 else None


def _extract_beds(text: str) -> int | None:
    m = re.search(r'(\d+)\s*(?:br|bed|bedroom)', text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _extract_sqft(text: str) -> float | None:
    m = re.search(r'([\d,]+)\s*(?:sq\.?\s*ft|sqft)', text, re.IGNORECASE)
    return float(m.group(1).replace(",", "")) if m else None


def _extract_cap_rate(text: str) -> float | None:
    m = re.search(r'(\d+\.?\d*)\s*%\s*cap', text, re.IGNORECASE)
    return float(m.group(1)) / 100 if m else None


def _extract_units(text: str) -> int:
    tl = text.lower()
    if re.search(r'4[\-\s]?unit|quad|fourplex', tl):
        return 4
    if re.search(r'3[\-\s]?unit|tri[\-\s]?plex', tl):
        return 3
    if re.search(r'2[\-\s]?unit|duplex', tl):
        return 2
    return 1


# ── Sources ───────────────────────────────────────────────────────────────────

def fetch_zillow() -> list[dict]:
    global _api_calls_used
    if not ZILLOW_KEY:
        print("[collect_re] No ZILLOW_RAPIDAPI_KEY — Craigslist only.")
        return []
    headers  = {"X-RapidAPI-Key": ZILLOW_KEY, "X-RapidAPI-Host": ZILLOW_HOST}
    params_c = _COLLECT["zillow"]["params"]
    listings: list[dict] = []
    for location in _COLLECT["zillow"]["locations"]:
        if not budget_available():
            print(f"[collect_re] API budget exhausted ({MAX_API_CALLS}) — stopping Zillow.")
            break
        _api_calls_used += 1
        try:
            r = requests.get(
                f"https://{ZILLOW_HOST}/propertyExtendedSearch",
                headers=headers,
                params={"location": location, **params_c},
                timeout=TIMEOUT,
            )
            log_api_call("propertyExtendedSearch", location, str(r.status_code))
            r.raise_for_status()
            for p in (r.json().get("props") or [])[:MAX_ITEMS]:
                price = p.get("price")
                if not price:
                    continue
                listings.append({
                    "kind":      "residential",
                    "source":    "zillow",
                    "address":   p.get("address", ""),
                    "city":      p.get("addressCity", ""),
                    "state":     p.get("addressState", ""),
                    "zip":       p.get("addressZipcode", ""),
                    "price":     float(price),
                    "beds":      p.get("beds"),
                    "baths":     p.get("baths"),
                    "sqft":      p.get("area"),
                    "units":     1,
                    "rent":      float(p["rentZestimate"]) if p.get("rentZestimate") else None,
                    "url":       "https://www.zillow.com" + (p.get("detailUrl") or ""),
                    "home_type": p.get("homeType", ""),
                })
        except Exception as exc:
            log_api_call("propertyExtendedSearch", location, f"ERROR:{type(exc).__name__}")
            print(f"[collect_re] Zillow error ({location!r}): {type(exc).__name__}: {exc}")
        time.sleep(0.5)
    return listings


def fetch_craigslist(markets: list[dict], kind: str) -> list[dict]:
    listings: list[dict] = []
    for market in markets:
        try:
            r = requests.get(market["url"], timeout=TIMEOUT,
                             headers={"User-Agent": "ai-core/real-estate-collector"})
            r.raise_for_status()
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:MAX_ITEMS]:
                title   = (entry.get("title") or "").strip()
                summary = entry.get("summary", "")
                price   = _extract_price(title) or _extract_price(summary)
                if not price:
                    continue  # basic spec filter: must have a price
                item = {
                    "kind":    kind,
                    "source":  "craigslist",
                    "city":    market["name"],
                    "state":   market.get("state", ""),
                    "price":   price,
                    "sqft":    _extract_sqft(title) or _extract_sqft(summary),
                    "url":     entry.get("link", ""),
                }
                if kind == "residential":
                    item.update({
                        "address": _sanitize(title),
                        "beds":    _extract_beds(title) or _extract_beds(summary),
                        "units":   _extract_units(title),
                        "rent":    None,
                    })
                else:
                    item.update({
                        "description":   _sanitize(title),
                        "summary":       _sanitize(re.sub(r"<[^>]+>", " ", summary))[:400],
                        "cap_rate_hint": _extract_cap_rate(title) or _extract_cap_rate(summary),
                    })
                listings.append(item)
        except Exception as exc:
            print(f"[collect_re] CL {kind} ({market['name']}): {type(exc).__name__}: {exc}")
        time.sleep(0.3)
    return listings


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Real estate collector (MintWorker)")
    parser.add_argument("--force", action="store_true", help="skip weekday guard")
    args = parser.parse_args()

    check_enabled()
    check_weekday(args.force)
    acquire_lock()

    try:
        print(f"[collect_re] env={_ENV}  budget={MAX_API_CALLS}  out={_OUT_DIR}")

        residential  = fetch_zillow()
        residential += fetch_craigslist(_COLLECT["craigslist_residential"], "residential")
        commercial   = fetch_craigslist(_COLLECT["craigslist_commercial"], "commercial")

        # De-dupe by URL
        seen: set[str] = set()
        def dedupe(items: list[dict]) -> list[dict]:
            out = []
            for it in items:
                u = it.get("url", "")
                if u and u in seen:
                    continue
                if u:
                    seen.add(u)
                out.append(it)
            return out

        residential = dedupe(residential)
        commercial  = dedupe(commercial)

        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        out_file = _OUT_DIR / f"{today}.json"
        out_file.write_text(json.dumps({
            "collected_at":   datetime.now(timezone.utc).isoformat(),
            "env":            _ENV,
            "api_calls_used": _api_calls_used,
            "api_budget":     MAX_API_CALLS,
            "zillow_used":    bool(ZILLOW_KEY),
            "residential":    residential,
            "commercial":     commercial,
        }, indent=2), encoding="utf-8")

        print(f"[collect_re] OK — residential={len(residential)} commercial={len(commercial)} "
              f"api_calls={_api_calls_used}/{MAX_API_CALLS} → {out_file}")
        record_result(success=True)
    except Exception:
        record_result(success=False)
        raise
    finally:
        release_lock()


if __name__ == "__main__":
    main()
