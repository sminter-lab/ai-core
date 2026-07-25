from __future__ import annotations

import sys

from .protocol import parse_update_board
from .store import NotionDashboardStore, load_config
from reporting.reporter import report_job


def run(text: str) -> int:
    # Normalize input safely (don't destroy single-line payloads)
    if text is None:
        text = ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Only trim outer whitespace, keep content intact
    text = text.strip()

    commands = parse_update_board(text)

    if not commands:
        preview = text[:200].replace("\n", "\\n")
        print("No UPDATE_BOARD commands found.")
        if preview:
            print(f"(debug) input preview: {preview}")
        else:
            print("(debug) input was empty after normalization")
        return 0

    store = NotionDashboardStore(load_config())

    for cmd in commands:
        print(f"🤖 TRIGGER: {cmd.category} | {cmd.metric}")
        try:
            # Support both historical method names
            if hasattr(store, "upsert"):
                action = store.upsert(cmd.category, cmd.metric, cmd.value)  # type: ignore[attr-defined]
            else:
                store.upsert_value(cmd.category, cmd.metric, cmd.value, ai_text=None)
                action = "UPDATED"

            shown = cmd.value if len(cmd.value) <= 120 else (cmd.value[:117] + "...")
            print(f"✨ {action}: {cmd.metric} → {shown}")
        except Exception as e:
            print(f"❌ ERROR {cmd.metric}: {e}")

    return 0


@report_job("notion_updater", job_type="AI Systems")
def main() -> int:
    # Priority 1: if a file path argument is provided, ALWAYS use it
    if len(sys.argv) >= 2 and sys.argv[1]:
        path = sys.argv[1]
        with open(path, "r", encoding="utf-8") as f:
            return run(f.read())

    # Priority 2: otherwise read stdin (if anything is piped)
    data = sys.stdin.read()
    return run(data)


if __name__ == "__main__":
    raise SystemExit(main())
