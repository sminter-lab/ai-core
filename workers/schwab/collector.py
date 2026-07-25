from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from workers.schwab.client import get_client
from tools.shared_store import raw_root
from reporting.reporter import report_job


# ===============================
# Configuration
# ===============================

DB_DEFAULT = "data/schwab/schwab.sqlite3"


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  status TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS account_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  ts TEXT NOT NULL,
  account_hash TEXT NOT NULL,
  account_type TEXT,
  is_day_trader INTEGER,
  round_trips INTEGER,
  raw_json TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS balances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  ts TEXT NOT NULL,
  account_hash TEXT NOT NULL,
  kind TEXT NOT NULL, -- initialBalances/currentBalances/projectedBalances
  raw_json TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  ts TEXT NOT NULL,
  account_hash TEXT NOT NULL,
  symbol TEXT,
  asset_type TEXT,
  long_short TEXT,
  quantity REAL,
  average_price REAL,
  market_value REAL,
  raw_json TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  ts TEXT NOT NULL,
  symbol TEXT NOT NULL,
  last REAL,
  bid REAL,
  ask REAL,
  mark REAL,
  volume REAL,
  quote_time INTEGER,
  raw_json TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);
"""


# ===============================
# Helpers
# ===============================

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def jdump(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def ensure_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA_SQL)
    return conn


def insert_run(conn: sqlite3.Connection, status: str, note: str | None = None) -> int:
    ts = utc_now_iso()
    cur = conn.execute(
        "INSERT INTO runs(ts,status,note) VALUES (?,?,?)",
        (ts, status, note),
    )
    return int(cur.lastrowid)


# ===============================
# Main collector
# ===============================

@report_job("schwab_collector", job_type="Trading")
def main() -> None:
    load_dotenv()

    db_path = Path(
        os.environ.get("SCHWAB_DB_PATH", DB_DEFAULT)
    ).expanduser().resolve()

    # Optional extra symbols (NOT required)
    extra = os.environ.get("SCHWAB_EXTRA_SYMBOLS", "").strip()
    symbols: set[str] = {s.strip().upper() for s in extra.split(",") if s.strip()}

    conn = ensure_db(db_path)
    run_id = insert_run(conn, "STARTED")
    ts = utc_now_iso()

    try:
        c = get_client()

        # 1) Account hash (single-account expected)
        acct_map = c.get_account_numbers().json()
        account_hash = acct_map[0]["hashValue"]

        # 2) Account snapshot with positions
        r = c.get_account(account_hash, fields=[c.Account.Fields.POSITIONS])
        r.raise_for_status()
        acct = r.json()
        sec = acct.get("securitiesAccount", {})

        conn.execute(
            """
            INSERT INTO account_snapshot
            (run_id,ts,account_hash,account_type,is_day_trader,round_trips,raw_json)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                run_id,
                ts,
                account_hash,
                sec.get("type"),
                1 if sec.get("isDayTrader") else 0,
                sec.get("roundTrips"),
                jdump(acct),
            ),
        )

        # 3) Balances
        for kind in ("initialBalances", "currentBalances", "projectedBalances"):
            if kind in sec:
                conn.execute(
                    """
                    INSERT INTO balances
                    (run_id,ts,account_hash,kind,raw_json)
                    VALUES (?,?,?,?,?)
                    """,
                    (run_id, ts, account_hash, kind, jdump(sec[kind])),
                )

        # 4) Positions + auto-derive quote symbols
        for p in sec.get("positions", []) or []:
            instr = p.get("instrument", {}) or {}
            sym = instr.get("symbol")
            if sym:
                symbols.add(sym.upper())

            conn.execute(
                """
                INSERT INTO positions
                (run_id,ts,account_hash,symbol,asset_type,long_short,
                 quantity,average_price,market_value,raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    ts,
                    account_hash,
                    instr.get("symbol"),
                    instr.get("assetType"),
                    p.get("longShort"),
                    (
                        p.get("longQuantity")
                        if p.get("longQuantity") is not None
                        else p.get("shortQuantity")
                    ),
                    p.get("averagePrice"),
                    p.get("marketValue"),
                    jdump(p),
                ),
            )

        # Normalize symbols list
        symbols = sorted(symbols)

        # 5) Quotes (auto-derived)
        if symbols:
            rq = c.get_quotes(symbols)
            rq.raise_for_status()
            quotes = rq.json()

            for sym in symbols:
                item = quotes.get(sym)
                if not item:
                    continue

                q = item.get("quote", {}) or {}
                conn.execute(
                    """
                    INSERT INTO quotes
                    (run_id,ts,symbol,last,bid,ask,mark,volume,quote_time,raw_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        run_id,
                        ts,
                        sym,
                        q.get("lastPrice"),
                        q.get("bidPrice"),
                        q.get("askPrice"),
                        q.get("mark"),
                        q.get("totalVolume"),
                        q.get("quoteTime"),
                        jdump(item),
                    ),
                )

        conn.execute("UPDATE runs SET status=? WHERE id=?", ("OK", run_id))
        conn.commit()

        # ── NAS handoff: write a JSON snapshot for studio to consume ──────────
        snapshot: dict = {
            "collected_at": ts,
            "run_id": run_id,
            "account_hash": account_hash,
            "balances": {},
            "positions": [],
            "quotes": {},
        }

        # Re-read what we just committed so the snapshot mirrors the DB exactly
        for kind in ("initialBalances", "currentBalances", "projectedBalances"):
            if kind in sec:
                snapshot["balances"][kind] = sec[kind]

        for p in sec.get("positions", []) or []:
            instr = p.get("instrument", {}) or {}
            snapshot["positions"].append({
                "symbol": instr.get("symbol"),
                "asset_type": instr.get("assetType"),
                "long_short": p.get("longShort"),
                "quantity": (
                    p.get("longQuantity")
                    if p.get("longQuantity") is not None
                    else p.get("shortQuantity")
                ),
                "average_price": p.get("averagePrice"),
                "market_value": p.get("marketValue"),
            })

        if symbols:
            rq2 = c.get_quotes(list(symbols))
            # httpx.Response has no `.ok` (that's requests) — this crashed every
            # run since 2026-04-28. Use a status-code check that works everywhere.
            if 200 <= rq2.status_code < 300:
                for sym, item in rq2.json().items():
                    q = item.get("quote", {}) or {}
                    snapshot["quotes"][sym] = {
                        "last": q.get("lastPrice"),
                        "bid": q.get("bidPrice"),
                        "ask": q.get("askPrice"),
                        "mark": q.get("mark"),
                        "volume": q.get("totalVolume"),
                    }

        snap_dir = raw_root() / "schwab"
        snap_dir.mkdir(parents=True, exist_ok=True)
        date_str = ts[:10]  # YYYY-MM-DD
        snap_path = snap_dir / f"snapshot_{date_str}.json"
        snap_path.write_text(jdump(snapshot), encoding="utf-8")
        # Keep a stable "latest" pointer for studio
        (snap_dir / "snapshot_latest.json").write_text(jdump(snapshot), encoding="utf-8")

        print(f"✅ Schwab collector OK | run_id={run_id} | symbols={symbols}")
        print(f"📤 Snapshot → {snap_path}")

    except Exception as e:
        conn.execute(
            "UPDATE runs SET status=?, note=? WHERE id=?",
            ("ERROR", str(e), run_id),
        )
        conn.commit()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()
