from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass
class ContractRow:
    underlying: str
    option_type: str   # CALL / PUT
    expiration: str
    strike: float
    bid: Optional[float]
    ask: Optional[float]
    mark: Optional[float]
    last: Optional[float]
    delta: Optional[float]
    iv: Optional[float]
    open_interest: Optional[int]
    volume: Optional[int]


def mark_price(bid: Optional[float], ask: Optional[float], mark: Optional[float], last: Optional[float]) -> Optional[float]:
    if mark is not None:
        return mark
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return last


def liquidity_score(oi: Optional[int], vol: Optional[int], bid: Optional[float], ask: Optional[float], px: Optional[float]) -> float:
    """
    Rough, monotonic score:
    - more OI/volume => better
    - tighter spread => better
    Returns 0..100
    """
    oi_v = float(oi or 0)
    vol_v = float(vol or 0)

    # Normalize OI/Vol into 0..1 using soft saturation
    oi_n = clamp(oi_v / 2000.0, 0.0, 1.0)       # 2000 OI ~ "good enough"
    vol_n = clamp(vol_v / 1000.0, 0.0, 1.0)     # 1000 vol ~ "good enough"

    # Spread penalty
    spread_n = 0.5  # default "meh"
    if bid is not None and ask is not None and px and px > 0:
        spr = max(ask - bid, 0.0)
        spr_pct = spr / px
        # <0.3% is excellent, >2% is poor
        spread_n = 1.0 - clamp((spr_pct - 0.003) / (0.02 - 0.003), 0.0, 1.0)

    score = (0.45 * oi_n + 0.35 * vol_n + 0.20 * spread_n) * 100.0
    return float(round(score, 2))


def delta_bucket(opt_type: str, delta: Optional[float]) -> str:
    if delta is None:
        return "unknown"
    d = abs(delta)
    if opt_type == "PUT":
        # CSP sellers often look 0.15-0.30
        if d <= 0.15:
            return "conservative"
        if d <= 0.30:
            return "balanced"
        return "aggressive"
    else:
        # CC sellers often look 0.20-0.35
        if d <= 0.20:
            return "conservative"
        if d <= 0.35:
            return "balanced"
        return "aggressive"


def premium_efficiency(premium_per_share: Optional[float], strike: float) -> Optional[float]:
    """
    Options premium is quoted per share.
    Contract premium = premium_per_share * 100.
    CSP collateral approx = strike * 100.
    Efficiency (%) ~= (premium_per_share*100) / (strike*100) * 100 = (premium_per_share / strike) * 100
    """
    if premium_per_share is None:
        return None
    if strike <= 0:
        return None
    return (premium_per_share / strike) * 100.0

def main() -> None:
    db_path = Path(os.environ.get("SCHWAB_DB_PATH", "data/schwab/schwab.sqlite3")).expanduser().resolve()
    out_dir = Path(os.environ.get("SCHWAB_OPTIONS_OUT_DIR", "data/schwab/options")).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Latest successful option run
    row = conn.execute(
        "SELECT id, created_at, symbols_json, status FROM option_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise SystemExit("No option_runs found. Run options_collector first.")

    option_run_id = int(row["id"])
    status = row["status"]
    if status != "OK":
        raise SystemExit(f"Latest option_run_id={option_run_id} status={status} (not OK)")

    # Pull contracts
    rows = conn.execute(
        """
        SELECT underlying, option_type, expiration, strike, bid, ask, mark, last, delta, iv, open_interest, volume
        FROM option_contracts
        WHERE option_run_id=?
        """,
        (option_run_id,),
    ).fetchall()

    contracts: List[ContractRow] = []
    for r in rows:
        contracts.append(
            ContractRow(
                underlying=r["underlying"],
                option_type=r["option_type"],
                expiration=r["expiration"],
                strike=float(r["strike"]) if r["strike"] is not None else 0.0,
                bid=safe_float(r["bid"]),
                ask=safe_float(r["ask"]),
                mark=safe_float(r["mark"]),
                last=safe_float(r["last"]),
                delta=safe_float(r["delta"]),
                iv=safe_float(r["iv"]),
                open_interest=r["open_interest"],
                volume=r["volume"],
            )
        )

    if not contracts:
        raise SystemExit(f"No option_contracts found for option_run_id={option_run_id}")

    # Build analytics
    scored: List[Dict[str, Any]] = []
    for c in contracts:
        px = mark_price(c.bid, c.ask, c.mark, c.last)
        eff = premium_efficiency(px, c.strike)
        liq = liquidity_score(c.open_interest, c.volume, c.bid, c.ask, px)
        bucket = delta_bucket(c.option_type, c.delta)

        scored.append(
            {
                "underlying": c.underlying,
                "option_type": c.option_type,
                "expiration": c.expiration,
                "strike": c.strike,
                "premium": px,
                "delta": c.delta,
                "iv": c.iv,
                "open_interest": c.open_interest,
                "volume": c.volume,
                "liquidity_score": liq,
                "delta_bucket": bucket,
                "efficiency_pct_of_collateral": eff,  # % per cycle (approx)
                "collateral_est": c.strike * 100.0,   # rough CSP collateral
            }
        )

    # For each underlying/type/expiration, pick top candidates by efficiency, with liquidity filter
    def pick_top(underlying: str, opt_type: str, n: int = 5) -> List[Dict[str, Any]]:
        subset = [x for x in scored if x["underlying"] == underlying and x["option_type"] == opt_type]
        # filter out illiquid junk
        subset = [x for x in subset if (x["premium"] is not None and x["premium"] > 0 and x["liquidity_score"] >= 30)]
        subset.sort(key=lambda x: (x["efficiency_pct_of_collateral"] or 0.0, x["liquidity_score"]), reverse=True)
        return subset[:n]

    underlyings = sorted({x["underlying"] for x in scored})
    expirations = sorted({x["expiration"] for x in scored})

    report: Dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "option_run_id": option_run_id,
        "underlyings": underlyings,
        "expirations": expirations,
        "notes": {
            "efficiency_pct_of_collateral": "premium / (strike*100), approximate % return per cycle for CSP-like collateral",
            "liquidity_score": "0-100 rough score using OI, volume, and bid/ask spread",
            "filters": "premium>0 and liquidity_score>=30",
        },
        "top_candidates": {},
    }

    for u in underlyings:
        report["top_candidates"][u] = {
            "puts_top": pick_top(u, "PUT", n=8),
            "calls_top": pick_top(u, "CALL", n=8),
        }

    # Write artifacts
    json_path = out_dir / f"options_analysis_{option_run_id}.json"
    md_path = out_dir / f"options_analysis_{option_run_id}.md"

    json_path.write_text(jdump(report), encoding="utf-8")

    # Markdown summary (human)
    lines: List[str] = []
    lines.append(f"# Options Analysis (option_run_id={option_run_id})")
    lines.append(f"- Generated: {report['generated_at']}")
    lines.append(f"- Underlyings: {', '.join(underlyings)}")
    lines.append(f"- Expirations: {', '.join([e for e in expirations if e])}")
    lines.append("")
    lines.append("## Top CSP candidates (by premium efficiency, liquidity-filtered)")
    lines.append("")
    for u in underlyings:
        lines.append(f"### {u} PUTs")
        tops = report["top_candidates"][u]["puts_top"]
        if not tops:
            lines.append("- (none passed filters)")
        else:
            for x in tops[:5]:
                lines.append(
                    f"- {x['expiration']} strike {x['strike']}: prem={x['premium']} "
                    f"eff={x['efficiency_pct_of_collateral']:.3f}% "
                    f"delta={x['delta']} bucket={x['delta_bucket']} liq={x['liquidity_score']}"
                )
        lines.append("")

    lines.append("## Top CC candidates (for when you hold 100 shares)")
    lines.append("")
    for u in underlyings:
        lines.append(f"### {u} CALLs")
        tops = report["top_candidates"][u]["calls_top"]
        if not tops:
            lines.append("- (none passed filters)")
        else:
            for x in tops[:5]:
                lines.append(
                    f"- {x['expiration']} strike {x['strike']}: prem={x['premium']} "
                    f"eff={x['efficiency_pct_of_collateral']:.3f}% "
                    f"delta={x['delta']} bucket={x['delta_bucket']} liq={x['liquidity_score']}"
                )
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"✅ Options analyze OK | option_run_id={option_run_id}")
    print(f"📦 Wrote: {json_path}")
    print(f"📝 Wrote: {md_path}")

    conn.close()


if __name__ == "__main__":
    main()
