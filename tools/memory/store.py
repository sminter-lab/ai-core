from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Default: repo-root/memory (works everywhere)
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "memory"

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _slug(s: str) -> str:
    # safe filename chunk
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in s)[:80].strip("_") or "item"

def _root() -> Path:
    """
    Interop rule:
    - If AI_CORE_MEMORY_ROOT is set, write there (server/Synology ready).
    - Else default to repo_root/memory.
    """
    env = (Path.home() / ".none")
    val = None
    try:
        import os
        val = os.environ.get("AI_CORE_MEMORY_ROOT")
    except Exception:
        val = None
    return Path(val).expanduser() if val else DEFAULT_ROOT

def _write(kind: str, payload: Dict[str, Any], name: Optional[str] = None) -> Path:
    root = _root()
    out_dir = root / kind
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    filename = ts
    if name:
        filename += f"_{_slug(name)}"
    filename += ".json"

    record = {
        "kind": kind,
        "timestamp": _now_iso(),
        "payload": payload,
    }

    path = out_dir / filename
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path

def write_daily(summary: str, sources: Optional[list[str]] = None, name: str = "daily") -> Path:
    return _write("daily", {"summary": summary, "sources": sources or []}, name=name)

def write_decision(title: str, rationale: str, consequences: Optional[list[str]] = None, name: str = "decision") -> Path:
    return _write("decisions", {"title": title, "rationale": rationale, "consequences": consequences or []}, name=name)

def write_fact(key: str, value: Any, confidence: float = 1.0) -> Path:
    return _write("facts", {"key": key, "value": value, "confidence": confidence}, name=key)

def write_run(run_id: str, status: str, notes: Optional[str] = None) -> Path:
    return _write("runs", {"run_id": run_id, "status": status, "notes": notes}, name=run_id)
