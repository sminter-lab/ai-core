"""MAC STUDIO — real estate ANALYZER.

Role (framework v1.0): read raw data, analyze, decide, report. Never collects.
Zero network calls except localhost Ollama.

Pipeline:
  1. Load today's raw collection from raw_root()/real_estate/raw/YYYY-MM-DD.json
  2. Financial pre-filter (criteria/re_investment_criteria.md, math in jobs/re_criteria.py)
  3. Ollama scores survivors 1-10
  4. Rank, take top 3 (config: leads_per_day)
  5. GO / REVIEW / NO-GO verdicts + reasons + financials
  6. Write decisions + digest

Output (under raw_root()/real_estate/):
  decisions/YYYY-MM-DD.json  — top leads with verdicts
  digest.txt                 — human-readable morning digest
  status.json                — last-run metadata

Usage:
  python -m jobs.analyze_realestate [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from tools.shared_store import raw_root
from reporting.reporter import report_job
from jobs.re_criteria import (
    commercial_metrics,
    commercial_passes,
    in_growth_market,
    in_landlord_friendly_state,
    residential_metrics,
    residential_passes,
)

_BASE     = Path(__file__).resolve().parents[1]
_CONFIG   = json.loads((_BASE / "config" / "realestate.json").read_text(encoding="utf-8"))
_ANALYZE  = _CONFIG["analyzer"]

_ENV      = os.environ.get("AI_CORE_ENV", "live").strip().lower()
_RE_ROOT  = raw_root() / "real_estate" / ("test" if _ENV == "test" else "")
_RAW_DIR  = _RE_ROOT / "raw"
_DEC_DIR  = _RE_ROOT / "decisions"

OLLAMA_URL   = os.environ.get("OLLAMA_URL", _ANALYZE["ollama_url"])
OLLAMA_MODEL = os.environ.get("RE_OLLAMA_MODEL", _ANALYZE["ollama_model"])
TIMEOUT      = int(_ANALYZE.get("timeout_sec", 120))
LEADS_PER_DAY     = int(_ANALYZE.get("leads_per_day", 3))
MIN_SURFACE_SCORE = int(_ANALYZE.get("min_surface_score", 7))

_CRE_RENT_PSF = 12.0  # $/sqft/yr NNN estimate when listing has no income data


def _sanitize(s: str) -> str:
    if not s:
        return ""
    s = s.encode("ascii", errors="ignore").decode("ascii")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s).strip()


# ── Ollama scoring ────────────────────────────────────────────────────────────

def _ollama_score(prompt: str) -> tuple[int, list[str]]:
    r = requests.post(f"{OLLAMA_URL}/api/generate", json={
        "model":   OLLAMA_MODEL,
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": 0.1, "num_predict": 220},
    }, timeout=TIMEOUT)
    r.raise_for_status()
    text  = _sanitize(r.json().get("response") or "")
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


# ── Analysis pipeline ─────────────────────────────────────────────────────────

def analyze_residential(listings: list[dict]) -> tuple[list[dict], int]:
    results: list[dict] = []
    prefilter_rejects = 0
    for listing in listings:
        price = listing.get("price")
        if not price or price < 10_000:
            continue
        rent    = listing.get("rent") or price * 0.0075
        metrics = residential_metrics(price, rent)
        if not residential_passes(metrics):
            prefilter_rejects += 1
            continue
        try:
            score, reasons = _score_residential(listing, metrics, rent)
        except Exception as exc:
            print(f"[analyze_re] Ollama residential error: {type(exc).__name__}: {exc}")
            continue
        results.append({
            "type":      "residential",
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
            "url":       listing.get("url", ""),
            "source":    listing.get("source", ""),
        })
    return results, prefilter_rejects


def analyze_commercial(listings: list[dict]) -> tuple[list[dict], int]:
    results: list[dict] = []
    prefilter_rejects = 0
    for listing in listings:
        price = listing.get("price")
        if not price or price < 100_000:
            continue
        cap_hint = listing.get("cap_rate_hint")
        sqft     = listing.get("sqft") or 0
        if cap_hint:
            gross_income = (price * cap_hint) / (1 - 0.10)
        elif sqft:
            gross_income = sqft * _CRE_RENT_PSF
        else:
            gross_income = (price * 0.08) / (1 - 0.10)
        metrics = commercial_metrics(price, gross_income)
        if not commercial_passes(metrics):
            prefilter_rejects += 1
            continue
        try:
            score, reasons = _score_commercial(listing, metrics)
        except Exception as exc:
            print(f"[analyze_re] Ollama commercial error: {type(exc).__name__}: {exc}")
            continue
        results.append({
            "type":        "commercial",
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
            "url":         listing.get("url", ""),
            "source":      listing.get("source", ""),
        })
    return results, prefilter_rejects


def _verdict(score: int) -> str:
    if score >= 8:
        return "GO"
    if score >= MIN_SURFACE_SCORE:
        return "REVIEW"
    return "NO-GO"


def _fmt_lead(d: dict) -> str:
    reasons = "\n".join(f"  - {r}" for r in d["reasons"])
    if d["type"] == "residential":
        head = (f"[{d['verdict']} {d['score']}/10] {d['address']}, {d['city']}, {d['state']}\n"
                f"  Price: ${d['price']:,.0f}  |  Rent: ${d['rent']:,.0f}/mo  |  "
                f"Cash flow: ${d['cash_flow']:,.0f}/mo  |  CoC: {d['coc_pct']}%  |  "
                f"Down: ${d['down']:,.0f}")
    else:
        sqft = f"{d['sqft']:,.0f} sqft  |  " if d.get("sqft") else ""
        head = (f"[{d['verdict']} {d['score']}/10] {d['description'][:90]}\n"
                f"  Market: {d['city']}, {d['state']}  |  Price: ${d['price']:,.0f}  |  "
                f"{sqft}Cap: {d['cap_rate']}%  |  DSCR: {d['dscr']}  |  NOI: ${d['noi']:,.0f}/yr")
    return f"{head}\n  {d['url']}\n{reasons}"


# ── Entry point ───────────────────────────────────────────────────────────────

@report_job("analyze_realestate", job_type="Business")
def main() -> None:
    parser = argparse.ArgumentParser(description="Real estate analyzer (Mac Studio)")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                        help="raw collection date to analyze (default: today)")
    args = parser.parse_args()

    raw_file = _RAW_DIR / f"{args.date}.json"
    print(f"[analyze_re] env={_ENV}  raw={raw_file}")
    if not raw_file.exists():
        print(f"[analyze_re] No raw collection for {args.date} — did the collector run?")
        _DEC_DIR.mkdir(parents=True, exist_ok=True)
        (_RE_ROOT / "status.json").write_text(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status":    "no_raw_data",
            "date":      args.date,
        }, indent=2), encoding="utf-8")
        return

    raw = json.loads(raw_file.read_text(encoding="utf-8"))

    res_scored, res_rejects = analyze_residential(raw.get("residential", []))
    com_scored, com_rejects = analyze_commercial(raw.get("commercial", []))

    # Rank everything together, verdicts, then take top N for the digest
    all_scored = sorted(res_scored + com_scored, key=lambda x: -x["score"])
    for d in all_scored:
        d["verdict"] = _verdict(d["score"])
    surfaced = [d for d in all_scored if d["score"] >= MIN_SURFACE_SCORE][:LEADS_PER_DAY]

    _DEC_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    (_DEC_DIR / f"{args.date}.json").write_text(json.dumps({
        "analyzed_at": ts,
        "date":        args.date,
        "env":         _ENV,
        "leads":       surfaced,
        "all_scored":  all_scored,          # full log, incl. NO-GO
        "counts": {
            "res_raw":     len(raw.get("residential", [])),
            "com_raw":     len(raw.get("commercial", [])),
            "res_prefilter_rejects": res_rejects,
            "com_prefilter_rejects": com_rejects,
            "scored":      len(all_scored),
            "surfaced":    len(surfaced),
        },
    }, indent=2), encoding="utf-8")

    # Morning digest
    if surfaced:
        body = f"{ts}\n\nREAL ESTATE — {len(surfaced)} lead(s) for {args.date}\n\n"
        body += "\n\n".join(_fmt_lead(d) for d in surfaced)
    else:
        body = f"{ts}\n\nREAL ESTATE — no qualifying leads for {args.date}."
    (_RE_ROOT / "digest.txt").write_text(body, encoding="utf-8")

    (_RE_ROOT / "status.json").write_text(json.dumps({
        "timestamp": ts,
        "status":    "success",
        "date":      args.date,
        "surfaced":  len(surfaced),
        "scored":    len(all_scored),
    }, indent=2), encoding="utf-8")

    print(f"[analyze_re] OK — scored={len(all_scored)} surfaced={len(surfaced)} "
          f"(res_rejects={res_rejects} com_rejects={com_rejects})")


if __name__ == "__main__":
    main()
