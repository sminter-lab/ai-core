from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from workers.schwab.llm import ollama_generate


def jdump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def latest_ok_id(conn: sqlite3.Connection, table: str) -> Optional[int]:
    if table == "runs":
        row = conn.execute("SELECT id FROM runs WHERE status='OK' ORDER BY id DESC LIMIT 1").fetchone()
        return int(row[0]) if row else None
    if table == "option_runs":
        row = conn.execute("SELECT id FROM option_runs WHERE status='OK' ORDER BY id DESC LIMIT 1").fetchone()
        return int(row[0]) if row else None
    return None


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    load_dotenv()

    db_path = Path(os.environ.get("SCHWAB_DB_PATH", "data/schwab/schwab.sqlite3")).expanduser().resolve()

    # Inputs created by your existing analyzers
    analysis_json_path = Path(os.environ.get("SCHWAB_ANALYSIS_JSON_PATH", "data/schwab/analysis.json")).expanduser().resolve()

    # Options analyzer outputs per option_run_id (we’ll locate latest)
    options_dir = Path(os.environ.get("SCHWAB_OPTIONS_OUT_DIR", "data/schwab/options")).expanduser().resolve()

    out_path = Path(os.environ.get("SCHWAB_LLM_OPTIONS_BRIEF_PATH", "data/schwab/llm_options_brief.md")).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    portfolio_run_id = latest_ok_id(conn, "runs")
    option_run_id = latest_ok_id(conn, "option_runs")
    conn.close()

    if portfolio_run_id is None:
        raise SystemExit("No portfolio run found. Run: python -m workers.schwab.collector && python -m workers.schwab.analyze")
    if option_run_id is None:
        raise SystemExit("No option run found. Run: python -m workers.schwab.options_collector && python -m workers.schwab.options_analyze")

    # Load analysis.json (balances, positions, quotes)
    analysis = load_json(analysis_json_path)

    # Load options analysis (latest option_run_id file)
    options_json_path = options_dir / f"options_analysis_{option_run_id}.json"
    options_analysis = load_json(options_json_path)
    if not options_analysis:
        raise SystemExit(f"Missing options analysis: {options_json_path}")

    # Pull key facts for feasibility
    balances = analysis.get("balances", {}) or {}
    cash = balances.get("cash_balance")
    lv = balances.get("liquidation_value")

    # Prompt: facts + ranked candidates (no hallucinations)
    prompt = f"""
YOU ARE AN OPTIONS INCOME OPERATOR.
Goal: build a “money machine” toward $4,000/month with a Core + Accelerator approach.
No auto-trades. Provide a plan + sizing + checks.

USER CONTEXT:
- Comfortable with covered calls historically, but open to best underlyings analytically.
- Current AAPL shares are < 100 (so CC not currently possible).
- Willing to use accelerators (NVDA/AMD) if capital-feasible and rules are tight.

FACTS (from local JSON files):
- portfolio_run_id: {portfolio_run_id}
- option_run_id: {option_run_id}
- cash_balance: {cash}
- liquidation_value: {lv}

ACCOUNT SNAPSHOT JSON (truth):
{jdump(analysis)}

OPTIONS RANKINGS JSON (truth):
{jdump(options_analysis)}

TASK:
Return a Markdown brief with these sections:

## 1) State of the Machine
- Cash %, current inventory, and what’s blocking income right now.

## 2) Best Plays This Week (ranked, capital-feasible)
- Choose 1 CORE play and 1 ACCELERATOR play from AAPL/NVDA/AMD.
- Use the options rankings (efficiency + liquidity + delta bucket).
- CAPITAL CHECK:
  - CSP collateral ≈ strike*100 must be <= cash balance for 1 contract.
  - If not feasible, say so and propose alternative sizing/underlying.
- Prefer “balanced” delta bucket first; only use “aggressive” if you explain why.

## 3) Inventory Plan (path to covered calls)
- How to rebuild toward 100-share lots (buy-to-100 vs wheel into assignment).
- Explain why this is faster/safer.

## 4) Risk Rules (non-negotiable)
- profit-take rule (50–80% premium capture)
- roll/exit rules when tested
- earnings / major event rule (do not hold through earnings unless explicitly chosen)

## 5) Next Action Today
- One concrete thing to do today (check, setup alerts, identify strike/expiry, etc.)

Ask exactly ONE question at the end that would materially improve next week’s plan.
"""

    md = ollama_generate(prompt)
    out_path.write_text(md + "\n", encoding="utf-8")
    print(f"🤖 Options LLM brief written: {out_path}")


if __name__ == "__main__":
    main()
