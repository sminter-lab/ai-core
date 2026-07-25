from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv

# Uses your existing authenticated Schwab client factory:
# - expected: get_client() returns a schwab-py Client instance
from workers.schwab.client import get_client
from reporting.reporter import report_job


# =========================
# Helpers
# =========================

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


def safe_int(x) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


def parse_exp_key(exp_key: str) -> Optional[date]:
    """
    Schwab option chain maps often use keys like:
      "2026-02-06:5"  (date:daysToExpiration)
    We take the date portion.
    """
    try:
        d = exp_key.split(":")[0].strip()
        y, m, dd = d.split("-")
        return date(int(y), int(m), int(dd))
    except Exception:
        return None


def pick_nearest_expirations(exp_map: Dict[str, Any], n: int = 2) -> List[str]:
    """
    exp_map keys -> pick n earliest by parsed date.
    Returns original keys (so we can index into maps).
    """
    items: List[Tuple[date, str]] = []
    for k in exp_map.keys():
        d = parse_exp_key(k)
        if d:
            items.append((d, k))
    items.sort(key=lambda t: t[0])
    return [k for _, k in items[:n]]


def iter_contracts_from_map(
    symbol: str,
    opt_type: str,
    exp_map: Dict[str, Any],
    exp_keys: List[str],
) -> Iterable[Dict[str, Any]]:
    """
    callExpDateMap / putExpDateMap format:
      {
        "2026-02-06:5": {
          "260.0": [ {contract}, {contract} ],
          "265.0": [ ... ]
        },
        ...
      }
    We yield normalized rows with important fields only.
    """
    for exp_key in exp_keys:
        strikes = exp_map.get(exp_key) or {}
        exp_date = parse_exp_key(exp_key)
        for strike_str, contracts in strikes.items():
            strike = safe_float(strike_str)
            if not isinstance(contracts, list):
                continue
            for c in contracts:
                if not isinstance(c, dict):
                    continue

                # Field names below match what Schwab/TDA-style chains commonly return.
                # If a field is missing, it stays None.
                yield {
                    "underlying": symbol,
                    "option_type": opt_type,  # CALL or PUT
                    "expiration": exp_date.isoformat() if exp_date else None,
                    "strike": strike,
                    "contract_symbol": c.get("symbol") or c.get("optionSymbol"),
                    "description": c.get("description"),
                    "bid": safe_float(c.get("bid")),
                    "ask": safe_float(c.get("ask")),
                    "mark": safe_float(c.get("mark")),
                    "last": safe_float(c.get("last")),
                    "delta": safe_float(c.get("delta")),
                    "iv": safe_float(c.get("volatility") or c.get("impliedVolatility")),
                    "open_interest": safe_int(c.get("openInterest")),
                    "volume": safe_int(c.get("totalVolume") or c.get("volume")),
                    "in_the_money": c.get("inTheMoney"),
                }


# =========================
# SQLite schema
# =========================

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS option_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  portfolio_run_id INTEGER,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  symbols_json TEXT NOT NULL,
  from_date TEXT,
  to_date TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS option_chains_raw (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  option_run_id INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  FOREIGN KEY(option_run_id) REFERENCES option_runs(id)
);

CREATE TABLE IF NOT EXISTS option_contracts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  option_run_id INTEGER NOT NULL,
  underlying TEXT NOT NULL,
  option_type TEXT NOT NULL,     -- CALL / PUT
  expiration TEXT,              -- YYYY-MM-DD
  strike REAL,
  contract_symbol TEXT,
  description TEXT,
  bid REAL,
  ask REAL,
  mark REAL,
  last REAL,
  delta REAL,
  iv REAL,
  open_interest INTEGER,
  volume INTEGER,
  in_the_money INTEGER,         -- 0/1 when present
  FOREIGN KEY(option_run_id) REFERENCES option_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_option_contracts_lookup
  ON option_contracts(underlying, expiration, option_type, strike);

CREATE INDEX IF NOT EXISTS idx_option_contracts_run
  ON option_contracts(option_run_id);
"""


# =========================
# Config
# =========================

DEFAULT_SYMBOLS = ["AAPL", "NVDA", "AMD"]


def get_portfolio_run_id(conn: sqlite3.Connection) -> Optional[int]:
    """
    Your existing collector stores 'runs' with status='OK'.
    We link our option run to the latest portfolio run id, but we do NOT
    create a new 'runs' row (so analyze.py continues to work).
    """
    try:
        row = conn.execute(
            "SELECT id FROM runs WHERE status='OK' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None


def env_symbols() -> List[str]:
    """
    Optional override:
      SCHWAB_OPTION_SYMBOLS=AAPL,NVDA,AMD
    If unset, uses default list.
    """
    raw = (os.environ.get("SCHWAB_OPTION_SYMBOLS") or "").strip()
    if not raw:
        return DEFAULT_SYMBOLS
    syms = [s.strip().upper() for s in raw.replace(";", ",").split(",") if s.strip()]
    return syms or DEFAULT_SYMBOLS


# =========================
# Main collector
# =========================

@report_job("schwab_options_collector", job_type="Trading")
def main() -> None:
    load_dotenv()

    db_path = Path(os.environ.get("SCHWAB_DB_PATH", "data/schwab/schwab.sqlite3")).expanduser().resolve()
    out_dir = Path(os.environ.get("SCHWAB_OPTIONS_OUT_DIR", "data/schwab/options")).expanduser().resolve()

    # How far ahead to query
    days_ahead = int(os.environ.get("SCHWAB_OPTIONS_DAYS_AHEAD", "14"))
    from_d = date.today()
    to_d = from_d + timedelta(days=days_ahead)

    symbols = env_symbols()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript(SCHEMA_SQL)

    portfolio_run_id = get_portfolio_run_id(conn)

    # Create an option run record
    created_at = utc_now_iso()
    option_run_id: Optional[int] = None
    try:
        cur = conn.execute(
            """
            INSERT INTO option_runs(portfolio_run_id, created_at, status, symbols_json, from_date, to_date)
            VALUES (?, ?, 'RUNNING', ?, ?, ?)
            """,
            (portfolio_run_id, created_at, json.dumps(symbols), from_d.isoformat(), to_d.isoformat()),
        )
        option_run_id = int(cur.lastrowid)
        conn.commit()
    except Exception as e:
        conn.close()
        raise SystemExit(f"Failed to create option run: {e}")

    # Build client
    c = get_client()

    inserted_contracts = 0
    raw_files: List[str] = []

    try:
        for sym in symbols:
            # Pull chain (SINGLE strategy is typical for vanilla chains)
            # strike_count controls how many strikes around ATM; adjust if you want.
            r = c.get_option_chain(
                sym,
                strike_count=20,
                include_underlying_quote=True,
                from_date=from_d,
                to_date=to_d,
            )
            r.raise_for_status()
            chain = r.json()

            # Store raw JSON in DB + file
            fetched_at = utc_now_iso()
            conn.execute(
                """
                INSERT INTO option_chains_raw(option_run_id, symbol, fetched_at, raw_json)
                VALUES (?, ?, ?, ?)
                """,
                (option_run_id, sym, fetched_at, json.dumps(chain)),
            )

            raw_path = out_dir / f"{sym}_chain_{option_run_id}.json"
            raw_path.write_text(jdump(chain), encoding="utf-8")
            raw_files.append(str(raw_path))

            call_map = chain.get("callExpDateMap") or {}
            put_map = chain.get("putExpDateMap") or {}

            # Pick 2 nearest expirations for each side (often the same)
            call_exps = pick_nearest_expirations(call_map, n=2)
            put_exps = pick_nearest_expirations(put_map, n=2)

            # Normalize + insert
            for row in iter_contracts_from_map(sym, "CALL", call_map, call_exps):
                conn.execute(
                    """
                    INSERT INTO option_contracts(
                      option_run_id, underlying, option_type, expiration, strike,
                      contract_symbol, description,
                      bid, ask, mark, last, delta, iv, open_interest, volume, in_the_money
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        option_run_id,
                        row["underlying"],
                        row["option_type"],
                        row["expiration"],
                        row["strike"],
                        row["contract_symbol"],
                        row["description"],
                        row["bid"],
                        row["ask"],
                        row["mark"],
                        row["last"],
                        row["delta"],
                        row["iv"],
                        row["open_interest"],
                        row["volume"],
                        (1 if row["in_the_money"] is True else 0 if row["in_the_money"] is False else None),
                    ),
                )
                inserted_contracts += 1

            for row in iter_contracts_from_map(sym, "PUT", put_map, put_exps):
                conn.execute(
                    """
                    INSERT INTO option_contracts(
                      option_run_id, underlying, option_type, expiration, strike,
                      contract_symbol, description,
                      bid, ask, mark, last, delta, iv, open_interest, volume, in_the_money
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        option_run_id,
                        row["underlying"],
                        row["option_type"],
                        row["expiration"],
                        row["strike"],
                        row["contract_symbol"],
                        row["description"],
                        row["bid"],
                        row["ask"],
                        row["mark"],
                        row["last"],
                        row["delta"],
                        row["iv"],
                        row["open_interest"],
                        row["volume"],
                        (1 if row["in_the_money"] is True else 0 if row["in_the_money"] is False else None),
                    ),
                )
                inserted_contracts += 1

            conn.commit()

        # Mark run OK
        conn.execute(
            "UPDATE option_runs SET status='OK', error=NULL WHERE id=?",
            (option_run_id,),
        )
        conn.commit()

    except Exception as e:
        conn.execute(
            "UPDATE option_runs SET status='ERROR', error=? WHERE id=?",
            (str(e), option_run_id),
        )
        conn.commit()
        conn.close()
        raise

    # Write a tiny summary file you can eyeball immediately
    summary = {
        "option_run_id": option_run_id,
        "portfolio_run_id": portfolio_run_id,
        "created_at": created_at,
        "symbols": symbols,
        "from_date": from_d.isoformat(),
        "to_date": to_d.isoformat(),
        "inserted_contracts": inserted_contracts,
        "raw_files": raw_files,
    }
    (out_dir / f"summary_{option_run_id}.json").write_text(jdump(summary), encoding="utf-8")
    print(f"✅ Options chain collector OK | option_run_id={option_run_id} | symbols={symbols} | contracts={inserted_contracts}")
    print(f"📁 Raw chain files: {out_dir}")
    conn.close()


if __name__ == "__main__":
    main()
