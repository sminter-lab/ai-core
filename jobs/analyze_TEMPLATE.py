"""MAC STUDIO — {PROJECT} ANALYZER (template).

Copy this file to jobs/analyze_{project}.py and fill in the scoring logic.
See docs/framework_architecture.md for the framework contract.

The analyzer's ONLY job: read raw data the collector already stored, score it
against the project criteria doc, select the top N, and write decisions +
digest. Never fetches from the internet (localhost Ollama only).

Required setup:
  1. criteria/{project}_criteria.md — human-readable decision criteria
  2. Cron on Mac Studio (after the collector's slot):
       0 7 * * 1-5  cd /Users/samuelminter/ai-core && .venv/bin/python -m jobs.analyze_{project}
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

PROJECT = "TEMPLATE"  # ← rename

_BASE     = Path(__file__).resolve().parents[1]
_CONFIG   = json.loads((_BASE / "config" / f"{PROJECT}.json").read_text(encoding="utf-8"))
_ANALYZE  = _CONFIG["analyzer"]
_ROOT     = raw_root() / PROJECT
_RAW_DIR  = _ROOT / "raw"
_DEC_DIR  = _ROOT / "decisions"

OLLAMA_URL   = os.environ.get("OLLAMA_URL", _ANALYZE.get("ollama_url", "http://127.0.0.1:11434"))
OLLAMA_MODEL = _ANALYZE.get("ollama_model", "llama3.1:8b")
LEADS_PER_DAY     = int(_ANALYZE.get("leads_per_day", 3))
MIN_SURFACE_SCORE = int(_ANALYZE.get("min_surface_score", 7))


def ollama_score(prompt: str, timeout: int = 120) -> tuple[int, list[str]]:
    """Standard Score: X/10 + bullet-reasons extraction."""
    r = requests.post(f"{OLLAMA_URL}/api/generate", json={
        "model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.1, "num_predict": 220},
    }, timeout=timeout)
    r.raise_for_status()
    text  = (r.json().get("response") or "").strip()
    m     = re.search(r'[Ss]core[:\s]+(\d+)\s*/\s*10', text)
    score = int(m.group(1)) if m else 0
    reasons = [x.strip() for x in re.findall(r'-\s*(.+)', text) if x.strip()][:3]
    return score, reasons


def verdict(score: int) -> str:
    if score >= 8:
        return "GO"
    if score >= MIN_SURFACE_SCORE:
        return "REVIEW"
    return "NO-GO"


# ── Analysis logic (project-specific — fill in) ──────────────────────────────

def analyze(raw: dict) -> list[dict]:
    """Pre-filter raw items, score survivors, return scored dicts.

    Each returned dict must include: score (int), reasons (list[str]),
    and whatever fields the digest needs.
    """
    raise NotImplementedError("fill in project analysis logic")


def format_lead(d: dict) -> str:
    """One digest block per surfaced lead."""
    raise NotImplementedError("fill in digest formatting")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()

    raw_file = _RAW_DIR / f"{args.date}.json"
    if not raw_file.exists():
        print(f"[{PROJECT}] No raw collection for {args.date} — did the collector run?")
        return

    raw        = json.loads(raw_file.read_text(encoding="utf-8"))
    all_scored = sorted(analyze(raw), key=lambda x: -x["score"])
    for d in all_scored:
        d["verdict"] = verdict(d["score"])
    surfaced = [d for d in all_scored if d["score"] >= MIN_SURFACE_SCORE][:LEADS_PER_DAY]

    _DEC_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    (_DEC_DIR / f"{args.date}.json").write_text(json.dumps({
        "analyzed_at": ts,
        "date":        args.date,
        "leads":       surfaced,
        "all_scored":  all_scored,
    }, indent=2), encoding="utf-8")

    body = (f"{ts}\n\n{PROJECT.upper()} — {len(surfaced)} lead(s) for {args.date}\n\n"
            + "\n\n".join(format_lead(d) for d in surfaced)) if surfaced else \
           f"{ts}\n\n{PROJECT.upper()} — no qualifying leads for {args.date}."
    (_ROOT / "digest.txt").write_text(body, encoding="utf-8")

    print(f"[{PROJECT}] OK — scored={len(all_scored)} surfaced={len(surfaced)}")


if __name__ == "__main__":
    main()
