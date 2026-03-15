"""mintworker — online RSS fetch entry point.

Sets AI_VAULT_ROOT to the shared NAS RawSourceLibrary before delegating to the
real collector so all raw feeds land on the NAS where studio can read them.

Usage:
    JOB_SPEC_PATH=feeds.json python -m workers.rss.fetch
"""

from __future__ import annotations

import os

from tools.shared_store import raw_root


def main() -> None:
    # Point the RSS collector at the shared NAS raw store.
    # NAS_RAW_ROOT takes precedence; AI_VAULT_ROOT is honoured for
    # backwards-compatibility if already set explicitly.
    if not os.environ.get("AI_VAULT_ROOT"):
        os.environ["AI_VAULT_ROOT"] = str(raw_root())

    from jobs.web_rss_collector.app.collector import main as _rss_main
    _rss_main()


if __name__ == "__main__":
    main()
