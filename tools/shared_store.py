"""Shared NAS store — path helpers for the mintworker / studio split.

Role layout
-----------
mintworker  (online tasks)   →  writes raw fetched data  →  raw_root()  / RawSourceLibrary
studio      (processing)     →  reads raw, writes output  →  docs_root() / Documents

Environment variables
---------------------
  NAS_RAW_ROOT    Mount path of the RawSourceLibrary share   (mintworker writes here)
  NAS_DOCS_ROOT   Mount path of the Documents share          (studio writes here)

Discovery order for each path
------------------------------
  1. Env var (NAS_RAW_ROOT / NAS_DOCS_ROOT) — if set and exists on disk
  2. ~/MountPoints/<ShareName>*  — ConnectMeNow mounts (Mac, suffix varies)
  3. /Volumes/<ShareName>        — standard macOS / Linux SMB mount
  4. local data/raw or data/docs — offline fallback

Falls back to local data/ so everything works without a NAS mount.
"""

from __future__ import annotations

import os
from pathlib import Path

_BASE = Path(__file__).resolve().parents[1]


def _find_share(share_name: str, env_var: str, local_fallback: str) -> Path:
    """Locate a NAS share using the discovery order above."""

    # 1. Explicit env var — honour it if the path actually exists
    v = os.environ.get(env_var, "").strip()
    if v:
        p = Path(v).expanduser()
        if p.exists():
            return p
        # Env var set but path missing — fall through to auto-discovery
        # (don't error: the NAS may just not be mounted right now)

    # 2. ConnectMeNow style: ~/MountPoints/<ShareName>*
    mount_root = Path.home() / "MountPoints"
    if mount_root.exists():
        for entry in sorted(mount_root.iterdir()):
            if entry.name.startswith(share_name):
                return entry

    # 3. Standard /Volumes/<ShareName>
    vol = Path("/Volumes") / share_name
    if vol.exists():
        return vol

    # 4. Local fallback
    return _BASE / local_fallback


def raw_root() -> Path:
    """RawSourceLibrary — mintworker deposits fetched data here."""
    return _find_share("RawSourceLibrary", "NAS_RAW_ROOT", "data/raw")


def docs_root() -> Path:
    """Documents — studio writes processed / analysed outputs here."""
    return _find_share("Documents", "NAS_DOCS_ROOT", "data/docs")
