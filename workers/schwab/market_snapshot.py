"""Mac-side Schwab market snapshot worker.

Fetches live quotes + top movers for the trading watch list and writes
a structured JSON file to NAS RawSourceLibrary so mintworker can consume
it in the morning brief without needing Schwab auth.

Output: raw_root() / "market" / "schwab_snapshot.json"

Run after the Schwab collector in the Mac cron:
  python -m workers.schwab.market_snapshot
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from workers.schwab.client import get_client

# ── Watch list ────────────────────────────────────────────────────────────────
WATCH_SYMBOLS = ["SPY", "QQQ", "NVDA", "AMD", "AAPL", "$VIX.X"]
BTC_SYMBOLS   = ["/BTCUSD", "BTCUSD", "$BTCUSD"]   # try in order, first that works

# ── Regime thresholds ─────────────────────────────────────────────────────────
VIX_HIGH      = 25.0   # above → elevated fear
VIX_EXTREME   = 35.0   # above → risk-off confirmed
SPY_RISK_OFF  = -1.5   # % change below → risk-off signal
SPY_RISK_ON   =  1.0   # % change above → risk-on signal


def _find_raw_mount() -> Path:
    """Locate RawSourceLibrary under ~/MountPoints (handles ConnectMeNow suffix)."""
    mount_root = Path.home() / "MountPoints"
    if mount_root.exists():
        for entry in mount_root.iterdir():
            if entry.name.startswith("RawSourceLibrary"):
                return entry
    # Fall back to env var or local data/
    v = os.environ.get("NAS_RAW_ROOT", "").strip()
    return Path(v).expanduser() if v else Path(__file__).resolve().parents[2] / "data" / "raw"


def _parse_quote(item: dict) -> dict:
    """Extract the fields we care about from a Schwab quote response item."""
    q = item.get("quote") or item.get("fundamental") or {}
    ref = item.get("reference") or {}

    last       = q.get("lastPrice") or q.get("last") or q.get("mark")
    net_chg    = q.get("netChange") or q.get("regularMarketNetChange")
    net_pct    = (
        q.get("netPercentChange")
        or q.get("regularMarketPercentChange")
        or q.get("netPercentChangeInDouble")
    )
    volume     = q.get("totalVolume") or q.get("regularMarketVolume")
    wk52_high  = q.get("52WeekHigh") or ref.get("52WeekHigh")
    wk52_low   = q.get("52WeekLow")  or ref.get("52WeekLow")

    return {
        "last":       round(float(last),    4) if last    is not None else None,
        "change":     round(float(net_chg), 4) if net_chg is not None else None,
        "change_pct": round(float(net_pct), 4) if net_pct is not None else None,
        "volume":     int(volume)              if volume  is not None else None,
        "52w_high":   round(float(wk52_high), 4) if wk52_high is not None else None,
        "52w_low":    round(float(wk52_low),  4) if wk52_low  is not None else None,
    }


def _compute_regime(quotes: dict) -> str:
    spy_pct = (quotes.get("SPY") or {}).get("change_pct")
    vix_lvl = (quotes.get("$VIX.X") or {}).get("last")

    if spy_pct is None and vix_lvl is None:
        return "Unknown"

    if (vix_lvl and vix_lvl >= VIX_EXTREME) or (spy_pct and spy_pct <= SPY_RISK_OFF * 2):
        return "Risk-Off"
    if vix_lvl and vix_lvl >= VIX_HIGH:
        return "Normal"   # elevated vol but not extreme
    if spy_pct and spy_pct <= SPY_RISK_OFF:
        return "Normal"
    if spy_pct and spy_pct >= SPY_RISK_ON and (vix_lvl is None or vix_lvl < VIX_HIGH):
        return "Risk-On"
    return "Calm"


def main() -> None:
    load_dotenv()
    ts = datetime.now(timezone.utc).isoformat()

    c = get_client()

    # ── 1. Fetch watch list quotes ────────────────────────────────────────────
    quotes: dict = {}
    try:
        r = c.get_quotes(WATCH_SYMBOLS)
        r.raise_for_status()
        for sym, item in r.json().items():
            quotes[sym] = _parse_quote(item)
    except Exception as exc:
        print(f"[market_snapshot] WARN: quote fetch failed: {exc}")

    # ── 2. Try BTC ────────────────────────────────────────────────────────────
    for btc_sym in BTC_SYMBOLS:
        try:
            r = c.get_quotes([btc_sym])
            if r.ok and r.json():
                item = list(r.json().values())[0]
                quotes["BTC"] = _parse_quote(item)
                print(f"[market_snapshot] BTC via {btc_sym}")
                break
        except Exception:
            continue

    # ── 3. Top movers (NASDAQ) ────────────────────────────────────────────────
    movers: dict = {"up": [], "down": [], "error": None}
    try:
        from schwab.client import Client as SchwabClient
        r = c.get_movers(
            SchwabClient.Movers.Index.COMPX,
            sort_order=SchwabClient.Movers.SortOrder.VOLUME,
            frequency=SchwabClient.Movers.Frequency.ZERO,
        )
        if r.ok:
            data = r.json()
            screeners = data if isinstance(data, list) else data.get("screeners", [])
            for m in screeners[:10]:
                sym     = m.get("symbol", "")
                pct     = m.get("percentChange") or m.get("netPercentChange") or 0
                entry   = {"symbol": sym, "change_pct": round(float(pct), 2)}
                if float(pct) >= 0:
                    movers["up"].append(entry)
                else:
                    movers["down"].append(entry)
    except Exception as exc:
        movers["error"] = str(exc)
        print(f"[market_snapshot] WARN: movers fetch failed: {exc}")

    # ── 4. Regime signal ──────────────────────────────────────────────────────
    regime = _compute_regime(quotes)

    # ── 5. Write to NAS ───────────────────────────────────────────────────────
    out_dir = _find_raw_mount() / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "schwab_snapshot.json"

    snapshot = {
        "snapshot_at": ts,
        "regime":      regime,
        "quotes":      quotes,
        "movers":      movers,
    }
    out_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print(f"[market_snapshot] OK → {out_path}")
    print(f"[market_snapshot] regime={regime}  "
          f"SPY={quotes.get('SPY', {}).get('change_pct')}%  "
          f"VIX={quotes.get('$VIX.X', {}).get('last')}")


if __name__ == "__main__":
    main()
