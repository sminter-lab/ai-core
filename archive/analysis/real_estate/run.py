"""mintworker — real estate deal scanner.

Data sources (in priority order):
  Residential:
    1. Zillow via RapidAPI  — requires ZILLOW_RAPIDAPI_KEY env var
    2. Craigslist RSS       — free fallback, Southeast markets
  Commercial:
    1. Craigslist CRE RSS  — Atlanta metro + Southeast markets

Financial pre-filter runs before Ollama to save inference calls.
  Residential: cash_flow >= $500/mo AND CoC >= 8% at 6.3% / 20% down
  Commercial:  cap_rate >= 8% AND DSCR >= 1.25

Scoring (Ollama — only for pre-filter survivors):
  Score >= 8  → GREEN flag, surface in morning digest
  Score  = 7  → FLAGGED, include with note
  Score  < 7  → skip, log only

Output layout (under raw_root() / "real_estate/"):
  digest.json            — all qualifying deals with scores + metrics
  residential_brief.txt  — UPDATE_BOARD line for Notion runner
  commercial_brief.txt   — UPDATE_BOARD line for Notion runner
  status.json            — last-run metadata and counts

Test mode:
  AI_CORE_ENV=test python -m workers.real_estate.run
  Writes to raw_root() / "real_estate/test/" — live output never touched.

Usage:
  python -m workers.real_estate.run [--job residential|commercial|both]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

from tools.shared_store import raw_root
from .criteria import (
    commercial_metrics,
    commercial_passes,
    in_growth_market,
    in_landlord_friendly_state,
    residential_metrics,
    residential_passes,
)

# ── Config ────────────────────────────────────────────────────────────────────
_ENV          = os.environ.get("AI_CORE_ENV", "live").strip().lower()
_OUT_DIR      = raw_root() / "real_estate" / ("test" if _ENV == "test" else "")
_SOURCES_FILE = Path(__file__).parent / "sources.json"

OLLAMA_URL    = os.environ.get("OLLAMA_URL",          "http://127.0.0.1:11434")
OLLAMA_MODEL  = os.environ.get("RE_OLLAMA_MODEL",     "llama3.1:8b")
TIMEOUT       = int(os.environ.get("RE_TIMEOUT",      "20"))
MAX_ITEMS     = int(os.environ.get("RE_MAX_ITEMS",    "10"))  # listings per feed

ZILLOW_KEY    = os.environ.get("ZILLOW_RAPIDAPI_KEY", "")
ZILLOW_HOST   = "zillow-com1.p.rapidapi.com"

CATEGORY_RES  = os.environ.get("RE_RES_CATEGORY", "Real Estate")
METRIC_RES    = os.environ.get("RE_RES_METRIC",   "Residential Deals")
CATEGORY_COM  = os.environ.get("RE_COM_CATEGORY", "Real Estate")
METRIC_COM    = os.environ.get("RE_COM_METRIC",   "Commercial Deals")

# $/sqft/yr NNN rent estimate for small SE industrial/retail (no listing data available)
_CRE_RENT_PSF = 12.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitize(s: str) -> str:
    if not s:
        return ""
    for src, dst in [
        ("•", "-"), ("–", "-"), ("—", "-"),
        ("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"),
    ]:
        s = s.replace(src, dst)
    s = s.encode("ascii", errors="ignore").decode("ascii")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    return s.strip()


def _load_sources() -> dict:
    return json.loads(_SOURCES_FILE.read_text(encoding="utf-8"))


def _write_status(status: str, **extra) -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    (_OUT_DIR / "status.json").write_text(
        json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "env":       _ENV,
            "status":    status,
            **extra,
        }, indent=2),
        encoding="utf-8",
    )


def _extract_price(text: str) -> float | None:
    """Return first dollar amount from text as float, or None."""
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


# ── Data sources ──────────────────────────────────────────────────────────────

def fetch_zillow_residential(locations: list[str], params: dict) -> list[dict]:
    if not ZILLOW_KEY:
        return []
    headers = {"X-RapidAPI-Key": ZILLOW_KEY, "X-RapidAPI-Host": ZILLOW_HOST}
    listings: list[dict] = []
    for location in locations:
        try:
            r = requests.get(
                f"https://{ZILLOW_HOST}/propertyExtendedSearch",
                headers=headers,
                params={
                    "location":  location,
                    "home_type": params.get("home_type", "Houses,Multi-family"),
                    "minBeds":   params.get("minBeds", 2),
                    "maxBeds":   params.get("maxBeds", 4),
                    "minPrice":  params.get("minPrice", 80000),
                    "maxPrice":  params.get("maxPrice", 600000),
                    "sort":      params.get("sort", "Newest"),
                },
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            props = (r.json().get("props") or [])[:MAX_ITEMS]
            for p in props:
                price = p.get("price")
                if not price:
                    continue
                listings.append({
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
            print(f"[real_estate] Zillow error ({location!r}): {type(exc).__name__}: {exc}")
        time.sleep(0.5)
    return listings


def fetch_craigslist_residential(markets: list[dict]) -> list[dict]:
    listings: list[dict] = []
    for market in markets:
        try:
            r = requests.get(
                market["url"], timeout=TIMEOUT,
                headers={"User-Agent": "ai-core/real-estate-scanner"},
            )
            r.raise_for_status()
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:MAX_ITEMS]:
                title   = (entry.get("title") or "").strip()
                summary = entry.get("summary", "")
                price   = _extract_price(title) or _extract_price(summary)
                if not price:
                    continue
                listings.append({
                    "source":  "craigslist",
                    "address": _sanitize(title),
                    "city":    market["name"],
                    "state":   market.get("state", ""),
                    "price":   price,
                    "beds":    _extract_beds(title) or _extract_beds(summary),
                    "sqft":    _extract_sqft(title) or _extract_sqft(summary),
                    "units":   _extract_units(title),
                    "rent":    None,
                    "url":     entry.get("link", ""),
                })
        except Exception as exc:
            print(f"[real_estate] CL residential ({market['name']}): {type(exc).__name__}: {exc}")
        time.sleep(0.3)
    return listings


def fetch_craigslist_commercial(markets: list[dict]) -> list[dict]:
    listings: list[dict] = []
    for market in markets:
        try:
            r = requests.get(
                market["url"], timeout=TIMEOUT,
                headers={"User-Agent": "ai-core/real-estate-scanner"},
            )
            r.raise_for_status()
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:MAX_ITEMS]:
                title   = (entry.get("title") or "").strip()
                summary = (entry.get("summary") or "")
                price   = _extract_price(title) or _extract_price(summary)
                if not price:
                    continue
                summary_clean = _sanitize(re.sub(r"<[^>]+>", " ", summary))[:400]
                listings.append({
                    "source":        "craigslist",
                    "description":   _sanitize(title),
                    "summary":       summary_clean,
                    "city":          market["name"],
                    "state":         market.get("state", ""),
                    "price":         price,
                    "sqft":          _extract_sqft(title) or _extract_sqft(summary),
                    "cap_rate_hint": _extract_cap_rate(title) or _extract_cap_rate(summary),
                    "url":           entry.get("link", ""),
                })
        except Exception as exc:
            print(f"[real_estate] CL commercial ({market['name']}): {type(exc).__name__}: {exc}")
        time.sleep(0.3)
    return listings


# ── Ollama scoring ────────────────────────────────────────────────────────────

def _ollama_score(prompt: str) -> tuple[int, list[str]]:
    """Return (score_1_to_10, [reason, reason, reason]) from Ollama."""
    payload = {
        "model":   OLLAMA_MODEL,
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": 0.1, "num_predict": 220},
    }
    r = requests.post(
        f"{OLLAMA_URL}/api/generate", json=payload, timeout=TIMEOUT * 6)
    r.raise_for_status()
    text = _sanitize(r.json().get("response") or "")

    m     = re.search(r'[Ss]core[:\s]+(\d+)\s*/\s*10', text)
    score = int(m.group(1)) if m else 0
    reasons = [_sanitize(x.strip()) for x in re.findall(r'-\s*(.+)', text) if x.strip()][:3]
    return score, reasons


def _score_residential(listing: dict, metrics: dict, rent: float) -> tuple[int, list[str]]:
    state_ok  = "yes" if in_landlord_friendly_state(listing.get("state", "")) else "unknown"
    market_ok = "yes" if in_growth_market(listing.get("city", "")) else "unknown"
    prompt = (
        "Evaluate this rental property as an investment. Score 1-10 and give 3 bullet reasons.\n"
        "Criteria: cash flow >$500/mo, CoC >=8%, 1-4 units, landlord-friendly state, "
        "population growth market.\n\n"
        f"- Address: {listing.get('address', 'N/A')}, "
        f"{listing.get('city', '')}, {listing.get('state', '')}\n"
        f"- Price: ${listing['price']:,.0f}\n"
        f"- Units: {listing.get('units', 1)}\n"
        f"- Beds: {listing.get('beds') or 'N/A'}\n"
        f"- Est. monthly rent: ${rent:,.0f}\n"
        f"- Monthly cash flow: ${metrics['cash_flow']:,.0f}\n"
        f"- Cash-on-cash return: {metrics['coc'] * 100:.1f}%\n"
        f"- Monthly mortgage: ${metrics['mortgage']:,.0f}\n"
        f"- Landlord-friendly state: {state_ok}\n"
        f"- Population growth market: {market_ok}\n\n"
        "Respond with exactly:\nScore: X/10\nReasons:\n- reason 1\n- reason 2\n- reason 3"
    )
    return _ollama_score(prompt)


def _score_commercial(listing: dict, metrics: dict) -> tuple[int, list[str]]:
    prompt = (
        "Evaluate this commercial property as an investment. Score 1-10 and give 3 bullet reasons.\n"
        "Criteria: cap rate >=8%, DSCR >=1.25, value-add potential, "
        "Metro Atlanta or Southeast market.\n\n"
        f"- Description: {listing.get('description', 'N/A')}\n"
        f"- Market: {listing.get('city', '')}, {listing.get('state', '')}\n"
        f"- Price: ${listing['price']:,.0f}\n"
        f"- Sqft: {listing.get('sqft') or 'N/A'}\n"
        f"- Cap rate: {metrics['cap_rate'] * 100:.1f}%\n"
        f"- NOI: ${metrics['noi']:,.0f}/yr\n"
        f"- DSCR: {metrics['dscr']:.2f}\n"
        f"- Annual debt service: ${metrics['annual_debt']:,.0f}\n\n"
        "Respond with exactly:\nScore: X/10\nReasons:\n- reason 1\n- reason 2\n- reason 3"
    )
    return _ollama_score(prompt)


# ── Processing pipelines ──────────────────────────────────────────────────────

def process_residential(listings: list[dict]) -> tuple[list[dict], int]:
    """Pre-filter → score → return (qualifying_deals, prefilter_rejects)."""
    results: list[dict] = []
    seen_urls: set[str] = set()
    prefilter_rejects = 0

    for listing in listings:
        url = listing.get("url", "")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)

        price = listing.get("price")
        if not price or price < 10_000:
            continue

        # Use Zillow rentZestimate if available; else conservative 0.75% rule
        rent    = listing.get("rent") or price * 0.0075
        metrics = residential_metrics(price, rent)

        if not residential_passes(metrics):
            prefilter_rejects += 1
            continue

        try:
            score, reasons = _score_residential(listing, metrics, rent)
        except Exception as exc:
            print(f"[real_estate] Ollama residential error: {type(exc).__name__}: {exc}")
            continue

        if score < 7:
            continue

        results.append({
            "type":      "residential",
            "flag":      "GREEN" if score >= 8 else "FLAGGED",
            "score":     score,
            "reasons":   reasons,
            "price":     price,
            "rent":      round(rent, 0),
            "cash_flow": round(metrics["cash_flow"], 0),
            "coc_pct":   round(metrics["coc"] * 100, 1),
            "mortgage":  round(metrics["mortgage"], 0),
            "down":      round(metrics["down"], 0),
            "address":   listing.get("address", ""),
            "city":      listing.get("city", ""),
            "state":     listing.get("state", ""),
            "units":     listing.get("units", 1),
            "beds":      listing.get("beds"),
            "url":       url,
            "source":    listing.get("source", ""),
        })

    results.sort(key=lambda x: (-x["score"], -x["cash_flow"]))
    return results, prefilter_rejects


def process_commercial(listings: list[dict]) -> tuple[list[dict], int]:
    """Pre-filter → score → return (qualifying_deals, prefilter_rejects)."""
    results: list[dict] = []
    seen_urls: set[str] = set()
    prefilter_rejects = 0

    for listing in listings:
        url = listing.get("url", "")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)

        price = listing.get("price")
        if not price or price < 100_000:
            continue

        # Estimate gross annual income from best available signal
        cap_hint = listing.get("cap_rate_hint")
        sqft     = listing.get("sqft") or 0
        if cap_hint:
            # NOI = price * cap_rate; back out gross assuming 10% vacancy
            gross_income = (price * cap_hint) / (1 - 0.10)
        elif sqft:
            gross_income = sqft * _CRE_RENT_PSF
        else:
            # Assume minimum threshold — marginal candidate, Ollama will score it low
            gross_income = (price * 0.08) / (1 - 0.10)

        metrics = commercial_metrics(price, gross_income)

        if not commercial_passes(metrics):
            prefilter_rejects += 1
            continue

        try:
            score, reasons = _score_commercial(listing, metrics)
        except Exception as exc:
            print(f"[real_estate] Ollama commercial error: {type(exc).__name__}: {exc}")
            continue

        if score < 7:
            continue

        results.append({
            "type":        "commercial",
            "flag":        "GREEN" if score >= 8 else "FLAGGED",
            "score":       score,
            "reasons":     reasons,
            "price":       price,
            "noi":         round(metrics["noi"], 0),
            "cap_rate":    round(metrics["cap_rate"] * 100, 1),
            "dscr":        round(metrics["dscr"], 2),
            "sqft":        sqft or None,
            "city":        listing.get("city", ""),
            "state":       listing.get("state", ""),
            "description": listing.get("description", ""),
            "url":         url,
            "source":      listing.get("source", ""),
        })

    results.sort(key=lambda x: (-x["score"], -x["cap_rate"]))
    return results, prefilter_rejects


# ── Output formatting ─────────────────────────────────────────────────────────

def _fmt_residential(d: dict) -> str:
    tag     = "GREEN" if d["flag"] == "GREEN" else "FLAG"
    reasons = "\n".join(f"  - {r}" for r in d["reasons"])
    return (
        f"[{tag} {d['score']}/10] {d['address']}, {d['city']}, {d['state']}\n"
        f"  Price: ${d['price']:,.0f}  |  Rent: ${d['rent']:,.0f}/mo  |  "
        f"Cash flow: ${d['cash_flow']:,.0f}/mo  |  CoC: {d['coc_pct']}%  |  "
        f"Down: ${d['down']:,.0f}\n"
        f"  {d['url']}\n"
        f"{reasons}"
    )


def _fmt_commercial(d: dict) -> str:
    tag     = "GREEN" if d["flag"] == "GREEN" else "FLAG"
    reasons = "\n".join(f"  - {r}" for r in d["reasons"])
    sqft    = f"{d['sqft']:,.0f} sqft  |  " if d["sqft"] else ""
    return (
        f"[{tag} {d['score']}/10] {d['description'][:90]}\n"
        f"  Market: {d['city']}, {d['state']}  |  Price: ${d['price']:,.0f}  |  "
        f"{sqft}Cap: {d['cap_rate']}%  |  DSCR: {d['dscr']}  |  NOI: ${d['noi']:,.0f}/yr\n"
        f"  {d['url']}\n"
        f"{reasons}"
    )


def _brief_line(category: str, metric: str, body: str) -> str:
    safe = body.replace("\r\n", "\n").replace("\n", "\\n")
    return f"UPDATE_BOARD|{category}|{metric}|{safe}\n"


def write_outputs(
    res_deals: list[dict],
    com_deals: list[dict],
    meta: dict,
) -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()

    # digest.json
    (_OUT_DIR / "digest.json").write_text(
        json.dumps({
            "generated_at":    ts,
            "env":             _ENV,
            "residential":     res_deals,
            "commercial":      com_deals,
            "residential_ct":  len(res_deals),
            "commercial_ct":   len(com_deals),
            **meta,
        }, indent=2),
        encoding="utf-8",
    )

    # residential_brief.txt
    if res_deals:
        body = f"{ts}\n\nRESIDENTIAL DEALS — {len(res_deals)} qualifying\n\n"
        body += "\n\n".join(_fmt_residential(d) for d in res_deals)
    else:
        body = f"{ts}\n\nNo qualifying residential deals found."
    (_OUT_DIR / "residential_brief.txt").write_text(
        _brief_line(CATEGORY_RES, METRIC_RES, body), encoding="utf-8")

    # commercial_brief.txt
    if com_deals:
        body = f"{ts}\n\nCOMMERCIAL DEALS — {len(com_deals)} qualifying\n\n"
        body += "\n\n".join(_fmt_commercial(d) for d in com_deals)
    else:
        body = f"{ts}\n\nNo qualifying commercial deals found."
    (_OUT_DIR / "commercial_brief.txt").write_text(
        _brief_line(CATEGORY_COM, METRIC_COM, body), encoding="utf-8")

    _write_status(
        "success" if not meta.get("warnings") else "success_with_warnings",
        residential_qualifying=len(res_deals),
        commercial_qualifying=len(com_deals),
        **meta,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Real estate deal scanner")
    parser.add_argument(
        "--job",
        choices=["residential", "commercial", "both"],
        default="both",
        help="Which job to run (default: both)",
    )
    args = parser.parse_args()

    print(f"[real_estate] env={_ENV}  job={args.job}  out={_OUT_DIR}")
    sources  = _load_sources()
    warnings: list[str] = []
    meta: dict = {
        "job":          args.job,
        "zillow_used":  False,
        "res_fetched":  0,
        "com_fetched":  0,
        "res_prefilter_rejects": 0,
        "com_prefilter_rejects": 0,
    }

    res_deals: list[dict] = []
    com_deals: list[dict] = []

    # ── Residential ───────────────────────────────────────────────────────
    if args.job in ("residential", "both"):
        res_listings: list[dict] = []

        if ZILLOW_KEY:
            print("[real_estate] Fetching residential via Zillow RapidAPI...")
            zil = fetch_zillow_residential(
                sources["residential"]["zillow_locations"],
                sources["residential"].get("zillow_params", {}),
            )
            res_listings.extend(zil)
            meta["zillow_used"] = True
            print(f"[real_estate] Zillow: {len(zil)} listings")
        else:
            warnings.append("ZILLOW_RAPIDAPI_KEY not set — using Craigslist only")
            print("[real_estate] No ZILLOW_RAPIDAPI_KEY — Craigslist fallback")

        cl_res = fetch_craigslist_residential(sources["residential"]["craigslist_markets"])
        res_listings.extend(cl_res)
        print(f"[real_estate] Craigslist residential: {len(cl_res)} listings")
        meta["res_fetched"] = len(res_listings)

        print(f"[real_estate] Scoring {len(res_listings)} residential listings...")
        res_deals, res_rejects = process_residential(res_listings)
        meta["res_prefilter_rejects"] = res_rejects
        print(f"[real_estate] Residential: {res_rejects} pre-filter rejects, "
              f"{len(res_deals)} qualifying (score >= 7)")

    # ── Commercial ────────────────────────────────────────────────────────
    if args.job in ("commercial", "both"):
        print("[real_estate] Fetching commercial via Craigslist CRE...")
        com_listings = fetch_craigslist_commercial(sources["commercial"]["craigslist_markets"])
        print(f"[real_estate] Craigslist commercial: {len(com_listings)} listings")
        meta["com_fetched"] = len(com_listings)

        print(f"[real_estate] Scoring {len(com_listings)} commercial listings...")
        com_deals, com_rejects = process_commercial(com_listings)
        meta["com_prefilter_rejects"] = com_rejects
        print(f"[real_estate] Commercial: {com_rejects} pre-filter rejects, "
              f"{len(com_deals)} qualifying (score >= 7)")

    if warnings:
        meta["warnings"] = warnings

    write_outputs(res_deals, com_deals, meta)
    print(
        f"[real_estate] OK — residential={len(res_deals)} commercial={len(com_deals)} "
        f"warnings={len(warnings)}"
    )


if __name__ == "__main__":
    main()
