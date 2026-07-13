from __future__ import annotations

import os
import httpx
from dotenv import load_dotenv


# ===============================
# System framing
# ===============================

SYSTEM_PROMPT = """You are an options income operator assistant.
Your job is to help the user maximize *risk-adjusted weekly income* and improve execution skill.

Rules:
- Use ONLY the provided facts and user context.
- Do NOT hallucinate prices, option premiums, or positions.
- Do NOT place trades or give instructions to auto-trade.
- Be capital-realistic (1 options contract = 100 shares).
- Prefer repeatable processes over hype.
- When uncertain, state constraints clearly.
- Output Markdown with clear headings and bullets.
"""


# ===============================
# User / operator intent (truth)
# ===============================

OPERATOR_CONTEXT = """USER CONTEXT (authoritative intent):
- Account goal: generate consistent weekly income and improve options skill.
- Preferred / trusted process: AAPL covered calls and wheel strategy.
- AAPL shares were recently called away via covered calls.
- Current AAPL shares < 100, so covered calls are not currently possible.
- The AI should actively help re-establish an income engine.
- The AI should push toward *better decisions*, not passive commentary.
"""


# ===============================
# Candidate universe (analysis only)
# ===============================

CANDIDATE_UNIVERSE = [
    "AAPL",
    "NVDA",
    "AMD",
    "MSFT",
    "SPY",
    "QQQ",
]


# ===============================
# Ollama client
# ===============================

def ollama_generate(prompt: str) -> str:
    load_dotenv()
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
    model = os.environ.get("OLLAMA_MODEL", "").strip()

    if not model:
        raise RuntimeError("Missing OLLAMA_MODEL in .env")

    payload = {
        "model": model,
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "stream": False,
        "options": {
            "temperature": 0.25,
            "top_p": 0.9,
            "num_ctx": 8192,
        },
    }

    with httpx.Client(timeout=240) as client:
        r = client.post(f"{host}/api/generate", json=payload)
        r.raise_for_status()
        data = r.json()

    return (data.get("response") or "").strip()


# ===============================
# Prompt builder (THE IMPORTANT PART)
# ===============================

def build_prompt(analysis: dict) -> str:
    b = analysis.get("balances", {}) or {}
    positions = analysis.get("positions", []) or []
    quotes = analysis.get("quotes", {}) or {}

    # ---- derived facts ----
    cash = b.get("cash_balance")
    liquidation = b.get("liquidation_value")

    try:
        cash_f = float(cash) if cash is not None else None
        liq_f = float(liquidation) if liquidation is not None else None
        cash_pct = (cash_f / liq_f * 100.0) if cash_f and liq_f else None
    except Exception:
        cash_f = liq_f = cash_pct = None

    owned_symbols = sorted({p.get("symbol") for p in positions if p.get("symbol")})

    # contract affordability check
    affordability = {}
    if cash_f:
        for sym, q in quotes.items():
            try:
                last = float(q.get("last")) if q.get("last") is not None else None
            except Exception:
                last = None

            if last:
                affordability[sym] = {
                    "est_cost_100_shares": round(last * 100, 2),
                    "can_sell_1_csp": cash_f >= (last * 100),
                }

    # ---- prompt assembly ----
    lines = []
    lines.append(OPERATOR_CONTEXT.strip())
    lines.append("\nFACTS (Schwab snapshot – authoritative):\n")

    lines.append("Balances:")
    for k in [
        "liquidation_value",
        "cash_balance",
        "available_funds",
        "buying_power",
        "maintenance_requirement",
        "margin_balance",
    ]:
        lines.append(f"- {k}: {b.get(k)}")

    lines.append(f"- cash_pct_of_liquidation: {cash_pct}")
    lines.append("")

    lines.append("Current positions:")
    if positions:
        for p in positions:
            lines.append(
                f"- {p.get('symbol')}: qty={p.get('quantity')} "
                f"mv={p.get('market_value')} weight_pct={p.get('weight_pct')}"
            )
    else:
        lines.append("- (none)")

    lines.append("")
    lines.append("Latest quotes:")
    for sym, q in quotes.items():
        lines.append(
            f"- {sym}: last={q.get('last')} bid={q.get('bid')} ask={q.get('ask')} "
            f"mark={q.get('mark')} vol={q.get('volume')}"
        )

    lines.append("")
    lines.append("Affordability check (rough, last * 100 vs cashBalance):")
    lines.append(str(affordability))

    lines.append("")
    lines.append("Candidate universe for income engine consideration:")
    lines.append(", ".join(CANDIDATE_UNIVERSE))
    lines.append(f"Currently owned symbols: {owned_symbols}")

    # ---- enforced task spec ----
    lines.append(
        """
TASK:
Produce a decisive, capital-realistic income brief with the following sections.

## 1) What changed & why it matters
- Explain the cash-heavy posture and why income is currently stalled.
- Explicitly state that AAPL < 100 shares blocks covered calls.

## 2) Primary Income Engine Plan (AAPL-first)
- Treat AAPL as the default engine unless there is a strong reason not to.
- Explain HOW to re-establish AAPL income:
  - Buy-to-100 strategy vs cash-secured puts to get assigned.
- Reference affordability and current cash.
- State what must be true (price stability, no earnings risk, etc.).

## 3) Secondary Engine Options (only if capital-feasible)
- Evaluate NVDA ONLY if the affordability check allows at least 1 CSP.
- If NVDA CSP is NOT feasible, say so clearly.
- Propose ONE capital-efficient alternative (AMD / MSFT / index proxy) and explain why.

## 4) Weekly Operator Playbook (Mon–Fri)
- Entry windows
- Profit-taking rules (e.g., 50–80% premium capture)
- Roll / assignment risk rules
- What would invalidate the plan this week

## 5) Next Action (Today)
- One clear, concrete next step the user should take today.

## 6) One question
- Ask exactly ONE question that would materially improve next week’s plan.
"""
    )

    return "\n".join(lines)
