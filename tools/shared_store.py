"""Shared NAS store — path helpers for the mintworker / studio split.

Role layout
-----------
mintworker  (online tasks)   →  writes raw fetched data  →  raw_root()  / RawSourceLibrary
studio      (processing)     →  reads raw, writes output  →  docs_root() / Documents

Environment variables
---------------------
  NAS_RAW_ROOT    Mount path of the RawSourceLibrary share   (mintworker writes here)
  NAS_DOCS_ROOT   Mount path of the Documents share          (studio writes here)

Falls back to local data/raw and data/docs so everything works without a NAS mount.
"""

from __future__ import annotations

import os
from pathlib import Path

_BASE = Path(__file__).resolve().parents[1]


def raw_root() -> Path:
    """RawSourceLibrary — mintworker deposits fetched data here."""
    v = os.environ.get("NAS_RAW_ROOT", "").strip()
    return Path(v).expanduser() if v else _BASE / "data" / "raw"


def docs_root() -> Path:
    """Documents — studio writes processed / analysed outputs here."""
    v = os.environ.get("NAS_DOCS_ROOT", "").strip()
    return Path(v).expanduser() if v else _BASE / "data" / "docs"
