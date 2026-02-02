from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from workers.schwab.llm import ollama_generate, build_prompt


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def jdump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def pct(part: Optional[float], whole: Optional[float]) -> Optional[float]:
    if part is None or whole is None or whole == 0:
        return None
    return (part / whole) * 100.0


def fmt_money(v: Optional[float]) -> str:
    return "n/a" if v is None else f"${v:,.2f}"


def fmt_num(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:,.2f}"


def main() -> None:
    load_dotenv()

    db_path = Path(os.environ.get("SCHWAB_DB_PATH", "data/schwab/schwab.sqlite3")).expanduser().resolve()
    out_md = Path(os.environ.get("SCHWAB_ANALYSIS_PATH", "data/schwab/analysis.md")).expanduser().resolve()
    out_json = Path(os.environ.get("SCHWAB_ANALYSIS_JSON_PATH", "data/schwab/analysis.json")).expanduser().resolve()
    llm_out = Path(os.environ.get("SCHWAB_LLM_OUTPUT_PATH", "data/schwab/llm_brief.md")).expanduser().resolve()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Latest successful run
    run = conn.execute(
        "SELECT * FROM runs WHERE status='OK' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not run:
        raise SystemExit("No successful run found yet.")

    run_id = int(run["id"])
    ts = utc_now_iso()

    # Pull balances (current)
    bal_row = conn.execute(
        "SELECT raw_json FROM balances WHERE run_id=? AND kind='currentBalances' LIMIT 1",
        (run_id,),
    ).fetchone()

    current_balances: Dict[str, Any] = {}
    if bal_row:
        try:
            current_balances = json.loads(bal_row["raw_json"])
        except Exception:
            current_balances = {}

    # Key metrics (common fields in Schwab currentBalances)
    liquidation_value = safe_float(current_balances.get("liquidationValue"))
    equity = safe_float(current_balances.get("equity"))
    cash_balance = safe_float(current_balances.get("cashBalance"))
    buying_power = safe_float(current_balances.get("buyingPower"))
    available_funds = safe_float(current_balances.get("availableFunds"))
    maint_req = safe_float(current_balances.get("maintenanceRequirement"))
    sma = safe_float(current_balances.get("sma"))
    daytrading_bp = safe_float(current_balances.get("dayTradingBuyingPower"))
    long_mkt_value = safe_float(current_balances.get("longMarketValue"))
    short_mkt_value = safe_float(current_balances.get("shortMarketValue"))
    margin_balance = safe_float(current_balances.get("marginBalance"))

    # Positions (latest run)
    pos_rows = conn.execute(
        """
        SELECT symbol, quantity, market_value
        FROM positions
        WHERE run_id=?
        ORDER BY market_value DESC
        """,
        (run_id,),
    ).fetchall()

    positions: List[Dict[str, Any]] = []
    for r in pos_rows:
        mv = safe_float(r["market_value"])
        positions.append(
            {
                "symbol": r["symbol"],
                "quantity": safe_float(r["quantity"]),
                "market_value": mv,
                "weight_pct": pct(mv, liquidation_value),
            }
        )

    # Quotes (latest run)
    q_rows = conn.execute(
        """
        SELECT symbol, last, bid, ask, mark, volume, quote_time
        FROM quotes
        WHERE run_id=?
        ORDER BY symbol
        """,
        (run_id,),
    ).fetchall()

    quotes: Dict[str, Dict[str, Any]] = {}
    for q in q_rows:
        quotes[q["symbol"]] = {
            "last": safe_float(q["last"]),
            "bid": safe_float(q["bid"]),
            "ask": safe_float(q["ask"]),
            "mark": safe_float(q["mark"]),
            "volume": safe_float(q["volume"]),
            "quote_time": q["quote_time"],
        }

    analysis = {
        "generated_at": ts,
        "run_id": run_id,
        "balances": {
            "liquidation_value": liquidation_value,
            "equity": equity,
            "cash_balance": cash_balance,
            "available_funds": available_funds,
            "buying_power": buying_power,
            "maintenance_requirement": maint_req,
            "sma": sma,
            "day_trading_buying_power": daytrading_bp,
            "long_market_value": long_mkt_value,
            "short_market_value": short_mkt_value,
            "margin_balance": margin_balance,
        },
        "positions": positions,
        "quotes": quotes,
    }

    # ---- Write JSON ----
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(jdump(analysis), encoding="utf-8")

    # ---- Write Markdown ----
    lines: List[str] = []
    lines.append(f"# Schwab Brief ({ts})")
    lines.append("")
    lines.append(f"- Latest run_id: **{run_id}**")
    lines.append("")

    lines.append("## Key balances")
    lines.append("")
    lines.append(f"- Liquidation value: **{fmt_money(liquidation_value)}**")
    lines.append(f"- Equity: **{fmt_money(equity)}**")
    lines.append(f"- Cash balance: **{fmt_money(cash_balance)}**")
    lines.append(f"- Available funds: **{fmt_money(available_funds)}**")
    lines.append(f"- Buying power: **{fmt_money(buying_power)}**")
    lines.append(f"- Maintenance requirement: **{fmt_money(maint_req)}**")
    lines.append(f"- SMA: **{fmt_money(sma)}**")
    lines.append(f"- Day trading buying power: **{fmt_money(daytrading_bp)}**")
    lines.append(f"- Long market value: **{fmt_money(long_mkt_value)}**")
    lines.append(f"- Short market value: **{fmt_money(short_mkt_value)}**")
    lines.append(f"- Margin balance: **{fmt_money(margin_balance)}**")
    lines.append("")

    lines.append("## Positions (with portfolio weight)")
    lines.append("")
    if not positions:
        lines.append("- (none)")
    else:
        for p in positions:
            w = p["weight_pct"]
            w_s = "n/a" if w is None else f"{w:.2f}%"
            lines.append(
                f"- {p['symbol']}: qty={fmt_num(p['quantity'])} | mv={fmt_money(p['market_value'])} | weight={w_s}"
            )
    lines.append("")

    lines.append("## Quotes (latest run)")
    lines.append("")
    if not quotes:
        lines.append("- (none)")
    else:
        for sym, q in quotes.items():
            lines.append(
                f"- {sym}: last={fmt_num(q['last'])} bid={fmt_num(q['bid'])} ask={fmt_num(q['ask'])} "
                f"mark={fmt_num(q['mark'])} vol={fmt_num(q['volume'])}"
            )
    lines.append("")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"🧠 Analysis written: {out_md}")
    print(f"📦 Analysis JSON written: {out_json}")

    # ---- LLM Brief (Ollama) ----
    # Uses analysis dict (truth source) to build prompt and generate Markdown output.
    try:
        prompt = build_prompt(analysis)
        llm_md = ollama_generate(prompt)

        llm_out.parent.mkdir(parents=True, exist_ok=True)
        llm_out.write_text(llm_md + "\n", encoding="utf-8")
        print(f"🤖 LLM brief written: {llm_out}")
    except Exception as e:
        # Don't fail the whole analyzer if the LLM is down; just log.
        print(f"⚠️ LLM brief failed: {e}")

    conn.close()


if __name__ == "__main__":
    main()
